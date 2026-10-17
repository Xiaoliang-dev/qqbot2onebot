"""QQ 网关 WebSocket 客户端（支持多切片、断连补发 Resume）。

流程:
  连接 -> OP10 Hello -> OP2 Identify -> OP0 READY(获得 session_id)
  心跳 OP1 / 回执 OP11
  OP7 Reconnect 或异常断连 -> 重连后发送 OP6 Resume {token, session_id, seq}
  服务端补发断连期间事件，补发完毕下发 RESUMED 事件。

补发转发策略（配置 qqbot.resume.forward_replayed_events）:
  true  (默认): 补发事件照常转发给 OneBot。
  false:        补发事件只接收、照常记录 s 序号，不转发；
                收到 RESUMED 后恢复转发。
"""

# by:xiaoliang - 2026.10.17

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

import aiohttp

from .api import QQAPI
from .errors import QQ_ERROR_CODES, QQWSCloseError, log_qq_error

log = logging.getLogger("q2o.qqbot.ws")

OP_DISPATCH = 0
OP_HEARTBEAT = 1
OP_IDENTIFY = 2
OP_RESUME = 6
OP_RECONNECT = 7
OP_INVALID_SESSION = 9
OP_HELLO = 10
OP_HEARTBEAT_ACK = 11

EventHandler = Callable[[str, dict[str, Any], str], Awaitable[None]]


class QQGatewayWS:
    def __init__(self, api: QQAPI, wss_url: str, intents: int,
                 shard: tuple[int, int], on_event: EventHandler,
                 forward_replayed: bool = True,
                 error_min_level: str = "warn"):
        self.api = api
        self.wss_url = wss_url
        self.intents = intents
        self.shard = shard
        self.on_event = on_event
        self.forward_replayed = forward_replayed
        self.error_min_level = error_min_level

        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._seq: int | None = None
        self._session_id: str | None = None
        self._heartbeat_interval = 40.0
        self._heartbeat_task: asyncio.Task | None = None
        self._hb_ack_received = True
        self._resuming = False
        self._stopping = False

    async def run(self) -> None:
        """阻塞运行，内部处理所有重连逻辑。"""
        first = True
        while not self._stopping:
            try:
                if first:
                    await self._connect_and_identify()
                    first = False
                else:
                    await self._connect_and_resume()
                await self._recv_loop()
            except QQWSCloseError as e:
                log_qq_error(log, e, self.error_min_level,
                             f"shard {self.shard[0]}/{self.shard[1]}")
                if not e.resumeable:
                    self._session_id = None
                    self._seq = None
                    self._resuming = False
                    first = True
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                log.warning("shard %d 连接异常: %s，尝试 Resume 重连",
                            self.shard[0], e)
            finally:
                await self._teardown()
            if not self._stopping:
                await asyncio.sleep(2)

    async def stop(self) -> None:
        self._stopping = True
        await self._teardown()

    async def _open(self) -> None:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=0))
        self._ws = await self._session.ws_connect(
            self.wss_url, heartbeat=None, autoping=True)
        log.info("shard %d/%d 已连接 QQ 网关", self.shard[0], self.shard[1])

    async def _connect_and_identify(self) -> None:
        await self._open()
        hello = await self._expect_op(OP_HELLO)
        self._heartbeat_interval = hello["d"]["heartbeat_interval"] / 1000
        self._start_heartbeat()
        token = await self.api.access_token()
        await self._send(OP_IDENTIFY, {
            "token": f"QQBot {token}",
            "intents": self.intents,
            "shard": [self.shard[0], self.shard[1]],
            "properties": {},
        })
        log.info("shard %d/%d 已发送 Identify", self.shard[0], self.shard[1])

    async def _connect_and_resume(self) -> None:
        """断线重连：发送 OP6 请求补发，随后进入补发阶段直到 RESUMED。"""
        await self._open()
        hello = await self._expect_op(OP_HELLO)
        self._heartbeat_interval = hello["d"]["heartbeat_interval"] / 1000
        self._start_heartbeat()
        if self._session_id and self._seq is not None:
            token = await self.api.access_token()
            await self._send(OP_RESUME, {
                "token": f"QQBot {token}",
                "session_id": self._session_id,
                "seq": self._seq,
            })
            self._resuming = True
            log.info("shard %d/%d 已发送 Resume(seq=%d)，等待补发结束(RESUMED)",
                     self.shard[0], self.shard[1], self._seq)
        else:
            await self._teardown()
            await self._connect_and_identify()

    async def _expect_op(self, op: int) -> dict[str, Any]:
        assert self._ws is not None
        msg = await self._ws.receive(timeout=30)
        if msg.type != aiohttp.WSMsgType.TEXT:
            raise aiohttp.ClientError(f"等待 op={op} 时收到非文本帧: {msg.type}")
        payload = msg.json()
        if payload.get("op") != op:
            raise aiohttp.ClientError(f"期望 op={op}，收到 {payload.get('op')}")
        return payload

    async def _recv_loop(self) -> None:
        assert self._ws is not None
        async for msg in self._ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                await self._handle(msg.json())
            elif msg.type == aiohttp.WSMsgType.CLOSE:
                code = self._ws.close_code or 4009
                desc = QQ_ERROR_CODES.get(code, ("未知关闭码", "major"))[0]
                raise QQWSCloseError(code, desc)
            elif msg.type in (aiohttp.WSMsgType.CLOSING,
                              aiohttp.WSMsgType.CLOSED,
                              aiohttp.WSMsgType.ERROR):
                raise aiohttp.ClientError(f"WS 关闭/出错: {msg.type}")

    async def _handle(self, payload: dict[str, Any]) -> None:
        op = payload.get("op")
        if op == OP_DISPATCH:
            s = payload.get("s")
            if s is not None:
                self._seq = s
            t = payload.get("t", "")
            d = payload.get("d") or {}
            log.debug("shard %d/%d 收到 QQ 事件: type=%s event_id=%s data=%s",
                      self.shard[0], self.shard[1], t,
                      payload.get("id", ""), d)
            if t == "READY":
                self._session_id = d.get("session_id")
                self._resuming = False
                log.info("shard %d/%d READY, session_id=%s",
                         self.shard[0], self.shard[1], self._session_id)
                return
            if t == "RESUMED":
                self._resuming = False
                log.info("shard %d/%d 补发结束(RESUMED)，恢复正常转发",
                         self.shard[0], self.shard[1])
                return
            if self._resuming and not self.forward_replayed:
                log.debug("shard %d 补发事件 %s 已接收未转发 (s=%s)",
                          self.shard[0], t, s)
                return
            await self.on_event(t, d, str(payload.get("id") or ""))

        elif op == OP_HEARTBEAT_ACK:
            self._hb_ack_received = True
        elif op == OP_RECONNECT:
            raise QQWSCloseError(4007, "服务端要求重连(Reconnect)")
        elif op == OP_INVALID_SESSION:
            raise QQWSCloseError(4009, "会话无效(Invalid Session)")
        elif op == OP_HEARTBEAT:
            await self._send(OP_HEARTBEAT, self._seq)

    def _start_heartbeat(self) -> None:
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        self._hb_ack_received = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._heartbeat_interval)
                if not self._hb_ack_received:
                    log.warning("shard %d 心跳无回执，触发重连", self.shard[0])
                    raise QQWSCloseError(4009, "心跳超时")
                self._hb_ack_received = False
                await self._send(OP_HEARTBEAT, self._seq)
        except asyncio.CancelledError:
            pass

    async def _send(self, op: int, d: Any) -> None:
        assert self._ws is not None
        await self._ws.send_json({"op": op, "d": d})

    async def _teardown(self) -> None:
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
        if self._ws is not None and not self._ws.closed:
            try:
                await self._ws.close()
            except Exception:
                pass
        self._ws = None
