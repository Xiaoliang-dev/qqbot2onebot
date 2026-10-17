"""消息上下文与近期消息缓存。

- MessageContext: 记录每个会话最近可被动回复的 QQ 消息/事件标识。
  发送动作可显式传入 msg_id/event_id，也可设置 passive=true 让程序自动
  取最近上下文；否则按 QQ 官方现行接口发送主动消息（不传 msg_id/event_id）。
- MessageStore: 以 OneBot 数字 message_id 为键缓存近期入站消息，
  支撑 get_msg 动作（LRU，默认 1000 条）。
"""

# by:xiaoliang - 2026.10.17

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any


PASSIVE_LIMITS: dict[str, dict[str, int]] = {
    "group": {"ttl": 5 * 60, "max_replies": 5},
    "private": {"ttl": 5 * 60, "max_replies": 4},
}


@dataclass(frozen=True)
class PassiveRef:
    """一次发送使用的被动回复引用。msg_id/event_id 均为空表示主动消息。"""

    msg_id: str | None = None
    event_id: str | None = None
    msg_seq: int | None = None
    active_reason: str | None = None

    @property
    def is_passive(self) -> bool:
        return bool(self.msg_id or self.event_id)


@dataclass
class _ContextEntry:
    msg_id: str = ""
    event_id: str = ""
    created_at: float = 0.0
    msg_seq: int = 0
    replies: int = 0


class MessageContext:
    """按会话记录最近入站事件，供被动回复引用和 msg_seq 递增。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last: dict[str, _ContextEntry] = {}

    def update(self, target: str, msg_id: str = "",
               event_id: str = "", timestamp: float | None = None) -> None:
        """登记一次新的入站消息/事件；同一目标的新事件会重置被动计数。"""
        if not target:
            return
        with self._lock:
            self._last[target] = _ContextEntry(
                msg_id=str(msg_id or ""),
                event_id=str(event_id or ""),
                created_at=timestamp if timestamp is not None else time.time(),
            )

    def next_ref(self, target: str, kind: str = "group", *,
                 msg_id: str | None = None,
                 event_id: str | None = None,
                 msg_seq: int | None = None,
                 passive: bool = False) -> PassiveRef:
        """构造被动引用。

        优先级：
        1. 显式 msg_id；
        2. 显式 event_id；
        3. passive=true 时使用该会话最近上下文；
        4. 其余情况返回主动消息（两个 ID 均为空）。
        """
        explicit_msg_id = str(msg_id or "") or None
        explicit_event_id = str(event_id or "") or None
        if explicit_msg_id and explicit_event_id:
            raise ValueError("msg_id 与 event_id 只能二选一")

        if explicit_msg_id or explicit_event_id:
            return PassiveRef(msg_id=explicit_msg_id,
                              event_id=explicit_event_id,
                              msg_seq=msg_seq)

        if not passive:
            return PassiveRef(active_reason="未请求被动回复，按主动消息发送")

        with self._lock:
            entry = self._last.get(target)
            if entry is None:
                return PassiveRef(active_reason="该会话没有可被动回复的上下文")
            now = time.time()
            limits = PASSIVE_LIMITS.get(kind, PASSIVE_LIMITS["group"])
            if now - entry.created_at > limits["ttl"]:
                return PassiveRef(active_reason="被动回复窗口已过期，降级为主动消息")
            if entry.replies >= limits["max_replies"]:
                return PassiveRef(active_reason="被动回复次数已达上限，降级为主动消息")

            entry.replies += 1
            entry.msg_seq += 1
            if entry.msg_id:
                return PassiveRef(msg_id=entry.msg_id,
                                  msg_seq=msg_seq or entry.msg_seq)
            if entry.event_id:
                return PassiveRef(event_id=entry.event_id,
                                  msg_seq=msg_seq or entry.msg_seq)
            return PassiveRef(active_reason="上下文中没有 msg_id/event_id")


class MessageStore:
    """近期消息缓存，键为 OneBot int32 message_id。"""

    def __init__(self, capacity: int = 1000):
        self._capacity = capacity
        self._lock = threading.Lock()
        self._data: OrderedDict[int, dict[str, Any]] = OrderedDict()

    def save(self, mid: int, record: dict[str, Any]) -> None:
        with self._lock:
            self._data[mid] = record
            self._data.move_to_end(mid)
            while len(self._data) > self._capacity:
                self._data.popitem(last=False)

    def get(self, mid: int) -> dict[str, Any] | None:
        with self._lock:
            return self._data.get(int(mid))


class JoinRequestContext:
    """入群申请审批上下文：flag -> (group_openid, member_openid, join_request_id)。

    QQ 官方要求审批时回传事件下发的 join_request_id，且一次申请只能审批一次，
    因此 get 采用一次性取出（pop）语义。
    """

    def __init__(self, capacity: int = 500):
        self._capacity = capacity
        self._lock = threading.Lock()
        self._data: OrderedDict[str, tuple[str, str, str]] = OrderedDict()

    def put(self, flag: str, group_openid: str, member_openid: str,
            join_request_id: str) -> None:
        with self._lock:
            self._data[flag] = (group_openid, member_openid, join_request_id)
            self._data.move_to_end(flag)
            while len(self._data) > self._capacity:
                self._data.popitem(last=False)

    def get(self, flag: str) -> tuple[str, str, str] | None:
        """一次性取出（审批后 flag 即失效）。"""
        with self._lock:
            return self._data.pop(flag, None)
