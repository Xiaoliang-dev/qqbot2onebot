"""反向 WebSocket：作为 WS 客户端主动连接 OneBot 应用的 WS 服务端。

- 携带 OneBot 标准请求头（Authorization / X-Self-ID / X-Client-Role）
- 断线后按 reconnect_interval 自动重连
- 支持 ws:// 与 wss://
"""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from ..actions import ActionHandler
from ...config import ConnectionConfig
from .base import BaseComm


class WsReverseComm(BaseComm):
    def __init__(self, cfg: ConnectionConfig, handler: ActionHandler,
                 self_id: str):
        super().__init__(f"ws_reverse:{cfg.get('url')}", cfg.protocol,
                         handler, self_id)
        self.cfg = cfg
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._task: asyncio.Task | None = None
        self._stopping = False

    async def start(self) -> None:
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=0))
        self._task = asyncio.create_task(self._run())
        self.log.info("反向 WS 已启动，目标 %s (OneBot %d)",
                      self.cfg.get("url"), self.protocol)

    async def _run(self) -> None:
        interval = int(self.cfg.get("reconnect_interval", 3))
        while not self._stopping:
            try:
                headers = {
                    "X-Self-ID": str(self.self_id),
                    "X-Client-Role": "Universal",
                    "User-Agent": f"OneBot/{self.protocol} QQBot2OneBot",
                }
                token = self.cfg.get("access_token") or ""
                if token:
                    headers["Authorization"] = f"Bearer {token}"
                assert self._session is not None
                async with self._session.ws_connect(
                        self.cfg.get("url"), headers=headers,
                        heartbeat=30) as ws:
                    self._ws = ws
                    self.log.info("反向 WS 已连接 %s", self.cfg.get("url"))
                    await ws.send_json(self.lifecycle_event())
                    async for msg in ws:
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        try:
                            req = msg.json()
                        except Exception:
                            continue
                        result = await self.handler.dispatch(
                            str(req.get("action", "")), req.get("params") or {},
                            self.protocol)
                        if req.get("echo") is not None:
                            result["echo"] = req["echo"]
                        await ws.send_json(result)
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                if not self._stopping:
                    self.log.warning("反向 WS 连接失败/断开: %s，%d 秒后重连",
                                     e, interval)
            finally:
                self._ws = None
            if not self._stopping:
                await asyncio.sleep(interval)

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        if self._session and not self._session.closed:
            await self._session.close()

    async def push_event(self, event: dict[str, Any]) -> None:
        ws = self._ws
        if ws is None or ws.closed:
            return
        try:
            await ws.send_json(event)
        except Exception as e:
            self.log.debug("反向 WS 事件推送失败: %s", e)
