"""反向 HTTP(S) Webhook：把事件 POST 到 OneBot 应用指定的 URL。

- 支持 http:// 与 https:// 目标
- 配置 secret 时附带 X-Signature: sha1=<HMAC-SHA1(secret, body)>
- 应用响应中的动作(action)会被执行并把结果回传（OneBot 11 快速操作语义）
"""

# by:xiaoliang - 2026.10.17

from __future__ import annotations

import hashlib
import hmac
from typing import Any

import aiohttp

from ..actions import ActionHandler
from ...config import ConnectionConfig
from .base import BaseComm


class HttpPostComm(BaseComm):
    def __init__(self, cfg: ConnectionConfig, handler: ActionHandler,
                 self_id: str):
        super().__init__(f"http_post:{cfg.get('url')}", cfg.protocol,
                         handler, self_id)
        self.cfg = cfg
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        timeout = aiohttp.ClientTimeout(total=int(self.cfg.get("timeout", 5)))
        self._session = aiohttp.ClientSession(timeout=timeout)
        self.log.info("反向 HTTP 上报已就绪: %s (OneBot %d)",
                      self.cfg.get("url"), self.protocol)

    async def stop(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def push_event(self, event: dict[str, Any]) -> None:
        assert self._session is not None
        body = aiohttp.payload.JsonPayload(event).dumps()  # type: ignore[attr-defined]
        headers = {
            "Content-Type": "application/json",
            "X-Self-ID": str(self.self_id),
            "User-Agent": "OneBot/11 QQBot2OneBot" if self.protocol == 11
            else "OneBot/12 QQBot2OneBot",
        }
        secret = self.cfg.get("secret") or ""
        if secret:
            sig = hmac.new(secret.encode(), body, hashlib.sha1).hexdigest()
            headers["X-Signature"] = f"sha1={sig}"
        try:
            async with self._session.post(self.cfg.get("url"), data=body,
                                          headers=headers) as resp:
                if resp.status >= 400:
                    self.log.warning("反向 HTTP 上报返回 %d", resp.status)
                    return
                if resp.content_type == "application/json":
                    reply = await resp.json()
                    await self._handle_quick_action(reply)
        except (aiohttp.ClientError, TimeoutError) as e:
            self.log.warning("反向 HTTP 上报失败: %s", e)

    async def _handle_quick_action(self, reply: dict) -> None:
        if not isinstance(reply, dict):
            return
        action = reply.get("action")
        if action:
            params = reply.get("params") or {}
            await self.handler.dispatch(str(action), params, self.protocol)
