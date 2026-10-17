"""QQ 开放平台 REST API 客户端（API v2）。

- 自动维护 app access token（client_credentials 模式，过期前刷新）
- GET /gateway/bot 获取 wss 接入点、建议切片数与会话限制
- 群/C2C/频道消息发送：支持主动消息、msg_id/event_id 被动回复、Markdown、按钮键盘、富媒体
- 互动事件回应：PUT /interactions/{interaction_id}
- 业务错误码统一抛 QQAPIError，由上层映射 OneBot retcode 并按级别记日志
"""

# by:xiaoliang - 2026.10.17

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

import aiohttp

from .errors import QQAPIError, log_qq_error

log = logging.getLogger("q2o.qqbot.api")

API_BASE = "https://api.sgroup.qq.com"
API_BASE_SANDBOX = "https://sandbox.api.sgroup.qq.com"
TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"


def _build_message_body(*, msg_type: int | None = None,
                        content: str | None = None,
                        markdown: dict[str, Any] | None = None,
                        keyboard: dict[str, Any] | None = None,
                        media: dict[str, Any] | None = None,
                        message_reference: dict[str, Any] | None = None,
                        msg_id: str | None = None,
                        event_id: str | None = None,
                        msg_seq: int | None = None,
                        is_wakeup: bool | None = None,
                        input_notify: dict[str, Any] | None = None,
                        stream: bool | None = None,
                        extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """按 QQ 官方 v2 现行字段构造群/C2C消息请求体。

    msg_id/event_id/is_wakeup 三个被动/召回标识互斥；不传任一标识即主动消息。
    """
    if msg_id and event_id:
        raise ValueError("msg_id 与 event_id 只能二选一")
    if (msg_id or event_id) and is_wakeup:
        raise ValueError("is_wakeup 与 msg_id/event_id 互斥")
    if markdown is not None and content not in (None, ""):
        raise ValueError("发送 Markdown 时 content 必须为空")

    if msg_type is None:
        if media is not None:
            msg_type = 7
        elif markdown is not None:
            msg_type = 2
        elif input_notify is not None:
            msg_type = 6
        else:
            msg_type = 0

    body: dict[str, Any] = {"msg_type": msg_type}
    if content not in (None, ""):
        body["content"] = content
    if markdown is not None:
        body["markdown"] = markdown
    if keyboard is not None:
        body["keyboard"] = keyboard
    if media is not None:
        body["media"] = media
    if message_reference is not None:
        body["message_reference"] = message_reference
    if input_notify is not None:
        body["input_notify"] = input_notify
    if stream is not None:
        body["stream"] = stream

    if msg_id:
        body["msg_id"] = msg_id
    elif event_id:
        body["event_id"] = event_id
    if (msg_id or event_id) and msg_seq is not None:
        body["msg_seq"] = msg_seq
    if is_wakeup is not None:
        body["is_wakeup"] = is_wakeup
    if extra:
        body.update(extra)
    return body


class QQAPI:
    def __init__(self, app_id: str, client_secret: str, *,
                 sandbox: bool = False, timeout: int = 15,
                 error_min_level: str = "warn"):
        self.app_id = app_id
        self.client_secret = client_secret
        self.base = API_BASE_SANDBOX if sandbox else API_BASE
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.error_min_level = error_min_level

        self._session: Optional[aiohttp.ClientSession] = None
        self._token: str = ""
        self._token_expire_at: float = 0.0
        self._token_lock = asyncio.Lock()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
        return self._session

    async def _refresh_token(self) -> None:
        session = await self._ensure_session()
        async with session.post(TOKEN_URL, json={
            "appId": self.app_id,
            "clientSecret": self.client_secret,
        }) as resp:
            data = await resp.json(content_type=None)
        token = data.get("access_token")
        if not token:
            err = QQAPIError(int(data.get("code", 10001)),
                             str(data.get("message", "获取 access_token 失败")))
            log_qq_error(log, err, self.error_min_level, "auth")
            raise err
        expires = int(data.get("expires_in", 7000))
        self._token = token
        self._token_expire_at = time.time() + expires - 60
        log.info("已获取 QQ app access_token，%d 秒后过期", expires)

    async def access_token(self) -> str:
        async with self._token_lock:
            if not self._token or time.time() >= self._token_expire_at:
                await self._refresh_token()
            return self._token

    async def auth_header(self) -> dict[str, str]:
        return {"Authorization": f"QQBot {await self.access_token()}"}

    async def request(self, method: str, path: str, *,
                      json: Any = None, params: Any = None) -> Any:
        session = await self._ensure_session()
        headers = await self.auth_header()
        url = self.base + path
        try:
            async with session.request(method, url, json=json,
                                       params=params, headers=headers) as resp:
                data = await resp.json(content_type=None)
        except aiohttp.ClientError as e:
            log.warning("QQ API 网络错误 %s %s: %s", method, path, e)
            raise QQAPIError(11001, f"网络错误: {e}") from e

        if isinstance(data, dict) and "code" in data:
            err = QQAPIError(int(data["code"]), str(data.get("message", "")),
                             http_status=resp.status)
            log_qq_error(log, err, self.error_min_level, f"{method} {path}")
            raise err
        return data

    async def get(self, path: str, **kw) -> Any:
        return await self.request("GET", path, **kw)

    async def post(self, path: str, **kw) -> Any:
        return await self.request("POST", path, **kw)

    async def put(self, path: str, **kw) -> Any:
        return await self.request("PUT", path, **kw)

    async def gateway_bot(self) -> dict[str, Any]:
        """GET /gateway/bot -> {url, shards, session_start_limit{...}}"""
        return await self.get("/gateway/bot")

    async def send_group_message(self, group_openid: str, *,
                                 content: str | None = None,
                                 msg_type: int | None = None,
                                 msg_id: str | None = None,
                                 event_id: str | None = None,
                                 msg_seq: int | None = None,
                                 markdown: dict[str, Any] | None = None,
                                 keyboard: dict[str, Any] | None = None,
                                 media: dict[str, Any] | None = None,
                                 message_reference: dict[str, Any] | None = None,
                                 stream: bool | None = None,
                                 **extra: Any) -> dict:
        body = _build_message_body(
            msg_type=msg_type, content=content, markdown=markdown,
            keyboard=keyboard, media=media,
            message_reference=message_reference,
            msg_id=msg_id, event_id=event_id, msg_seq=msg_seq,
            stream=stream, extra=extra)
        return await self.post(f"/v2/groups/{group_openid}/messages", json=body)

    async def send_c2c_message(self, openid: str, *,
                               content: str | None = None,
                               msg_type: int | None = None,
                               msg_id: str | None = None,
                               event_id: str | None = None,
                               msg_seq: int | None = None,
                               markdown: dict[str, Any] | None = None,
                               keyboard: dict[str, Any] | None = None,
                               media: dict[str, Any] | None = None,
                               message_reference: dict[str, Any] | None = None,
                               is_wakeup: bool | None = None,
                               input_notify: dict[str, Any] | None = None,
                               stream: bool | None = None,
                               **extra: Any) -> dict:
        body = _build_message_body(
            msg_type=msg_type, content=content, markdown=markdown,
            keyboard=keyboard, media=media,
            message_reference=message_reference,
            msg_id=msg_id, event_id=event_id, msg_seq=msg_seq,
            is_wakeup=is_wakeup, input_notify=input_notify,
            stream=stream, extra=extra)
        return await self.post(f"/v2/users/{openid}/messages", json=body)

    async def send_channel_message(self, channel_id: str, *,
                                   content: str | None = None,
                                   msg_id: str | None = None,
                                   event_id: str | None = None,
                                   msg_seq: int | None = None,
                                   markdown: dict[str, Any] | None = None,
                                   keyboard: dict[str, Any] | None = None,
                                   **extra: Any) -> dict:
        """发送频道子频道消息；extra 可携带官方支持的 embed/ark/image 等字段。"""
        if msg_id and event_id:
            raise ValueError("msg_id 与 event_id 只能二选一")
        body: dict[str, Any] = {}
        if content not in (None, ""):
            body["content"] = content
        if markdown is not None:
            body["markdown"] = markdown
        if keyboard is not None:
            body["keyboard"] = keyboard
        if msg_id:
            body["msg_id"] = msg_id
        elif event_id:
            body["event_id"] = event_id
        if (msg_id or event_id) and msg_seq is not None:
            body["msg_seq"] = msg_seq
        body.update(extra)
        return await self.post(f"/channels/{channel_id}/messages", json=body)

    async def respond_interaction(self, interaction_id: str, *,
                                  code: int = 0) -> dict:
        """回应 INTERACTION_CREATE，避免客户端按钮一直 loading。"""
        return await self.put(f"/interactions/{interaction_id}", json={
            "code": int(code),
        })

    async def approve_join_request(self, group_openid: str, member_openid: str,
                                   *, op: str, join_request_id: str | None = None,
                                   reject_reason: str | None = None,
                                   blacklist: bool = False) -> dict:
        """入群申请审批（机器人需拥有群管理员身份，60 QPM）。

        op: "approve" 通过 | "decline" 拒绝
        """
        body: dict[str, Any] = {"op": op}
        if join_request_id:
            body["join_request_id"] = join_request_id
        if reject_reason and op == "decline":
            body["reject_reason"] = reject_reason
        if blacklist and op == "decline":
            body["add_to_member_blacklist"] = True
        return await self.post(
            f"/v2/groups/{group_openid}/approval_join_request/{member_openid}",
            json=body)

    async def upload_group_file(self, group_openid: str, *, file_type: int,
                                url: str, srv_send_msg: bool = False) -> dict:
        return await self.post(f"/v2/groups/{group_openid}/files", json={
            "file_type": file_type, "url": url, "srv_send_msg": srv_send_msg,
        })

    async def upload_c2c_file(self, openid: str, *, file_type: int,
                              url: str, srv_send_msg: bool = False) -> dict:
        return await self.post(f"/v2/users/{openid}/files", json={
            "file_type": file_type, "url": url, "srv_send_msg": srv_send_msg,
        })

    async def list_group_members(self, group_openid: str, *,
                                 next_index: str | None = None,
                                 limit: int = 100) -> dict:
        """GET /v2/groups/{group_openid}/members 分页拉取群成员。"""
        params: dict[str, Any] = {"limit": limit}
        if next_index:
            params["next_index"] = next_index
        return await self.get(f"/v2/groups/{group_openid}/members",
                              params=params)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
