"""正向 WebSocket：作为 WS 服务端，接收应用连接。

- 握手时校验 access_token（Authorization: Bearer 或 query 参数）
- 连接建立后发送生命周期 connect 事件
- 客户端发送 {"action":..., "params":..., "echo":...}，服务端回带 echo 的响应
- 事件实时推送给所有已连接客户端
- 配置 tls_cert/tls_key 即启用 WSS
"""

from __future__ import annotations

import ssl
from typing import Any

from aiohttp import web

from ..actions import ActionHandler
from ...config import ConnectionConfig
from .base import BaseComm


class WsServerComm(BaseComm):
    def __init__(self, cfg: ConnectionConfig, handler: ActionHandler,
                 self_id: str):
        super().__init__(f"ws_server:{cfg.get('port')}", cfg.protocol,
                         handler, self_id)
        self.cfg = cfg
        self._runner: web.AppRunner | None = None
        self._clients: set[web.WebSocketResponse] = set()

    def _check_token(self, request: web.Request) -> bool:
        token = self.cfg.get("access_token") or ""
        if not token:
            return True
        auth = request.headers.get("Authorization", "")
        return (auth == f"Bearer {token}"
                or request.query.get("access_token") == token)

    async def _ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        if not self._check_token(request):
            raise web.HTTPUnauthorized(text="bad access_token")
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._clients.add(ws)
        self.log.info("正向 WS 客户端接入，当前 %d 个", len(self._clients))
        await ws.send_json(self.lifecycle_event())
        try:
            async for msg in ws:
                if msg.type != web.WSMsgType.TEXT:
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
        finally:
            self._clients.discard(ws)
            self.log.info("正向 WS 客户端断开，剩余 %d 个", len(self._clients))
        return ws

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/", self._ws_handler)
        app.router.add_get("/onebot/v11/ws", self._ws_handler)
        app.router.add_get("/onebot/v12/ws", self._ws_handler)
        self._runner = web.AppRunner(app)
        await self._runner.setup()

        ssl_ctx = None
        cert, key = self.cfg.get("tls_cert"), self.cfg.get("tls_key")
        if cert and key:
            ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            ssl_ctx.load_cert_chain(cert, key)

        site = web.TCPSite(self._runner, self.cfg.get("host", "0.0.0.0"),
                           int(self.cfg.get("port", 6700)), ssl_context=ssl_ctx)
        await site.start()
        scheme = "wss" if ssl_ctx else "ws"
        self.log.info("正向 WS 已启动: %s://%s:%s (OneBot %d)",
                      scheme, self.cfg.get("host", "0.0.0.0"),
                      self.cfg.get("port", 6700), self.protocol)

    async def stop(self) -> None:
        for ws in list(self._clients):
            await ws.close()
        if self._runner:
            await self._runner.cleanup()

    async def push_event(self, event: dict[str, Any]) -> None:
        dead = []
        for ws in list(self._clients):
            try:
                await ws.send_json(event)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)
