"""OneBot 动作(action)处理层。

**完整注册 OneBot 11 标准动作与 OneBot 12(实验性)核心动作**：
- QQ 官方协议可实现的动作 -> 真实实现（消息发送、主动推送、Markdown、按钮、互动回应、入群审批等）
- QQ 官方协议没有的能力 -> 动作依然可以访问，返回明确的 retcode 1404

发送语义（对齐 QQ 开放平台 API v2 现行文档）：
- 显式传 `msg_id` 或 `event_id`：被动回复，二者只能选一；
- 传 `passive=true`：自动使用当前会话最近收到的上下文做被动回复；
- 都不传：主动推送（不带 `msg_id/event_id/msg_seq`）；
- `qqbot.message.passive_by_default=true` 可把第 3 种默认改为第 2 种。

Q2OEX 消息扩展：
- OB12 消息段：`q2oex:markdown`、`q2oex:keyboard`、`q2oex:button`、`q2oex:keyboard_row`
- OB11 CQ 码：`[CQ:q2oex,type=markdown,data=<base64url-json>]`（keyboard/button 同理）
- 所有标准发送动作也接受 `markdown` / `keyboard` 参数；完整定义见 Q2OEX.md。

QQ 侧业务错误码转换为 OneBot retcode 并向上反馈给调用方:
  retcode 0      成功
  retcode 1300   QQ 侧可重试/轻微错误 (minor)
  retcode 1400   QQ 侧主要错误 (major)
  retcode 1500   QQ 侧致命错误 (fatal)
  retcode 1404   该能力 QQ 官方 Bot 协议不支持
wording / message 中携带 QQ 原始错误码，便于业务侧定位。
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from typing import Any, Awaitable, Callable

from .. import __version__
from ..qqbot.api import QQAPI
from ..qqbot.errors import QQAPIError
from ..utils.context import (JoinRequestContext, MessageContext,
                             MessageStore, PassiveRef)
from ..utils.idmap import IdMapper

log = logging.getLogger("q2o.onebot.actions")

_CQ_RE = re.compile(r"\[CQ:([a-zA-Z0-9_-]+)((?:,[^\]]*)?)\]")
_CQ_PARAM_RE = re.compile(r"([a-zA-Z0-9_-]+)=([^,\]]*)")

# CQ 码转义规则：OneBot 文本中的转义序列 -> 原始字符
_CQ_UNESCAPES = (
    ("&amp;", "&"),
    ("&#91;", "["),
    ("&#93;", "]"),
    ("&#44;", ","),
)

# QQ 发送 content 时的转义（与官方接收方向相反，仅用于纯文本段）
_QQ_ESCAPES = (
    ("&", "&amp;"),
    ("[", "&#91;"),
    ("]", "&#93;"),
)

# QQ 富媒体上传 file_type: 1 图片, 2 视频, 3 语音, 4 文件
_FILE_TYPES = {"image": 1, "video": 2, "record": 3, "file": 4}

_SEV_RETCODE = {"minor": 1300, "major": 1400, "fatal": 1500}

# 自动/上下文被动引用失效时，可安全降级为主动消息重试的 QQ 错误码
_PASSIVE_FALLBACK_CODES = {
    304103, 40034005, 40034024, 40034025,
    40034026, 40034027, 40034128,
}

# ---------------------------------------------------------------------------
# OneBot 11 标准动作全集（botuniverse/onebot-11 + 常见扩展动作）
# 表中未单独实现的动作自动注册为「可访问但返回 1404」。
# ---------------------------------------------------------------------------
OB11_ACTIONS: tuple[str, ...] = (
    # 消息
    "send_private_msg", "send_group_msg", "send_msg",
    "send_private_forward_msg", "send_group_forward_msg",
    "delete_msg", "get_msg", "get_forward_msg",
    # 点赞与群管理
    "send_like",
    "set_group_kick", "set_group_ban", "set_group_anonymous_ban",
    "set_group_whole_ban", "set_group_admin", "set_group_anonymous",
    "set_group_card", "set_group_name", "set_group_leave",
    "set_group_special_title",
    # 请求审批
    "set_friend_add_request", "set_group_add_request",
    # 账号与信息
    "get_login_info", "get_stranger_info",
    "get_friend_list",
    "get_group_info", "get_group_list",
    "get_group_member_info", "get_group_member_list",
    "get_group_honor_info",
    # 凭证与文件
    "get_cookies", "get_csrf_token", "get_credentials",
    "get_record", "get_image",
    "can_send_image", "can_send_record",
    # 状态与运维
    "get_status", "get_version_info", "set_restart", "clean_cache",
    # 内部动作（快速操作回执，go-cqhttp 风格）
    ".handle_quick_operation",
)

# OneBot 12(实验性) 核心动作 -> OneBot 11 动作名映射（同名直通）
_OB12_TO_OB11: dict[str, str] = {
    "delete_message": "delete_msg",
    "get_user_info": "get_stranger_info",
    "get_friend_list": "get_friend_list",
    "get_group_info": "get_group_info",
    "get_group_list": "get_group_list",
    "get_group_member_info": "get_group_member_info",
    "get_group_member_list": "get_group_member_list",
    "set_group_name": "set_group_name",
    "leave_group": "set_group_leave",
}
# OneBot 12 中单独适配/实现的动作
_OB12_SPECIAL = ("get_supported_actions", "get_status", "get_version",
                 "get_self_info", "send_message", "upload_file",
                 "upload_file_fragmented", "get_file", "get_file_fragmented")


def ok(data: Any = None) -> dict[str, Any]:
    return {"status": "ok", "retcode": 0, "data": data, "message": "", "wording": ""}


def fail(retcode: int, wording: str) -> dict[str, Any]:
    return {"status": "failed", "retcode": retcode, "data": None,
            "message": wording, "wording": wording}


def _qq_fail(e: QQAPIError) -> dict[str, Any]:
    return fail(_SEV_RETCODE.get(e.severity, 1400),
                f"QQ错误码 {e.code}: {e.description} - {e.message}")


def unsupported(name: str) -> dict[str, Any]:
    return fail(1404, f"动作 {name} 不受 QQ 官方 Bot 协议支持")


def _unescape_cq(text: str) -> str:
    for src, dst in _CQ_UNESCAPES:
        text = text.replace(src, dst)
    return text


def _escape_qq_text(text: str) -> str:
    for src, dst in _QQ_ESCAPES:
        text = text.replace(src, dst)
    return text


# ---------------------------------------------------------------------------
# Q2OEX 消息结构规范化
# ---------------------------------------------------------------------------

def _decode_payload(value: str) -> Any:
    """解析 CQ:q2oex 的 data：优先 JSON，其次 base64url(JSON)。"""
    value = _unescape_cq(value or "").strip()
    if not value:
        return None
    if value.startswith("{"):
        return json.loads(value)
    padding = "=" * (-len(value) % 4)
    raw = base64.urlsafe_b64decode((value + padding).encode("ascii"))
    return json.loads(raw.decode("utf-8"))


def _normalize_markdown(value: Any) -> dict[str, Any]:
    if value is None:
        raise ValueError("markdown 不能为空")
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{"):
            parsed = json.loads(stripped)
            if not isinstance(parsed, dict):
                raise ValueError("markdown JSON 必须是对象")
            return parsed
        return {"content": value}
    if isinstance(value, dict):
        return dict(value)
    raise ValueError("markdown 必须是字符串或对象")


def _normalize_button(value: Any) -> dict[str, Any]:
    """把 Q2OEX 简写按钮规范化为 QQ 官方 Button。"""
    if isinstance(value, str):
        value = {"label": value, "data": value}
    if not isinstance(value, dict):
        raise ValueError("button 必须是对象或字符串")

    # 已是官方结构则原样透传
    if "render_data" in value or "action" in value:
        return dict(value)

    label = str(value.get("label", ""))
    if not label:
        raise ValueError("button.label 不能为空")

    render_data: dict[str, Any] = {"label": label}
    if value.get("visited_label") is not None:
        render_data["visited_label"] = str(value.get("visited_label"))
    if value.get("style") is not None:
        render_data["style"] = int(value.get("style"))

    action: dict[str, Any] = {
        # 简写默认做指令按钮；业务可显式传 type=0/1 改为链接/回调按钮。
        "type": int(value.get("type", 2)),
        "permission": dict(value.get("permission")
                           or {"type": int(value.get("permission_type", 2))}),
        "data": str(value.get("data") or label),
    }
    for key in ("unsupport_tips", "enter", "reply", "anchor", "click_limit",
                "at_bot_show_channel_list", "modal"):
        if value.get(key) is not None:
            action[key] = value[key]

    button: dict[str, Any] = {
        "render_data": render_data,
        "action": action,
    }
    if value.get("id") is not None:
        button["id"] = str(value.get("id"))
    if value.get("group_id") is not None:
        button["group_id"] = str(value.get("group_id"))
    return button


def _normalize_keyboard_row(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        return {"buttons": [_normalize_button(v) for v in value]}
    if isinstance(value, dict):
        row = dict(value)
        row["buttons"] = [_normalize_button(v) for v in row.get("buttons") or []]
        return row
    raise ValueError("keyboard 行必须是按钮数组或对象")


def _normalize_keyboard(value: Any) -> dict[str, Any]:
    if value is None:
        raise ValueError("keyboard 不能为空")
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{"):
            parsed = json.loads(stripped)
            if not isinstance(parsed, dict):
                raise ValueError("keyboard JSON 必须是对象")
            value = parsed
        else:
            # 平台按钮模板 ID 短形式
            return {"id": value}
    if isinstance(value, list):
        return {"content": {"rows": [_normalize_keyboard_row(v) for v in value]}}
    if not isinstance(value, dict):
        raise ValueError("keyboard 必须是模板 ID、对象或行数组")

    keyboard = dict(value)
    if "rows" in keyboard:  # Q2OEX 简写：直接传 rows
        return {"content": {"rows": [_normalize_keyboard_row(v)
                                     for v in keyboard.get("rows") or []]}}
    if "buttons" in keyboard:  # Q2OEX 简写：单行按钮
        return {"content": {"rows": [_normalize_keyboard_row(keyboard)]}}
    if "content" in keyboard and keyboard["content"] is not None:
        content = dict(keyboard["content"])
        content["rows"] = [_normalize_keyboard_row(v)
                           for v in content.get("rows") or []]
        keyboard["content"] = content
    return keyboard


def _normalize_input_notify(value: Any) -> dict[str, Any]:
    if value is None:
        return {"input_type": 1, "input_second": 60}
    if isinstance(value, dict):
        notify = {"input_type": 1, "input_second": 60}
        notify.update(value)
        return notify
    if isinstance(value, (int, float)):
        return {"input_type": 1, "input_second": int(value)}
    raise ValueError("input_notify 必须是对象或秒数")


class _QQContent:
    """从 OneBot 消息解析出的 QQ 侧消息结构。"""

    __slots__ = ("content", "markdown", "keyboard", "media_type",
                 "media_url", "reply_msg_id")

    def __init__(self) -> None:
        self.content = ""             # QQ content 字符串（文本 + at 标签，文本已转义）
        self.markdown: dict[str, Any] | None = None
        self.keyboard: dict[str, Any] | None = None
        self.media_type = 0           # QQ msg_type（0 纯文本, 7 富媒体）
        self.media_url: str | None = None
        self.reply_msg_id: str | None = None


def _ensure_keyboard(qc: _QQContent) -> dict[str, Any]:
    if qc.keyboard is None:
        qc.keyboard = {"content": {"rows": []}}
    keyboard = qc.keyboard
    if "id" in keyboard and "content" not in keyboard:
        raise ValueError("keyboard 模板 ID 与自定义 buttons 不能混用")
    if keyboard.get("content") is None:
        keyboard["content"] = {"rows": []}
    content = keyboard["content"]
    if not isinstance(content, dict):
        raise ValueError("keyboard.content 必须是对象")
    content.setdefault("rows", [])
    return keyboard


def _append_keyboard_row(qc: _QQContent, row: Any) -> None:
    keyboard = _ensure_keyboard(qc)
    keyboard["content"]["rows"].append(_normalize_keyboard_row(row))


def _append_button(qc: _QQContent, button: Any) -> None:
    keyboard = _ensure_keyboard(qc)
    rows = keyboard["content"]["rows"]
    if not rows:
        rows.append({"buttons": []})
    rows[-1]["buttons"].append(_normalize_button(button))


def _apply_q2oex_payload(qc: _QQContent, kind: str, payload: Any) -> None:
    kind = kind.lower().replace("q2oex:", "")
    if kind in ("markdown", "md"):
        if isinstance(payload, dict) and "markdown" in payload:
            payload = payload["markdown"]
        elif isinstance(payload, dict) and "content" not in payload and "data" in payload:
            payload = payload["data"]
        qc.markdown = _normalize_markdown(payload)
    elif kind in ("keyboard", "buttons"):
        if isinstance(payload, dict) and "keyboard" in payload:
            payload = payload["keyboard"]
        qc.keyboard = _normalize_keyboard(payload)
    elif kind in ("button", "btn"):
        _append_button(qc, payload)
    elif kind in ("keyboard_row", "row"):
        _append_keyboard_row(qc, payload)
    else:
        raise ValueError(f"未知 Q2OEX 消息类型: {kind}")


class ActionHandler:
    def __init__(self, api: QQAPI, idmap: IdMapper, ctx: MessageContext,
                 self_id: str, app_id: str,
                 join_ctx: JoinRequestContext | None = None,
                 store: MessageStore | None = None,
                 q2oex_enable: bool = True,
                 passive_by_default: bool = False):
        self.api = api
        self.idmap = idmap
        self.ctx = ctx
        self.self_id = self_id
        self.app_id = app_id
        self.join_ctx = join_ctx or JoinRequestContext()
        self.store = store
        self.q2oex_enable = q2oex_enable
        self.passive_by_default = passive_by_default

        # ------------------------------------------------ 真实实现的动作
        self._handlers: dict[str, Callable[[dict, int], Awaitable[dict]]] = {
            "send_msg": self._send_msg,
            "send_private_msg": self._send_private_msg,
            "send_group_msg": self._send_group_msg,
            "delete_msg": self._delete_msg,
            "get_msg": self._get_msg,
            "get_image": self._get_file_passthrough,
            "get_record": self._get_file_passthrough,
            "can_send_image": self._can_send_image,
            "can_send_record": self._can_send_record,
            "get_login_info": self._get_login_info,
            "get_version_info": self._get_version_info,
            "get_status": self._get_status,
            "get_group_member_list": self._get_group_member_list,
            # 入群申请审批（QQ 官方 GROUP_JOIN_REQUEST 事件）
            "set_group_add_request": self._set_group_add_request,
            # 快速操作回执（内部）
            ".handle_quick_operation": self._handle_quick_operation,
            # q2oex 扩展动作
            "q2oex.send_message": self._q2oex_send_message,
            "q2oex.send_group_message": self._q2oex_send_group,
            "q2oex.send_private_message": self._q2oex_send_private,
            "q2oex.send_channel_message": self._q2oex_send_channel,
            "q2oex.send_input_notify": self._q2oex_send_input_notify,
            "q2oex.respond_interaction": self._q2oex_respond_interaction,
            "q2oex.get_gateway_info": self._q2oex_gateway_info,
        }

        # ------------------------------------ 其余标准动作：可访问但返回 1404
        special_reasons = {
            "set_friend_add_request":
                "set_friend_add_request（QQ 官方协议中好友添加无需审批，无对应接口）",
            "delete_msg":
                "delete_msg（QQ 官方 Bot 协议不支持撤回群/C2C 消息）",
            "get_forward_msg":
                "get_forward_msg（QQ 官方协议无合并转发概念）",
            "send_private_forward_msg":
                "send_private_forward_msg（QQ 官方协议无合并转发概念）",
            "send_group_forward_msg":
                "send_group_forward_msg（QQ 官方协议无合并转发概念）",
            "get_cookies":
                "get_cookies（官方 Bot 使用 app access token，无网页 Cookie 体系）",
            "get_csrf_token":
                "get_csrf_token（官方 Bot 使用 app access token，无 CSRF 体系）",
            "get_credentials":
                "get_credentials（官方 Bot 使用 app access token，无网页凭证体系）",
        }
        for name in OB11_ACTIONS:
            if name in self._handlers:
                continue
            label = special_reasons.get(name, name)
            self._handlers[name] = self._make_unsupported(label)

    @staticmethod
    def _make_unsupported(name: str) -> Callable[[dict, int], Awaitable[dict]]:
        async def _stub(p: dict, v: int) -> dict:
            return unsupported(name)
        return _stub

    # ------------------------------------------------------------------ 入口
    async def dispatch(self, action: str, params: dict | None,
                       protocol: int) -> dict[str, Any]:
        params = params or {}
        if action.startswith("q2oex.") and not self.q2oex_enable:
            return fail(1404, "q2oex 扩展协议已在配置中关闭")
        if protocol == 12:
            return await self._dispatch_v12(action, params)
        handler = self._handlers.get(action)
        if handler is None:
            return fail(1404, f"未知动作: {action}")
        try:
            return await handler(params, protocol)
        except QQAPIError as e:
            return _qq_fail(e)
        except ValueError as e:
            return fail(1400, str(e))
        except Exception as e:  # noqa: BLE001 - 兜底，保证连接不被异常打断
            log.exception("动作 %s 处理异常", action)
            return fail(1400, f"内部错误: {e}")

    # ------------------------------------------------------------------ OB12
    async def _dispatch_v12(self, action: str, params: dict) -> dict[str, Any]:
        """OneBot 12(实验性) 动作适配入口。"""
        try:
            if action.startswith("q2oex.") and not self.q2oex_enable:
                return fail(1404, "q2oex 扩展协议已在配置中关闭")
            if action == "get_supported_actions":
                names = set(self._handlers) | set(_OB12_TO_OB11) | set(_OB12_SPECIAL)
                if not self.q2oex_enable:
                    names = {n for n in names if not n.startswith("q2oex.")}
                return ok(sorted(names))
            if action == "get_version":
                return await self._get_version_info(params, 12)
            if action == "get_status":
                return ok({"good": True, "online": True,
                           "bots": [{"self": {"platform": "qq",
                                              "user_id": str(self.self_id)},
                                     "online": True}]})
            if action == "get_self_info":
                return ok({"user_id": str(self.self_id),
                           "nickname": f"q2o-bot-{self.app_id}",
                           "platform": "qq"})
            if action == "send_message":
                return await self._ob12_send_message(params)
            if action == "upload_file":
                return await self._ob12_upload_file(params)
            if action in ("upload_file_fragmented",
                          "get_file", "get_file_fragmented"):
                return unsupported(action)
            # 映射到 OB11 同名动作
            ob11 = _OB12_TO_OB11.get(action, action)
            handler = self._handlers.get(ob11)
            if handler is None:
                return fail(1404, f"未知动作: {action}")
            return await handler(params, 12)
        except QQAPIError as e:
            return _qq_fail(e)
        except ValueError as e:
            return fail(1400, str(e))
        except Exception as e:  # noqa: BLE001
            log.exception("OB12 动作 %s 处理异常", action)
            return fail(1400, f"内部错误: {e}")

    async def _ob12_send_message(self, p: dict) -> dict:
        """OB12 send_message: {detail_type, user_id?/group_id?/channel_id?, message[]}"""
        detail = p.get("detail_type", "")
        if detail == "group":
            return await self._send_group_msg(p, 12)
        if detail == "private":
            return await self._send_private_msg(p, 12)
        if detail == "channel":
            return await self._send_channel(p)
        return fail(1400, f"send_message 缺少或不支持的 detail_type: {detail!r}")

    async def _ob12_upload_file(self, p: dict) -> dict:
        """OB12 upload_file: 仅支持 type=url（QQ 富媒体仅接受 URL 上传）。

        需要同时给出 group_id 或 user_id 以确定上传目标。
        """
        if p.get("type") != "url" or not p.get("url"):
            return fail(1404, "QQ 官方协议仅支持以 URL 方式上传文件")
        name = str(p.get("name", ""))
        file_type = 1 if p.get("file_type_hint") == "image" else 4
        if name.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
            file_type = 1
        elif name.lower().endswith((".mp4", ".mov")):
            file_type = 2
        elif name.lower().endswith((".mp3", ".silk", ".amr", ".wav")):
            file_type = 3
        if p.get("group_id") is not None:
            openid = self.idmap.resolve_external(p["group_id"])
            if not openid:
                return fail(1404, "未知 group_id，无法解析 openid")
            result = await self.api.upload_group_file(
                openid, file_type=file_type, url=p["url"])
        elif p.get("user_id") is not None:
            openid = self.idmap.resolve_external(p["user_id"])
            if not openid:
                return fail(1404, "未知 user_id，无法解析 openid")
            result = await self.api.upload_c2c_file(
                openid, file_type=file_type, url=p["url"])
        else:
            return fail(1400, "upload_file 需要 group_id 或 user_id 指定上传目标")
        return ok({"file_id": result.get("file_info", "")})

    # ------------------------------------------------------------------ 消息解析
    def _build_content(self, message: Any) -> _QQContent:
        """OneBot 消息(string CQ / 段数组) -> QQ 侧消息结构。

        支持：text / at / reply / image / record / video / file /
        Q2OEX 的 markdown、keyboard、button、keyboard_row。
        QQ 单条消息仅可容纳一个富媒体附件，多余附件丢弃并记 warning。
        """
        qc = _QQContent()
        parts: list[str] = []
        media: tuple[int, str] | None = None

        def add_text(t: str) -> None:
            if t:
                parts.append(_escape_qq_text(t))

        def add_at(qq: str) -> None:
            if qq == "all":
                log.warning("QQ 官方 Bot 协议不支持 at 全体成员，已跳过")
                return
            openid = self.idmap.resolve_external(qq)
            if not openid:
                log.warning("at 目标 %s 未知（未收到过该用户消息），已跳过", qq)
                return
            parts.append(f'<qqbot-at-user id="{openid}"/>')

        def add_media(seg_type: str, url: str) -> None:
            nonlocal media
            if not url:
                return
            if media is None:
                media = (_FILE_TYPES[seg_type], url)
            else:
                log.warning("QQ 单条消息仅支持一个附件，多余 %s 已丢弃: %s",
                            seg_type, url)

        def add_reply(mid: Any) -> None:
            if str(mid).isdigit():
                qq_mid = self.idmap.qq_message_id(int(mid))
                if not qq_mid:
                    log.warning("reply 目标消息 %s 未知，按普通消息发送", mid)
                    return
            else:
                qq_mid = str(mid)
            if qq_mid:
                qc.reply_msg_id = qq_mid

        if isinstance(message, str):
            pos = 0
            for m in _CQ_RE.finditer(message):
                if m.start() > pos:
                    add_text(_unescape_cq(message[pos:m.start()]))
                seg_type = m.group(1).lower()
                params = {k: _unescape_cq(v)
                          for k, v in _CQ_PARAM_RE.findall(m.group(2) or "")}
                if seg_type == "at":
                    add_at(params.get("qq", ""))
                elif seg_type == "reply":
                    add_reply(params.get("id", "0"))
                elif seg_type in _FILE_TYPES:
                    add_media(seg_type, params.get("url") or params.get("file") or "")
                elif seg_type in ("q2oex", "q2o_ex"):
                    kind = str(params.get("type", ""))
                    raw = params.get("data") or params.get(kind) or ""
                    _apply_q2oex_payload(qc, kind, _decode_payload(raw))
                elif seg_type in ("markdown", "md", "keyboard", "button"):
                    raw = params.get("data") or params.get("value") or ""
                    _apply_q2oex_payload(qc, seg_type, _decode_payload(raw))
                else:
                    log.debug("不支持转发的 CQ 码 [%s]，已丢弃", seg_type)
                pos = m.end()
            if pos < len(message):
                add_text(_unescape_cq(message[pos:]))
        else:
            for seg in message or []:
                if not isinstance(seg, dict):
                    continue
                t = str(seg.get("type") or "").lower()
                d = seg.get("data") or {}
                if t == "text":
                    add_text(str(d.get("text", "")))
                elif t in ("mention", "at"):
                    add_at(str(d.get("user_id") or d.get("qq") or ""))
                elif t == "reply":
                    add_reply(d.get("message_id", 0))
                elif t in _FILE_TYPES or t == "audio":
                    add_media("record" if t == "audio" else t,
                              str(d.get("url") or d.get("file")
                                  or d.get("file_id") or ""))
                elif t.startswith("q2oex:") or t in ("markdown", "md",
                                                     "keyboard", "button",
                                                     "keyboard_row", "row"):
                    _apply_q2oex_payload(qc, t, d)
                else:
                    log.debug("不支持转发的消息段 %s，已丢弃", t)

        qc.content = "".join(parts)
        if media:
            qc.media_type, qc.media_url = media

        # QQ 官方约束：Markdown 与 content/富媒体是不同消息类型，不能混填。
        if qc.markdown is not None:
            if qc.media_url:
                raise ValueError("Markdown 消息不能与 image/record/video/file 同条发送")
            if qc.content.strip():
                raise ValueError("发送 Markdown 时普通文本 content 必须为空")
            qc.content = ""
        return qc

    def _apply_direct_extensions(self, qc: _QQContent, p: dict | None) -> None:
        """标准发送动作参数中的 markdown/keyboard/buttons 扩展。"""
        if not p:
            return
        if p.get("markdown") is not None:
            qc.markdown = _normalize_markdown(p.get("markdown"))
            if qc.media_url:
                raise ValueError("Markdown 消息不能与富媒体同条发送")
            if qc.content.strip():
                raise ValueError("发送 Markdown 时普通文本 content 必须为空")
            qc.content = ""
        if p.get("keyboard") is not None:
            qc.keyboard = _normalize_keyboard(p.get("keyboard"))
        for button in p.get("buttons") or []:
            _append_button(qc, button)

    # ------------------------------------------------------------------ 发送辅助
    def _coerce_msg_id(self, value: Any) -> str | None:
        if value in (None, ""):
            return None
        text = str(value)
        if text.isdigit():
            return self.idmap.qq_message_id(int(text)) or text
        return text

    def _resolve_group_openid(self, p: dict) -> str | None:
        raw = p.get("group_openid") or p.get("openid")
        if raw:
            return str(raw)
        return self.idmap.resolve_external(p.get("group_id", ""))

    def _resolve_user_openid(self, p: dict) -> str | None:
        raw = p.get("user_openid") or p.get("openid")
        if raw:
            return str(raw)
        return self.idmap.resolve_external(p.get("user_id", ""))

    def _passive_ref(self, target: str, kind: str, p: dict) -> tuple[PassiveRef, bool]:
        """返回 (被动引用, 是否允许被动失败降级主动重试)。"""
        explicit_msg_id = self._coerce_msg_id(p.get("msg_id"))
        explicit_event_id = (str(p.get("event_id"))
                             if p.get("event_id") not in (None, "") else None)
        explicit = bool(explicit_msg_id or explicit_event_id)

        passive = p.get("passive")
        if passive is None:
            passive = self.passive_by_default
        if p.get("is_wakeup"):
            # 互动召回消息与被动回复标识互斥。
            passive = False
            explicit_msg_id = explicit_event_id = None

        msg_seq = p.get("msg_seq")
        ref = self.ctx.next_ref(
            target, kind,
            msg_id=explicit_msg_id, event_id=explicit_event_id,
            msg_seq=int(msg_seq) if msg_seq is not None else None,
            passive=bool(passive))
        if ref.is_passive:
            log.debug("使用被动回复: target=%s msg_id=%s event_id=%s seq=%s",
                      target[:16], bool(ref.msg_id), bool(ref.event_id), ref.msg_seq)
        elif ref.active_reason:
            log.info("按主动消息发送: %s", ref.active_reason)
        return ref, not explicit

    def _message_reference(self, p: dict) -> dict[str, Any] | None:
        value = p.get("message_reference")
        if value in (None, ""):
            return None
        if isinstance(value, dict):
            return dict(value)
        return {"message_id": self._coerce_msg_id(value) or str(value)}

    def _finalize_message_result(self, result: dict[str, Any]) -> dict[str, Any]:
        qq_mid = str(result.get("id") or "")
        data: dict[str, Any] = {
            "message_id": self.idmap.message_id(qq_mid) if qq_mid else 0,
            "qq_message_id": qq_mid,
            "qq": {
                "id": qq_mid,
                "timestamp": result.get("timestamp", ""),
                "ext_info": result.get("ext_info") or {},
            },
        }
        return ok(data)

    async def _send_group_once(self, group_openid: str, qc: _QQContent,
                               ref: PassiveRef, p: dict, *,
                               file_info: str | None = None,
                               allow_fallback: bool = True) -> dict:
        content = None if qc.markdown is not None else (qc.content or " ")
        media = {"file_info": file_info} if file_info else None
        stream = p.get("stream") if p.get("stream") is not None else None
        try:
            result = await self.api.send_group_message(
                group_openid,
                content=content,
                markdown=qc.markdown,
                keyboard=qc.keyboard,
                media=media,
                msg_id=ref.msg_id,
                event_id=ref.event_id,
                msg_seq=ref.msg_seq,
                message_reference=self._message_reference(p),
                stream=stream if stream is None else bool(stream))
        except QQAPIError as e:
            if (allow_fallback and ref.is_passive
                    and e.code in _PASSIVE_FALLBACK_CODES):
                log.info("被动回复被 QQ 拒绝（%s），已自动改用主动消息重试", e)
                active = PassiveRef(active_reason="被动引用被平台拒绝后重试")
                return await self._send_group_once(
                    group_openid, qc, active, p, file_info=file_info,
                    allow_fallback=False)
            raise
        return self._finalize_message_result(result)

    async def _send_user_once(self, openid: str, qc: _QQContent,
                              ref: PassiveRef, p: dict, *,
                              file_info: str | None = None,
                              allow_fallback: bool = True) -> dict:
        if p.get("input_notify") is not None or int(p.get("msg_type", 0) or 0) == 6:
            notify = _normalize_input_notify(p.get("input_notify"))
            try:
                result = await self.api.send_c2c_message(
                    openid, msg_type=6, input_notify=notify,
                    msg_id=ref.msg_id, event_id=ref.event_id,
                    msg_seq=ref.msg_seq)
            except QQAPIError as e:
                if (allow_fallback and ref.is_passive
                        and e.code in _PASSIVE_FALLBACK_CODES):
                    log.info("输入状态被动引用被 QQ 拒绝（%s），改为主动重试", e)
                    active = PassiveRef(active_reason="被动引用被平台拒绝后重试")
                    return await self._send_user_once(
                        openid, qc, active, p, file_info=file_info,
                        allow_fallback=False)
                raise
            return self._finalize_message_result(result)

        content = None if qc.markdown is not None else (qc.content or " ")
        media = {"file_info": file_info} if file_info else None
        stream = p.get("stream") if p.get("stream") is not None else None
        try:
            result = await self.api.send_c2c_message(
                openid,
                content=content,
                markdown=qc.markdown,
                keyboard=qc.keyboard,
                media=media,
                msg_id=ref.msg_id,
                event_id=ref.event_id,
                msg_seq=ref.msg_seq,
                message_reference=self._message_reference(p),
                is_wakeup=(bool(p.get("is_wakeup"))
                           if p.get("is_wakeup") is not None else None),
                stream=stream if stream is None else bool(stream))
        except QQAPIError as e:
            if (allow_fallback and ref.is_passive
                    and e.code in _PASSIVE_FALLBACK_CODES):
                log.info("被动回复被 QQ 拒绝（%s），已自动改用主动消息重试", e)
                active = PassiveRef(active_reason="被动引用被平台拒绝后重试")
                return await self._send_user_once(
                    openid, qc, active, p, file_info=file_info,
                    allow_fallback=False)
            raise
        return self._finalize_message_result(result)

    async def _send_to_group(self, group_openid: str, message: Any,
                             p: dict | None = None) -> dict:
        p = dict(p or {})
        qc = self._build_content(message)
        self._apply_direct_extensions(qc, p)
        if qc.media_url and qc.markdown is not None:
            raise ValueError("Markdown 消息不能与富媒体同条发送")

        file_info: str | None = None
        if qc.media_url:
            uploaded = await self.api.upload_group_file(
                group_openid, file_type=qc.media_type, url=qc.media_url,
                srv_send_msg=False)
            file_info = uploaded.get("file_info", "")

        # CQ:reply 仍作为被动 msg_id；主动发送则不传任何被动标识。
        if qc.reply_msg_id and p.get("msg_id") in (None, ""):
            p["msg_id"] = qc.reply_msg_id
        ref, allow_fallback = self._passive_ref(group_openid, "group", p)
        return await self._send_group_once(
            group_openid, qc, ref, p, file_info=file_info,
            allow_fallback=allow_fallback)

    async def _send_to_user(self, openid: str, message: Any,
                            p: dict | None = None) -> dict:
        p = dict(p or {})
        qc = self._build_content(message)
        self._apply_direct_extensions(qc, p)
        if qc.media_url and qc.markdown is not None:
            raise ValueError("Markdown 消息不能与富媒体同条发送")

        file_info: str | None = None
        if qc.media_url:
            uploaded = await self.api.upload_c2c_file(
                openid, file_type=qc.media_type, url=qc.media_url,
                srv_send_msg=False)
            file_info = uploaded.get("file_info", "")

        if qc.reply_msg_id and p.get("msg_id") in (None, ""):
            p["msg_id"] = qc.reply_msg_id
        ref, allow_fallback = self._passive_ref(openid, "private", p)
        return await self._send_user_once(
            openid, qc, ref, p, file_info=file_info,
            allow_fallback=allow_fallback)

    async def _send_channel(self, p: dict) -> dict:
        channel_id = str(p.get("channel_id") or "")
        if not channel_id:
            return fail(1400, "缺少 channel_id")
        qc = self._build_content(p.get("message", p.get("content", "")))
        self._apply_direct_extensions(qc, p)
        if qc.media_url:
            return fail(1404, "频道消息当前仅支持文本/Markdown/按钮，富媒体请直接传官方 extra 字段")
        ref, _ = self._passive_ref(channel_id, "private", p)
        extra = {k: p[k] for k in ("embed", "ark", "image", "message_reference")
                 if k in p}
        result = await self.api.send_channel_message(
            channel_id,
            content=qc.content or None,
            markdown=qc.markdown,
            keyboard=qc.keyboard,
            msg_id=ref.msg_id,
            event_id=ref.event_id,
            msg_seq=ref.msg_seq,
            **extra)
        return self._finalize_message_result(result)

    # ------------------------------------------------------------------ 标准消息动作
    async def _send_msg(self, p: dict, protocol: int) -> dict:
        if p.get("group_id") is not None or p.get("group_openid"):
            return await self._send_group_msg(p, protocol)
        return await self._send_private_msg(p, protocol)

    async def _send_group_msg(self, p: dict, protocol: int) -> dict:
        openid = self._resolve_group_openid(p)
        if not openid:
            return fail(1404, "未知 group_id/group_openid（未收到过该群消息，无法解析 openid）")
        return await self._send_to_group(
            openid, p.get("message", p.get("content", "")), p)

    async def _send_private_msg(self, p: dict, protocol: int) -> dict:
        openid = self._resolve_user_openid(p)
        if not openid:
            return fail(1404, "未知 user_id/openid（未收到过该用户消息，无法解析 openid）")
        return await self._send_to_user(
            openid, p.get("message", p.get("content", "")), p)

    async def _delete_msg(self, p: dict, protocol: int) -> dict:
        return fail(1404, "QQ 官方 Bot 协议不支持撤回群/C2C 消息")

    async def _get_msg(self, p: dict, protocol: int) -> dict:
        """从近期消息缓存读取（仅覆盖机器人运行期间收到的消息）。"""
        if self.store is None:
            return fail(1404, "消息缓存未启用")
        record = self.store.get(int(p.get("message_id", 0)))
        if record is None:
            return fail(1400, "消息不存在或已超出缓存范围"
                              "（QQ 官方协议无历史消息查询接口，"
                              "仅支持获取运行期间收到的消息）")
        return ok(record)

    async def _get_file_passthrough(self, p: dict, protocol: int) -> dict:
        """get_image / get_record: 本实现 CQ 码 file 字段本身就是 URL，直接透传。"""
        file = str(p.get("file", ""))
        if not file:
            return fail(1400, "缺少 file 参数")
        return ok({"file": file, "url": file})

    async def _can_send_image(self, p: dict, protocol: int) -> dict:
        return ok({"yes": True})   # 支持 URL 图片

    async def _can_send_record(self, p: dict, protocol: int) -> dict:
        return ok({"yes": True})   # 支持 URL 语音(file_type=3)

    # ------------------------------------------------------------------ 入群审批
    async def _set_group_add_request(self, p: dict, protocol: int) -> dict:
        flag = str(p.get("flag", ""))
        if not flag:
            return fail(1400, "缺少 flag")
        item = self.join_ctx.get(flag)
        if not item:
            return fail(1400, "flag 无效或已过期（入群申请仅可审批一次，请确认事件未处理过）")
        group_openid, member_openid, join_request_id = item
        approve = bool(p.get("approve", True))
        await self.api.approve_join_request(
            group_openid, member_openid,
            op="approve" if approve else "decline",
            join_request_id=join_request_id,
            reject_reason=str(p.get("reason", "")) or None)
        return ok()

    async def _handle_quick_operation(self, p: dict, protocol: int) -> dict:
        """go-cqhttp 风格的快速操作：执行 operation 中的动作。"""
        operation = p.get("operation") or {}
        action = operation.pop("action", None)
        if not action:
            return ok()
        return await self.dispatch(str(action), operation, protocol)

    # ------------------------------------------------------------------ 信息
    async def _get_login_info(self, p: dict, protocol: int) -> dict:
        uid = int(self.self_id) if str(self.self_id).isdigit() else 0
        return ok({"user_id": uid, "nickname": f"q2o-bot-{self.app_id}"})

    async def _get_version_info(self, p: dict, protocol: int) -> dict:
        return ok({"app_name": "qqbot2onebot", "app_version": __version__,
                   "protocol_version": str(protocol),
                   "onebot_version": str(protocol)})

    async def _get_status(self, p: dict, protocol: int) -> dict:
        return ok({"online": True, "good": True,
                   "app_initialized": True, "app_enabled": True,
                   "plugins_good": True, "app_good": True})

    # ------------------------------------------------------------------ 群成员
    async def _get_group_member_list(self, p: dict, protocol: int) -> dict:
        """GET /v2/groups/{group_openid}/members 分页拉取（最多 10 页 × 100 人）。

        QQ 返回的字段有限（member_openid / join_timestamp 等），
        OneBot 侧缺失字段以空值补齐；无权限时 QQ 错误码自动向上反馈。
        """
        openid = self._resolve_group_openid(p)
        if not openid:
            return fail(1404, "未知 group_id（机器人未收到过该群消息，无法解析 openid）")
        group_id = self.idmap.register(openid)
        members: list[dict] = []
        next_index: str | None = None
        for _ in range(10):
            data = await self.api.list_group_members(
                openid, next_index=next_index)
            for m in data.get("members") or []:
                m_openid = m.get("member_openid", "")
                uid = self.idmap.register(m_openid)
                members.append({
                    "group_id": group_id, "user_id": uid,
                    "nickname": m_openid[:16], "card": "",
                    "sex": "unknown", "age": 0, "area": "",
                    "join_time": int(m.get("join_timestamp") or time.time()),
                    "last_sent_time": 0, "level": "0",
                    "role": "member", "unfriendly": False,
                    "title": "", "title_expire_time": 0,
                    "card_changeable": False,
                })
            next_index = data.get("next_index")
            if not next_index:
                break
        return ok(members)

    # ------------------------------------------------------------------ q2oex
    async def _q2oex_send_message(self, p: dict, protocol: int) -> dict:
        detail = str(p.get("detail_type", ""))
        if detail == "group":
            return await self._q2oex_send_group(p, protocol)
        if detail == "private":
            return await self._q2oex_send_private(p, protocol)
        if detail == "channel":
            return await self._q2oex_send_channel(p, protocol)
        return fail(1400, "q2oex.send_message 需要 detail_type: group|private|channel")

    async def _q2oex_send_group(self, p: dict, protocol: int) -> dict:
        openid = self._resolve_group_openid(p)
        if not openid:
            return fail(1404, "缺少 group_id/group_openid，或 group_id 无法解析")
        return await self._send_to_group(
            openid, p.get("message", p.get("content", "")), p)

    async def _q2oex_send_private(self, p: dict, protocol: int) -> dict:
        openid = self._resolve_user_openid(p)
        if not openid:
            return fail(1404, "缺少 user_id/openid，或 user_id 无法解析")
        return await self._send_to_user(
            openid, p.get("message", p.get("content", "")), p)

    async def _q2oex_send_channel(self, p: dict, protocol: int) -> dict:
        return await self._send_channel(p)

    async def _q2oex_send_input_notify(self, p: dict, protocol: int) -> dict:
        openid = self._resolve_user_openid(p)
        if not openid:
            return fail(1404, "缺少 user_id/openid，或 user_id 无法解析")
        return await self._send_to_user(openid, "", p)

    async def _q2oex_respond_interaction(self, p: dict, protocol: int) -> dict:
        interaction_id = str(p.get("interaction_id") or p.get("id") or "")
        if not interaction_id:
            return fail(1400, "缺少 interaction_id")
        code = int(p.get("code", 0))
        result = await self.api.respond_interaction(interaction_id, code=code)
        return ok(result)

    async def _q2oex_gateway_info(self, p: dict, protocol: int) -> dict:
        return ok(await self.api.gateway_bot())
