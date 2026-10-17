"""应用编排：装配 QQ 接入、事件转换、动作处理与全部 OneBot 连接。"""

# by:xiaoliang - 2026.10.17

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .config import Config
from .onebot.actions import ActionHandler
from .onebot.comms.base import BaseComm
from .onebot.comms.http_post import HttpPostComm
from .onebot.comms.http_server import HttpServerComm
from .onebot.comms.ws_reverse import WsReverseComm
from .onebot.comms.ws_server import WsServerComm
from .onebot.convert import EventConverter
from .qqbot.api import QQAPI
from .qqbot.intents import compute_intents
from .qqbot.sharding import decide_shard_count
from .qqbot.webhook import QQWebhookServer
from .qqbot.ws import QQGatewayWS
from .utils.context import JoinRequestContext, MessageContext, MessageStore
from .utils.idmap import IdMapper

log = logging.getLogger("q2o.app")

COMM_TYPES = {
    "http_server": HttpServerComm,
    "http_post": HttpPostComm,
    "ws_server": WsServerComm,
    "ws_reverse": WsReverseComm,
}


class Application:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.idmap = IdMapper(translate=cfg.onebot.openid_translate)
        self.ctx = MessageContext()
        self.join_ctx = JoinRequestContext()
        self.store = MessageStore()
        self.api = QQAPI(cfg.qqbot.app_id, cfg.qqbot.client_secret,
                         sandbox=cfg.qqbot.sandbox,
                         timeout=cfg.qqbot.http_timeout,
                         error_min_level=cfg.logging.qq_error_min_level)
        self.converter = EventConverter(self.idmap, self.ctx,
                                        cfg.onebot.self_id,
                                        q2oex_enable=cfg.q2oex_enable,
                                        join_ctx=self.join_ctx,
                                        store=self.store)
        self.handler = ActionHandler(
            self.api, self.idmap, self.ctx,
            cfg.onebot.self_id, cfg.qqbot.app_id,
            join_ctx=self.join_ctx,
            store=self.store,
            q2oex_enable=cfg.q2oex_enable,
            passive_by_default=cfg.qqbot.message.passive_by_default)
        self.comms: list[BaseComm] = []
        self._qq_tasks: list[asyncio.Task] = []
        self._webhook: QQWebhookServer | None = None

    async def _on_qq_event(self, qq_type: str, data: dict[str, Any],
                           event_id: str = "") -> None:
        converted: dict[int, dict | None] = {}
        for proto in {c.protocol for c in self.comms}:
            try:
                converted[proto] = self.converter.convert(
                    qq_type, data, proto, event_id=event_id)
            except Exception:
                log.exception("事件 %s 转换为 OneBot %d 失败", qq_type, proto)
                converted[proto] = None
        pushed = 0
        for comm in self.comms:
            event = converted.get(comm.protocol)
            if event is None:
                continue
            try:
                await comm.push_event(event)
                pushed += 1
            except Exception:
                log.exception("向连接 %s 推送事件失败", comm.name)
        if pushed:
            log.debug("事件 %s 已推送到 %d 条连接", qq_type, pushed)

    async def _start_qq_websocket(self) -> None:
        intents = compute_intents(self.cfg.qqbot.events)
        log.info("intents = %d (events=%s)", intents,
                 ",".join(self.cfg.qqbot.events))

        gw = await self.api.gateway_bot()
        wss_url = gw["url"]
        suggested = int(gw.get("shards", 1))
        limit = gw.get("session_start_limit") or {}
        remaining = int(limit.get("remaining", 1))
        log.info("gateway: wss=%s 建议切片=%d 会话剩余=%d/%d 每24h重置",
                 wss_url, suggested, remaining, int(limit.get("total", 0)))
        if remaining <= 0:
            log.error("会话启动次数已用尽，请等待重置后再启动")
            raise SystemExit(2)

        shard_count = decide_shard_count(
            suggested, self.cfg.qqbot.sharding.enable,
            self.cfg.qqbot.sharding.count)
        if shard_count > remaining:
            log.warning("切片数 %d 超出剩余会话数 %d，裁剪为 %d",
                        shard_count, remaining, remaining)
            shard_count = remaining

        forward = self.cfg.qqbot.resume.forward_replayed_events
        for shard_id in range(shard_count):
            ws = QQGatewayWS(
                self.api, wss_url, intents,
                shard=(shard_id, shard_count),
                on_event=self._on_qq_event,
                forward_replayed=forward,
                error_min_level=self.cfg.logging.qq_error_min_level)
            self._qq_tasks.append(asyncio.create_task(ws.run()))
            await asyncio.sleep(1)
        log.info("已启动 %d 个 QQ 网关切片", shard_count)

    async def _start_qq_webhook(self) -> None:
        wc = self.cfg.qqbot.webhook
        self._webhook = QQWebhookServer(
            self.cfg.qqbot.client_secret, wc.host, wc.port, wc.path,
            self._on_qq_event)
        await self._webhook.start()
        intents = compute_intents(self.cfg.qqbot.events)
        log.info("webhook 模式，声明 intents = %d", intents)

    async def _start_comms(self) -> None:
        for conn_cfg in self.cfg.onebot.connections:
            if not conn_cfg.enable:
                continue
            cls = COMM_TYPES.get(conn_cfg.type)
            if cls is None:
                log.error("未知连接类型 %s，已跳过", conn_cfg.type)
                continue
            comm = cls(conn_cfg, self.handler, self.cfg.onebot.self_id)
            await comm.start()
            self.comms.append(comm)
        if not self.comms:
            log.warning("没有启用任何 OneBot 连接，事件将无处推送")

    async def start(self) -> None:
        await self._start_comms()
        if self.cfg.qqbot.ingress == "webhook":
            await self._start_qq_webhook()
        else:
            await self._start_qq_websocket()
        log.info("qqbot2onebot 启动完成")

    async def stop(self) -> None:
        for t in self._qq_tasks:
            t.cancel()
        for comm in self.comms:
            try:
                await comm.stop()
            except Exception:
                log.exception("停止连接 %s 出错", comm.name)
        if self._webhook:
            await self._webhook.stop()
        await self.api.close()

    async def run(self) -> None:
        await self.start()
        try:
            await asyncio.Event().wait()
        finally:
            await self.stop()
