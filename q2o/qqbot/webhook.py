"""QQ 开放平台 Webhook 接收端（Ed25519 验签 + 回调验证）。

- 使用 client_secret 派生 Ed25519 密钥（seed 为 secret 重复至 32 字节）
- 处理 op=13 回调地址验证：返回 {plain_token, signature}
- 处理 op=0 事件推送：验签后交给事件分发器，立即回 200
"""

# by:xiaoliang - 2026.10.17

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from aiohttp import web
from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey

log = logging.getLogger("q2o.qqbot.webhook")

EventHandler = Callable[[str, dict[str, Any], str], Awaitable[None]]


def _seed_from_secret(secret: str) -> bytes:
    while len(secret) < 32:
        secret += secret
    return secret[:32].encode("utf-8")


class QQWebhookServer:
    def __init__(self, client_secret: str, host: str, port: int, path: str,
                 on_event: EventHandler):
        self.host = host
        self.port = port
        self.path = path
        self.on_event = on_event
        self._signing_key = SigningKey(_seed_from_secret(client_secret))
        self._verify_key = self._signing_key.verify_key
        self._runner: web.AppRunner | None = None

    def _verify(self, timestamp: str, body: bytes, signature: str) -> bool:
        try:
            msg = timestamp.encode() + body
            self._verify_key.verify(msg, bytes.fromhex(signature))
            return True
        except (BadSignatureError, ValueError):
            return False

    async def _handle(self, request: web.Request) -> web.Response:
        body = await request.read()
        try:
            payload: dict[str, Any] = await request.json()
        except Exception:
            return web.json_response({"error": "bad json"}, status=400)

        op = payload.get("op")
        d = payload.get("d") or {}

        if op == 13:
            plain_token = d.get("plain_token", "")
            event_ts = d.get("event_ts", "")
            signature = self._signing_key.sign(
                (event_ts + plain_token).encode()).signature.hex()
            log.info("QQ Webhook 回调地址验证通过")
            return web.json_response({
                "plain_token": plain_token,
                "signature": signature,
            })

        if op == 0:
            ts = request.headers.get("X-Signature-Timestamp", "")
            sig = request.headers.get("X-Signature-Ed25519", "")
            if not self._verify(ts, body, sig):
                log.warning("QQ Webhook 验签失败，已拒绝该回调")
                return web.json_response({"error": "bad signature"}, status=401)
            t = payload.get("t", "")
            log.debug("收到 QQ Webhook 事件: type=%s event_id=%s data=%s",
                      t, payload.get("id", ""), d)
            import asyncio
            asyncio.create_task(self.on_event(t, d, str(payload.get("id") or "")))
            return web.json_response({})

        return web.json_response({})

    async def start(self) -> None:
        app = web.Application()
        app.router.add_post(self.path, self._handle)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        log.info("QQ Webhook 监听 http://%s:%d%s", self.host, self.port, self.path)

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
