"""正向 HTTP(S)：作为 HTTP 服务端接收 OneBot 应用的 API 调用。

支持两种调用形式:
  POST /            {"action": "send_msg", "params": {...}, "echo": ...}
  POST /send_msg    {...params, "echo": ...}
配置 tls_cert/tls_key 即启用 HTTPS。
"""

from __future__ import annotations

import ssl
from typing import Any

from aiohttp import web

from ..actions import ActionHandler
from ...config import ConnectionConfig
from .base import BaseComm


class HttpServerComm(BaseComm):
    def __init__(self, cfg: ConnectionConfig, handler: ActionHandler,
                 self_id: str):
        super().__init__(f"http_server:{cfg.get('port')}", cfg.protocol,
                         handler, self_id)
        self.cfg = cfg
        self._runner: web.AppRunner | None = None

    def _check_token(self, request: web.Request) -> bool:
        token = self.cfg.get("access_token") or ""
        if not token:
            return True
        auth = request.headers.get("Authorization", "")
        query_token = request.query.get("access_token", "")
        return auth == f"Bearer {token}" or query_token == token

    async def _handle(self, request: web.Request) -> web.Response:
        if not self._check_token(request):
            return web.json_response(
                {"status": "failed", "retcode": 1401, "data": None,
                 "message": "access_token 校验失败", "wording": "鉴权失败"},
                status=401)
        try:
            body: dict[str, Any] = await request.json()
        except Exception:
            return web.json_response(
                {"status": "failed", "retcode": 1400, "data": None,
                 "message": "请求体不是合法 JSON", "wording": "请求格式错误"},
                status=400)

        if request.path != "/" and request.path.count("/") == 1:
            action = request.path.lstrip("/")
            params = {k: v for k, v in body.items() if k != "echo"}
        else:
            action = body.get("action", "")
            params = body.get("params") or {}
        echo = body.get("echo")

        result = await self.handler.dispatch(action, params, self.protocol)
        if echo is not None:
            result["echo"] = echo
        return web.json_response(result)

    async def start(self) -> None:
        app = web.Application()
        app.router.add_post("/", self._handle)
        app.router.add_post("/{action}", self._handle)
        self._runner = web.AppRunner(app)
        await self._runner.setup()

        ssl_ctx = None
        cert, key = self.cfg.get("tls_cert"), self.cfg.get("tls_key")
        if cert and key:
            ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
            ssl_ctx.load_cert_chain(cert, key)

        site = web.TCPSite(self._runner, self.cfg.get("host", "0.0.0.0"),
                           int(self.cfg.get("port", 5700)), ssl_context=ssl_ctx)
        await site.start()
        scheme = "https" if ssl_ctx else "http"
        self.log.info("正向 %s 已启动: %s://%s:%s (OneBot %d)",
                      scheme.upper(), scheme, self.cfg.get("host", "0.0.0.0"),
                      self.cfg.get("port", 5700), self.protocol)

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()

    async def push_event(self, event: dict) -> None:
        pass  # 正向 HTTP 仅提供 API 调用，不推送事件
