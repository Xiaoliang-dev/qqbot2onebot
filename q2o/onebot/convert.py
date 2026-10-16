"""QQ 官方事件 -> OneBot 11 / OneBot 12 事件转换器。"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from ..utils.context import JoinRequestContext, MessageContext, MessageStore
from ..utils.idmap import IdMapper
from .q2oex import build_q2oex_event, wrap_ob11, wrap_ob12

log = logging.getLogger("q2o.onebot.convert")

_AT_RE = re.compile(
    r'<qqbot-at-user id="(?P<legacy>[^"]+)"\s*/>|<@(?P<mention>[^>\s]+)>'
)

# QQ 消息内容的转义规则（官方）：接收时反转义
_CONTENT_ESCAPES = (
    ("&amp;", "&"),
    ("&#91;", "["),
    ("&#93;", "]"),
)


def _unescape_content(text: str) -> str:
    for src, dst in _CONTENT_ESCAPES:
        text = text.replace(src, dst)
    return text


def _at_openid(match: re.Match) -> str:
    return match.group("legacy") or match.group("mention")


def _effective_content(d: dict[str, Any]) -> str:
    """提取当前 QQ 消息事件中的可读文本。

    现行群/C2C事件可能不带 content，而改由 msg_elements（如引用消息）承载文本。
    """
    content = str(d.get("content") or "")
    elements = d.get("msg_elements") or []
    texts = [str(e.get("content") or "") for e in elements if isinstance(e, dict)]
    texts = [t for t in texts if t]
    if not texts:
        return content
    return "\n".join([content, *texts]).strip("\n")


# ---------------------------------------------------------------------------
# 消息内容转换（QQ -> OneBot）
# ---------------------------------------------------------------------------

def content_to_cq(content: str, attachments: list[dict] | None,
                  idmap: IdMapper) -> str:
    """QQ content 字符串 -> CQ 码字符串（at 目标翻译为虚拟号）。"""

    def at_sub(m: re.Match) -> str:
        uid = idmap.register(_at_openid(m))
        return f"[CQ:at,qq={uid}]"

    text = _unescape_content(_AT_RE.sub(at_sub, content or ""))
    for att in attachments or []:
        url = att.get("url", "")
        ctype = att.get("content_type", "")
        if ctype.startswith("image/") or att.get("width"):
            text += f"[CQ:image,file={url}]"
        elif ctype.startswith("video/"):
            text += f"[CQ:video,file={url}]"
        elif ctype.startswith("audio/"):
            text += f"[CQ:record,file={url}]"
        else:
            text += f"[CQ:file,file={url}]"
    return text


def content_to_ob12_segments(content: str, attachments: list[dict] | None,
                             idmap: IdMapper) -> list[dict]:
    """QQ content -> OneBot 12 消息段数组。"""
    segments: list[dict] = []
    pos = 0
    src = content or ""
    for m in _AT_RE.finditer(src):
        if m.start() > pos:
            segments.append({"type": "text",
                             "data": {"text": _unescape_content(src[pos:m.start()])}})
        segments.append({"type": "mention",
                         "data": {"user_id": str(idmap.register(_at_openid(m)))}})
        pos = m.end()
    rest = _unescape_content(src[pos:])
    if rest:
        segments.append({"type": "text", "data": {"text": rest}})
    for att in attachments or []:
        url = att.get("url", "")
        ctype = att.get("content_type", "")
        if ctype.startswith("image/") or att.get("width"):
            seg_type = "image"
        elif ctype.startswith("video/"):
            seg_type = "video"
        elif ctype.startswith("audio/"):
            seg_type = "audio"
        else:
            seg_type = "file"
        segments.append({"type": seg_type, "data": {"file_id": url, "url": url}})
    return segments or [{"type": "text", "data": {"text": ""}}]


def _qq_message_meta(d: dict[str, Any]) -> dict[str, Any]:
    """现行群/C2C事件中的扩展字段，原样带给业务侧。"""
    return {
        "qq.message_type": d.get("message_type"),
        "qq.message_scene": d.get("message_scene") or {},
        "qq.msg_elements": d.get("msg_elements") or [],
        "qq.ark_data": d.get("ark_data") or {},
    }


def _parse_ts(ts: str | None) -> int:
    if not ts:
        return int(time.time())
    try:
        from datetime import datetime
        return int(datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return int(time.time())


# ---------------------------------------------------------------------------
# 主转换入口
# ---------------------------------------------------------------------------

class EventConverter:
    def __init__(self, idmap: IdMapper, ctx: MessageContext,
                 self_id: str, q2oex_enable: bool = True,
                 join_ctx: JoinRequestContext | None = None,
                 store: MessageStore | None = None):
        self.idmap = idmap
        self.ctx = ctx
        self.self_id = self_id
        self.q2oex_enable = q2oex_enable
        self.join_ctx = join_ctx or JoinRequestContext()
        self.store = store  # 支撑 get_msg 动作的近期消息缓存

    def convert(self, qq_type: str, d: dict[str, Any],
                protocol: int, *, event_id: str = "") -> dict[str, Any] | None:
        """protocol: 11 或 12；返回 None 表示该事件被忽略。"""
        event_id_handlers = {
            "GROUP_AT_MESSAGE_CREATE": self._group_message,
            "GROUP_MESSAGE_CREATE": self._group_message,
            "C2C_MESSAGE_CREATE": self._c2c_message,
            "AT_MESSAGE_CREATE": self._channel_message,
            "MESSAGE_CREATE": self._channel_message,
            "DIRECT_MESSAGE_CREATE": self._direct_message,
            "INTERACTION_CREATE": self._interaction,
        }
        if qq_type in event_id_handlers:
            event = event_id_handlers[qq_type](d, protocol, event_id)
            if event is not None:
                self._maybe_store(qq_type, d)
            return event

        handler = {
            "GROUP_AT_MESSAGE_CREATE": self._group_message,
            "GROUP_MESSAGE_CREATE": self._group_message,
            "C2C_MESSAGE_CREATE": self._c2c_message,
            "AT_MESSAGE_CREATE": self._channel_message,
            "MESSAGE_CREATE": self._channel_message,
            "DIRECT_MESSAGE_CREATE": self._direct_message,
            "GROUP_ADD_ROBOT": self._group_add_robot,
            "GROUP_DEL_ROBOT": self._group_del_robot,
            "GROUP_JOIN_REQUEST": self._group_join_request,
            "FRIEND_ADD": self._friend_add,
            "INTERACTION_CREATE": self._interaction,
        }.get(qq_type)

        if handler:
            event = handler(d, protocol)
            if event is not None:
                self._maybe_store(qq_type, d)
            return event

        # 其余事件 -> q2oex（若启用）
        if self.q2oex_enable:
            return build_q2oex_event(qq_type, d, protocol, self.self_id)
        log.debug("忽略 QQ 事件 %s（q2oex 未启用且无标准映射）", qq_type)
        return None

    def _self_id_int(self) -> int:
        return int(self.self_id) if str(self.self_id).isdigit() else 0

    # ------------------------------------------------------------- 消息缓存
    def _maybe_store(self, qq_type: str, d: dict[str, Any]) -> None:
        """把入站消息写入 MessageStore（OB11 形态），支撑 get_msg 动作。"""
        if self.store is None:
            return
        if qq_type in ("GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE"):
            mtype, target = "group", d.get("group_openid", "")
        elif qq_type == "C2C_MESSAGE_CREATE":
            mtype, target = "private", ""
        else:
            return
        author = d.get("author") or {}
        openid = (author.get("member_openid") or author.get("user_openid")
                  or author.get("id") or author.get("union_openid") or "")
        qq_mid = d.get("id", "")
        mid = self.idmap.message_id(qq_mid)
        record = {
            "time": _parse_ts(d.get("timestamp")),
            "message_type": mtype,
            "message_id": mid,
            "real_id": mid,
            "user_id": self.idmap.register(openid) if openid else 0,
            "group_id": self.idmap.register(target) if target else 0,
            "sender": {"user_id": self.idmap.register(openid) if openid else 0,
                       "nickname": openid[:16]},
            "message": content_to_cq(_effective_content(d),
                                     d.get("attachments"), self.idmap),
        }
        record["raw_message"] = record["message"]
        self.store.save(mid, record)

    # ------------------------------------------------------------- 群消息
    def _group_message(self, d: dict, protocol: int,
                       event_id: str = "") -> dict:
        group_openid = d.get("group_openid", "")
        author = d.get("author") or {}
        openid = author.get("member_openid") or author.get("union_openid", "")
        msg_id = d.get("id", "")
        ts = _parse_ts(d.get("timestamp"))
        self.ctx.update(group_openid, msg_id, event_id, ts)
        group_id = self.idmap.register(group_openid)
        user_id = self.idmap.register(openid)
        content = _effective_content(d)

        if protocol == 12:
            return {
                "id": msg_id, "time": ts, "type": "message",
                "detail_type": "group", "sub_type": "",
                "message_id": str(self.idmap.message_id(msg_id)),
                "message": content_to_ob12_segments(content,
                                                    d.get("attachments"),
                                                    self.idmap),
                "alt_message": content_to_cq(content,
                                             d.get("attachments"),
                                             self.idmap),
                "user_id": str(user_id), "group_id": str(group_id),
                "self": {"platform": "qq", "user_id": str(self.self_id)},
                "qq.group_openid": group_openid, "qq.openid": openid,
                **_qq_message_meta(d),
            }
        cq = content_to_cq(content, d.get("attachments"), self.idmap)
        return {
            "time": ts,
            "self_id": self._self_id_int(),
            "post_type": "message", "message_type": "group", "sub_type": "normal",
            "message_id": self.idmap.message_id(msg_id),
            "group_id": group_id, "user_id": user_id,
            "anonymous": None,
            "message": cq, "raw_message": cq,
            "font": 0,
            "sender": {"user_id": user_id, "nickname": openid[:16],
                       "card": "", "sex": "unknown", "age": 0,
                       "area": "", "level": "0", "role": "member", "title": ""},
            **_qq_message_meta(d),
        }

    # ------------------------------------------------------------- C2C 消息
    def _c2c_message(self, d: dict, protocol: int,
                     event_id: str = "") -> dict:
        author = d.get("author") or {}
        openid = author.get("id") or author.get("user_openid") or author.get("union_openid", "")
        msg_id = d.get("id", "")
        ts = _parse_ts(d.get("timestamp"))
        self.ctx.update(openid, msg_id, event_id, ts)
        user_id = self.idmap.register(openid)
        content = _effective_content(d)

        if protocol == 12:
            return {
                "id": msg_id, "time": ts, "type": "message",
                "detail_type": "private", "sub_type": "",
                "message_id": str(self.idmap.message_id(msg_id)),
                "message": content_to_ob12_segments(content,
                                                    d.get("attachments"),
                                                    self.idmap),
                "alt_message": content_to_cq(content,
                                             d.get("attachments"),
                                             self.idmap),
                "user_id": str(user_id),
                "self": {"platform": "qq", "user_id": str(self.self_id)},
                "qq.openid": openid,
                **_qq_message_meta(d),
            }
        cq = content_to_cq(content, d.get("attachments"), self.idmap)
        return {
            "time": ts,
            "self_id": self._self_id_int(),
            "post_type": "message", "message_type": "private",
            "sub_type": "friend",
            "message_id": self.idmap.message_id(msg_id),
            "user_id": user_id,
            "message": cq, "raw_message": cq, "font": 0,
            "sender": {"user_id": user_id, "nickname": openid[:16],
                       "sex": "unknown", "age": 0},
            **_qq_message_meta(d),
        }

    # ------------------------------------------------------------- 频道消息 -> q2oex
    def _channel_message(self, d: dict, protocol: int,
                         event_id: str = "") -> dict | None:
        if not self.q2oex_enable:
            return None
        channel_id = d.get("channel_id", "")
        ts = _parse_ts(d.get("timestamp"))
        self.ctx.update(channel_id, d.get("id", ""), event_id, ts)
        fields = {
            "qq_event": "CHANNEL_MESSAGE",
            "guild_id": d.get("guild_id", ""),
            "channel_id": d.get("channel_id", ""),
            "message_id": d.get("id", ""),
            "author": d.get("author", {}),
            "content": d.get("content", ""),
            "attachments": d.get("attachments", []),
            "data": d,
        }
        if protocol == 12:
            return wrap_ob12("channel_message", self.self_id, **fields)
        return wrap_ob11("channel_message", self.self_id, **fields)

    def _direct_message(self, d: dict, protocol: int,
                        event_id: str = "") -> dict | None:
        if not self.q2oex_enable:
            return None
        channel_id = d.get("channel_id", "")
        ts = _parse_ts(d.get("timestamp"))
        self.ctx.update(channel_id, d.get("id", ""), event_id, ts)
        fields = {
            "qq_event": "DIRECT_MESSAGE",
            "guild_id": d.get("guild_id", ""),
            "channel_id": d.get("channel_id", ""),
            "message_id": d.get("id", ""),
            "author": d.get("author", {}),
            "content": d.get("content", ""),
            "data": d,
        }
        if protocol == 12:
            return wrap_ob12("direct_message", self.self_id, **fields)
        return wrap_ob11("direct_message", self.self_id, **fields)

    # ------------------------------------------------------------- 群成员/机器人事件
    def _group_add_robot(self, d: dict, protocol: int) -> dict:
        group_id = self.idmap.register(d.get("group_openid", ""))
        op_id = self.idmap.register(d.get("op_member_openid", ""))
        ts = _parse_ts(d.get("timestamp"))
        if protocol == 12:
            return {
                "id": f"evt-{time.time_ns()}", "time": ts, "type": "notice",
                "detail_type": "group_member_increase", "sub_type": "invite",
                "group_id": str(group_id), "user_id": str(self.self_id),
                "operator_id": str(op_id),
                "self": {"platform": "qq", "user_id": str(self.self_id)},
            }
        return {
            "time": ts,
            "self_id": self._self_id_int(),
            "post_type": "notice", "notice_type": "group_increase",
            "sub_type": "invite", "group_id": group_id,
            "operator_id": op_id, "user_id": self._self_id_int(),
        }

    def _group_del_robot(self, d: dict, protocol: int) -> dict:
        group_id = self.idmap.register(d.get("group_openid", ""))
        op_id = self.idmap.register(d.get("op_member_openid", ""))
        ts = _parse_ts(d.get("timestamp"))
        if protocol == 12:
            return {
                "id": f"evt-{time.time_ns()}", "time": ts, "type": "notice",
                "detail_type": "group_member_decrease", "sub_type": "kick",
                "group_id": str(group_id), "user_id": str(self.self_id),
                "operator_id": str(op_id),
                "self": {"platform": "qq", "user_id": str(self.self_id)},
            }
        return {
            "time": ts,
            "self_id": self._self_id_int(),
            "post_type": "notice", "notice_type": "group_decrease",
            "sub_type": "kick_me", "group_id": group_id,
            "operator_id": op_id, "user_id": self._self_id_int(),
        }

    # ------------------------------------------------------------- 入群申请审批
    def _group_join_request(self, d: dict, protocol: int) -> dict:
        """GROUP_JOIN_REQUEST -> OneBot request 事件（携带 flag，供审批动作回传）。

        flag 直接使用官方 join_request_id（不透明字符串，随事件透传）；
        同时登记 flag -> (group_openid, member_openid) 供 set_group_add_request 反查。
        """
        group_openid = d.get("group_openid", "")
        member_openid = d.get("member_openid", "")
        join_request_id = d.get("join_request_id", "")
        invited_by = d.get("invited_by", "")
        apply_source = d.get("apply_source", "self_apply")

        group_id = self.idmap.register(group_openid)
        user_id = self.idmap.register(member_openid)
        operator_id = self.idmap.register(invited_by) if invited_by else 0
        ts = _parse_ts(d.get("apply_at"))

        verify_info = d.get("verify_info") or {}
        comment = verify_info.get("verify_message") or ""
        for qa in verify_info.get("review_qa_list") or []:
            q = qa.get("question", "")
            a = qa.get("answer", "")
            comment = f"{comment}\nQ: {q}\nA: {a}".strip()

        self.join_ctx.put(join_request_id, group_openid, member_openid,
                          join_request_id)

        if protocol == 12:
            return {
                "id": f"evt-{time.time_ns()}", "time": ts, "type": "request",
                "detail_type": "group_join_request", "sub_type": "",
                "flag": join_request_id,
                "group_id": str(group_id), "user_id": str(user_id),
                "comment": comment,
                "qq.apply_source": apply_source,
                "qq.username": d.get("username", ""),
                "qq.invited_by_openid": invited_by,
                "qq.auto_approved": d.get("auto_approved"),
                "self": {"platform": "qq", "user_id": str(self.self_id)},
            }
        return {
            "time": ts,
            "self_id": self._self_id_int(),
            "post_type": "request", "request_type": "group",
            "sub_type": "invite" if apply_source == "invited" else "add",
            "group_id": group_id, "user_id": user_id,
            "operator_id": operator_id,
            "comment": comment,
            "flag": join_request_id,
        }

    def _friend_add(self, d: dict, protocol: int) -> dict:
        user_id = self.idmap.register(d.get("openid", ""))
        ts = _parse_ts(d.get("timestamp"))
        if protocol == 12:
            return {
                "id": f"evt-{time.time_ns()}", "time": ts, "type": "notice",
                "detail_type": "friend_increase", "sub_type": "",
                "user_id": str(user_id),
                "self": {"platform": "qq", "user_id": str(self.self_id)},
            }
        return {
            "time": ts,
            "self_id": self._self_id_int(),
            "post_type": "notice", "notice_type": "friend_add",
            "user_id": user_id,
        }

    def _interaction(self, d: dict, protocol: int,
                     event_id: str = "") -> dict | None:
        if not self.q2oex_enable:
            return None
        resolved = (d.get("data") or {}).get("resolved") or {}
        interaction_id = str(d.get("id") or event_id or "")

        # 互动事件本身也可作为 event_id 被动回复窗口；按场景登记上下文。
        target = (d.get("group_openid") or d.get("user_openid")
                  or d.get("channel_id") or "")
        ts = _parse_ts(d.get("timestamp"))
        # 互动事件的可靠被动标识是外层事件 id；resolved.message_id 仅作业务字段。
        self.ctx.update(target, "", interaction_id, ts)

        fields = {
            "qq_event": "INTERACTION_CREATE", "data": d,
            "interaction_id": interaction_id,
            "interaction_type": d.get("type"),
            "scene": d.get("scene", ""),
            "guild_id": d.get("guild_id", ""),
            "channel_id": d.get("channel_id", ""),
            "group_openid": d.get("group_openid", ""),
            "user_openid": d.get("user_openid", ""),
            "group_member_openid": d.get("group_member_openid", ""),
            "chat_type": d.get("chat_type"),
            "button_id": resolved.get("button_id", ""),
            "button_data": resolved.get("button_data", ""),
            "message_id": resolved.get("message_id", ""),
            "feedback_opt": resolved.get("feedback_opt", ""),
            "action": resolved.get("action", ""),
        }
        if protocol == 12:
            return wrap_ob12("interaction_create", self.self_id, **fields)
        return wrap_ob11("interaction_create", self.self_id, **fields)
