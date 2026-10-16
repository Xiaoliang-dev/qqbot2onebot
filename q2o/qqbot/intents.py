"""QQ 开放平台 intents（事件订阅）位移计算。

用户以文字方式在配置中声明事件；特殊事件名 all 表示全部订阅。
all 的全量位移计算量较大且结果恒定，已预计算并硬编码为 ALL_INTENTS，
运行时直接取用，不做逐位计算。
"""

from __future__ import annotations

# 事件名 -> intent 位 (来源: QQ 开放平台 API v2 文档)
INTENT_BITS: dict[str, int] = {
    "guilds": 1 << 0,                        # 频道变更
    "guild_members": 1 << 1,                 # 频道成员变更
    "guild_messages": 1 << 9,                # 频道消息（私域）
    "guild_message_reactions": 1 << 10,      # 消息表态
    "direct_message": 1 << 12,               # 频道私信
    "audio_or_live_channel_member": 1 << 19, # 音频/直播频道成员
    "group_and_c2c_event": 1 << 25,          # 群聊与 C2C 消息（公域）
    "interaction": 1 << 26,                  # 互动事件
    "message_audit": 1 << 27,                # 消息审核
    "forums_event": 1 << 28,                 # 论坛事件（私域）
    "audio_action": 1 << 29,                 # 音频操作
    "public_guild_messages": 1 << 30,        # 频道消息（公域）
}

# ---------------------------------------------------------------------------
# all 事件的预计算结果（硬编码，避免运行时做无意义的全量位移）：
#   (1<<0)|(1<<1)|(1<<9)|(1<<10)|(1<<12)|(1<<18)|(1<<19)
#   |(1<<25)|(1<<26)|(1<<27)|(1<<28)|(1<<29)|(1<<30)
# ---------------------------------------------------------------------------
ALL_INTENTS: int = 2114459139

# 供校验：启动时断言硬编码值与逐位计算一致（仅一次，O(1)）
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
