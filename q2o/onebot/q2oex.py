"""Q2OEX 扩展协议实现：补全 OneBot 11/12 未覆盖的 QQ 官方能力。

完整协议定义见项目根目录 Q2OEX.md。

事件命名约定:
  - OneBot 11: post_type="q2oex", q2oex_type=<事件名>, 其余字段平铺
  - OneBot 12: type="q2oex.<事件名>" 风格 detail_type，均带 q2oex 命名空间数据
动作命名约定: q2oex.<动作名>
"""

# by:xiaoliang - 2026.10.17

from __future__ import annotations

import time
from typing import Any, Callable


def wrap_ob11(event_name: str, self_id: str, **fields) -> dict[str, Any]:
    return {
        "time": int(time.time()),
        "self_id": int(self_id) if str(self_id).isdigit() else self_id,
        "post_type": "q2oex",
        "q2oex_type": event_name,
        **fields,
    }


def wrap_ob12(event_name: str, self_id: str, **fields) -> dict[str, Any]:
    return {
        "id": f"q2oex-{time.time_ns()}",
        "time": time.time(),
        "type": "q2oex",
        "detail_type": event_name,
        "sub_type": "",
        "self": {"platform": "qq", "user_id": str(self_id)},
        **fields,
    }


Q2OEX_EVENT_MAP: dict[str, str] = {
    "GUILD_CREATE": "guild_create",
    "GUILD_UPDATE": "guild_update",
    "GUILD_DELETE": "guild_delete",
    "CHANNEL_CREATE": "channel_create",
    "CHANNEL_UPDATE": "channel_update",
    "CHANNEL_DELETE": "channel_delete",
    "GUILD_MEMBER_ADD": "guild_member_add",
    "GUILD_MEMBER_UPDATE": "guild_member_update",
    "GUILD_MEMBER_REMOVE": "guild_member_remove",
    "MESSAGE_AUDIT_PASS": "message_audit_pass",
    "MESSAGE_AUDIT_REJECT": "message_audit_reject",
    "MESSAGE_REACTION_ADD": "message_reaction_add",
    "MESSAGE_REACTION_REMOVE": "message_reaction_remove",
    "THREAD_CREATE": "thread_create",
    "THREAD_UPDATE": "thread_update",
    "THREAD_DELETE": "thread_delete",
    "POST_CREATE": "post_create",
    "POST_DELETE": "post_delete",
    "REPLY_CREATE": "reply_create",
    "REPLY_DELETE": "reply_delete",
    "FORUM_PUBLISH_AUDIT_RESULT": "forum_publish_audit_result",
    "AUDIO_START": "audio_start",
    "AUDIO_FINISH": "audio_finish",
    "AUDIO_ON_MIC": "audio_on_mic",
    "AUDIO_OFF_MIC": "audio_off_mic",
    "INTERACTION_CREATE": "interaction_create",
    "FRIEND_DEL": "friend_del",
    "C2C_MSG_REJECT": "c2c_msg_reject",
    "C2C_MSG_RECEIVE": "c2c_msg_receive",
    "GROUP_MSG_REJECT": "group_msg_reject",
    "GROUP_MSG_RECEIVE": "group_msg_receive",
}


def build_q2oex_event(qq_type: str, data: dict[str, Any], protocol: int,
                      self_id: str) -> dict[str, Any] | None:
    """把无法映射到标准 OneBot 的 QQ 事件包装为 q2oex 事件。"""
    name = Q2OEX_EVENT_MAP.get(qq_type, qq_type.lower())
    fields = {"qq_event": qq_type, "data": data}
    if protocol == 12:
        return wrap_ob12(name, self_id, **fields)
    return wrap_ob11(name, self_id, **fields)
