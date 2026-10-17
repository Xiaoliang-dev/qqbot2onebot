"""QQ 开放平台 intents（事件订阅）位移计算。

用户以文字方式在配置中声明事件；特殊事件名 all 表示全部订阅。
all 的全量位移计算量较大且结果恒定，已预计算并硬编码为 ALL_INTENTS，
运行时直接取用，不做逐位计算。
"""

# by:xiaoliang - 2026.10.17

from __future__ import annotations

INTENT_BITS: dict[str, int] = {
    "guilds": 1 << 0,
    "guild_members": 1 << 1,
    "guild_messages": 1 << 9,
    "guild_message_reactions": 1 << 10,
    "direct_message": 1 << 12,
    "audio_or_live_channel_member": 1 << 19,
    "group_and_c2c_event": 1 << 25,
    "interaction": 1 << 26,
    "message_audit": 1 << 27,
    "forums_event": 1 << 28,
    "audio_action": 1 << 29,
    "public_guild_messages": 1 << 30,
}

ALL_INTENTS: int = 2114459139

assert ALL_INTENTS == (
    (1 << 0) | (1 << 1) | (1 << 9) | (1 << 10) | (1 << 12)
    | (1 << 19) | (1 << 25) | (1 << 26) | (1 << 27) | (1 << 28)
    | (1 << 29) | (1 << 30)
)


def compute_intents(events: list[str]) -> int:
    """把文字事件列表转成 intents 整数。包含 all 时直接返回硬编码值。"""
    lowered = {e.strip().lower() for e in events}
    if "all" in lowered:
        return ALL_INTENTS
    intents = 0
    unknown = []
    for name in lowered:
        bit = INTENT_BITS.get(name)
        if bit is None:
            unknown.append(name)
        else:
            intents |= bit
    if unknown:
        raise ValueError(
            f"未知事件名: {', '.join(unknown)}；可用: all, "
            + ", ".join(sorted(INTENT_BITS))
        )
    return intents
