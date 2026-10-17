"""配置系统：加载、校验、自生成详尽默认配置。

首次启动时若 config.yaml 不存在，会自动写入一份带完整注释的默认配置并退出，
引导用户填写 app_id / client_secret 后再次启动。
"""

# by:xiaoliang - 2026.10.17

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = "config.yaml"

DEFAULT_CONFIG_TEMPLATE = """\
# =============================================================================
# qqbot2onebot (q2o) 配置文件
# QQ 官方 Bot 协议 (QQ开放平台 API v2)  ->  OneBot 11 / OneBot 12(实验性) 协议转换
# 文档: https://bot.q.qq.com/wiki/develop/api-v2/
# =============================================================================

# ============================ QQBot（上游，QQ 官方协议） ============================
qqbot:
  # 机器人 appid 与 client_secret，于 QQ 开放平台管理端获取
  app_id: ""
  client_secret: ""

  # 是否使用沙箱环境（仅沙箱频道/群可用）
  sandbox: false

  # 事件接入方式: websocket | webhook
  #   websocket: 通过 GET /gateway/bot 获取 wss 接入点后长连接收事件
  #   webhook:   QQ 平台通过 HTTP 回调推送事件（需要公网可达地址，且须在
  #              QQ 开放平台配置回调地址并完成 Ed25519 验签）
  ingress: websocket

  # ---- webhook 方式的监听设置（ingress: webhook 时生效）----
  webhook:
    host: 0.0.0.0
    port: 9000
    path: /qq/webhook

  # ---- 事件订阅（intents），以文字方式书写 ----
  # 特殊值 all：订阅全部事件。程序已预计算全量位移并硬编码，运行时零开销。
  # 可选事件名（区分大小写，来自 QQ 开放平台 intents 定义）:
  #   guilds                    频道变更
  #   guild_members             频道成员变更
  #   guild_messages            频道消息(私域, 须私域权限)
  #   guild_message_reactions   频道消息表态
  #   direct_message            频道私信
  #   group_and_c2c_event       群聊与C2C消息(公域, 推荐)
  #   interaction               互动事件(按钮/回执)
  #   message_audit             消息审核
  #   forums_event              论坛事件(私域)
  #   open_forums_event         论坛事件(公域)
  #   audio_action              音频操作
  #   audio_or_live_channel_member  音频/直播频道成员
  #   public_guild_messages     频道消息(公域)
  events:
    - all

  # ---- 请求切片(sharding) ----
  # QQ 官方要求大规模机器人按切片建立多条连接，切片总数须与网关建议匹配。
  sharding:
    enable: true
    # 切片数量。特殊值 0 表示 auto：综合「GET /gateway/bot 返回的建议切片数」
    # (权重 80%) 与本机网络/CPU/内存状况(权重 20%) 自动决策。
    count: 0

  # ---- WebSocket 断连补发（Resume）行为 ----
  # QQ 网关在断线重连后可通过 Opcode 6 (Resume) 补发断连期间遗漏的事件，
  # 补发以 RESUMED 事件宣告结束。
  resume:
    # 是否把补发期间收到的事件转发给 OneBot 侧。
    #   true (默认): 补发事件照常转发（可能造成业务端重复/乱序处理）。
    #   false:       补发事件仅在本侧接收并照常记录 s 序号，不转发给 OneBot，
    #                直到服务端下发 RESUMED 事件后恢复正常转发。
    forward_replayed_events: true

  # ---- 消息发送策略（QQ 官方现行接口：msg_id/event_id 均为可选）----
  message:
    # 标准 OneBot 发送动作未显式传 msg_id/event_id、也没有 reply 段时，是否默认
    # 使用最近收到的上下文做被动回复。
    #   false (默认): 主动推送（不传 msg_id/event_id，受主动消息权限/频控约束）；
    #                 单次调用可传 passive=true 改为被动回复。
    #   true:         优先使用最近上下文做被动回复；过期/超限后自动降级为主动推送。
    passive_by_default: false

  # QQ API 请求超时（秒）
  http_timeout: 15

# ============================ OneBot（下游） ============================
onebot:
  # 机器人自身 QQ 号 / 标识（无法从 QQ 开放平台直接获得 bot QQ 号，
  # 用于填充 OneBot 事件的 self_id，请手动填写）
  self_id: "0"

  # ---- openid 翻译（虚拟号对照表） ----
  # QQ 官方协议使用 openid（字符串）标识用户/群，而很多 OneBot 生态
  # 程序不支持 openid（甚至把 user_id 当 QQ 号做格式校验）。
  #   true (默认): 程序内部维护一份「openid <-> 虚拟号」对照表，分配给每个
  #                openid 一个符合 QQ 号规则的虚拟号（纯数字、首位非 0、5~11 位），
  #                下游程序看到的是虚拟号；虚拟号可反向翻译回 openid。
  #                对照表持久化在 data/idmap.json，重启后继续有效。
  #   false:      不进行翻译，使用 blake2b 稳定哈希生成的 63 位数字 ID（旧行为）。
  openid_translate: true

  # 向 OneBot 应用上报事件时的协议版本: 11 或 12(实验性)
  # 注意: 每条连接(connection)上可单独覆盖该值，实现 11/12 混用
  protocol: 11

  # ---- 连接列表：可配置任意多条、四种类型混用 ----
  # type 可选:
  #   http_server  正向 HTTP(S): 本程序作为 HTTP 服务端，接收 OneBot 应用的 API 调用
  #   http_post    反向 HTTP(S) Webhook: 本程序把事件 POST 到指定 URL
  #   ws_server    正向 WebSocket: 本程序作为 WS 服务端，应用连入收事件/发 API
  #   ws_reverse   反向 WebSocket: 本程序作为 WS 客户端，主动连到应用的 WS 服务端
  connections:
    - type: http_server
      protocol: 11          # 11 | 12，缺省继承上方 onebot.protocol
      enable: true
      host: 0.0.0.0
      port: 5700
      access_token: ""      # 留空则不校验
      # 正向 HTTPS（可选）：填写证书与私钥路径即启用 TLS
      # tls_cert: /path/to/fullchain.pem
      # tls_key:  /path/to/privkey.pem

    - type: http_post
      protocol: 11
      enable: false
      url: "http://127.0.0.1:5701/"
      secret: ""            # 计算 X-Signature (HMAC-SHA1) 用，留空不签名
      timeout: 5            # 上报超时（秒）

    - type: ws_server
      protocol: 11
      enable: true
      host: 0.0.0.0
      port: 6700
      access_token: ""
      # tls_cert: /path/to/fullchain.pem
      # tls_key:  /path/to/privkey.pem

    - type: ws_reverse
      protocol: 11
      enable: false
      url: "ws://127.0.0.1:8080/onebot/v11/ws"
      access_token: ""
      reconnect_interval: 3  # 反向 WS 断线重连间隔（秒）

# ============================ Q2OEX 扩展协议 ============================
# QQ 官方协议存在大量 OneBot 未定义的能力（频道/帖子/公告/日程/表情表态等），
# 这些能力通过 q2oEX 扩展协议补全，定义见 Q2OEX.md。
q2oex:
  enable: true        # 关闭后不产生/不处理任何 q2oex.* 事件与动作

# ============================ 日志 ============================
logging:
  # DEBUG | INFO | WARNING | ERROR
  level: INFO
  # 日志文件（控制台带彩色输出，文件内不含任何颜色控制符）
  file: logs/q2o.log
  # 单个日志文件最大大小(MB)，超出后轮转
  max_size_mb: 10
  # 轮转保留份数
  backup_count: 5
  # QQ 官方返回的错误码按严重程度映射日志级别，此处为提升阈值:
  #   warn  -> 所有可恢复错误只记 WARNING
  #   error -> 同上，但致命错误记 ERROR（默认行为）
  qq_error_min_level: warn
"""


@dataclass
class QQBotWebhookConfig:
    host: str = "0.0.0.0"
    port: int = 9000
    path: str = "/qq/webhook"


@dataclass
class QQBotShardingConfig:
    enable: bool = True
    count: int = 0


@dataclass
class QQBotResumeConfig:
    forward_replayed_events: bool = True


@dataclass
class QQBotMessageConfig:
    passive_by_default: bool = False


@dataclass
class QQBotConfig:
    app_id: str = ""
    client_secret: str = ""
    sandbox: bool = False
    ingress: str = "websocket"
    webhook: QQBotWebhookConfig = field(default_factory=QQBotWebhookConfig)
    events: list[str] = field(default_factory=lambda: ["all"])
    sharding: QQBotShardingConfig = field(default_factory=QQBotShardingConfig)
    resume: QQBotResumeConfig = field(default_factory=QQBotResumeConfig)
    message: QQBotMessageConfig = field(default_factory=QQBotMessageConfig)
    http_timeout: int = 15


@dataclass
class ConnectionConfig:
    type: str
    protocol: int = 11
    enable: bool = True
    raw: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)


@dataclass
class OneBotConfig:
    self_id: str = "0"
    protocol: int = 11
    openid_translate: bool = True
    connections: list[ConnectionConfig] = field(default_factory=list)


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str = "logs/q2o.log"
    max_size_mb: int = 10
    backup_count: int = 5
    qq_error_min_level: str = "warn"


@dataclass
class Config:
    qqbot: QQBotConfig = field(default_factory=QQBotConfig)
    onebot: OneBotConfig = field(default_factory=OneBotConfig)
    q2oex_enable: bool = True
    logging: LoggingConfig = field(default_factory=LoggingConfig)


class ConfigError(Exception):
    pass


def _dict_to_config(data: dict[str, Any]) -> Config:
    cfg = Config()
    q = data.get("qqbot", {}) or {}
    cfg.qqbot = QQBotConfig(
        app_id=str(q.get("app_id", "")),
        client_secret=str(q.get("client_secret", "")),
        sandbox=bool(q.get("sandbox", False)),
        ingress=str(q.get("ingress", "websocket")).lower(),
        webhook=QQBotWebhookConfig(**{
            k: v for k, v in (q.get("webhook") or {}).items()
            if k in ("host", "port", "path")
        }),
        events=[str(e) for e in (q.get("events") or ["all"])],
        sharding=QQBotShardingConfig(**{
            k: v for k, v in (q.get("sharding") or {}).items()
            if k in ("enable", "count")
        }),
        resume=QQBotResumeConfig(**{
            k: v for k, v in (q.get("resume") or {}).items()
            if k in ("forward_replayed_events",)
        }),
        message=QQBotMessageConfig(**{
            k: v for k, v in (q.get("message") or {}).items()
            if k in ("passive_by_default",)
        }),
        http_timeout=int(q.get("http_timeout", 15)),
    )

    o = data.get("onebot", {}) or {}
    conns: list[ConnectionConfig] = []
    default_proto = int(o.get("protocol", 11))
    for c in o.get("connections") or []:
        c = dict(c)
        conns.append(ConnectionConfig(
            type=str(c.get("type", "")),
            protocol=int(c.get("protocol", default_proto)),
            enable=bool(c.get("enable", True)),
            raw=c,
        ))
    cfg.onebot = OneBotConfig(
        self_id=str(o.get("self_id", "0")),
        protocol=default_proto,
        openid_translate=bool(o.get("openid_translate", True)),
        connections=conns,
    )

    cfg.q2oex_enable = bool((data.get("q2oex") or {}).get("enable", True))

    lg = data.get("logging", {}) or {}
    cfg.logging = LoggingConfig(
        level=str(lg.get("level", "INFO")).upper(),
        file=str(lg.get("file", "logs/q2o.log")),
        max_size_mb=int(lg.get("max_size_mb", 10)),
        backup_count=int(lg.get("backup_count", 5)),
        qq_error_min_level=str(lg.get("qq_error_min_level", "warn")).lower(),
    )
    return cfg


def _validate(cfg: Config) -> None:
    if not cfg.qqbot.app_id or not cfg.qqbot.client_secret:
        raise ConfigError("qqbot.app_id 与 qqbot.client_secret 未填写，请编辑 config.yaml")
    if cfg.qqbot.ingress not in ("websocket", "webhook"):
        raise ConfigError(f"qqbot.ingress 仅支持 websocket|webhook，当前: {cfg.qqbot.ingress}")
    if cfg.qqbot.sharding.count < 0:
        raise ConfigError("qqbot.sharding.count 不能为负数（0 表示 auto）")
    for c in cfg.onebot.connections:
        if c.type not in ("http_server", "http_post", "ws_server", "ws_reverse"):
            raise ConfigError(f"未知 onebot 连接类型: {c.type}")
        if c.protocol not in (11, 12):
            raise ConfigError(f"onebot protocol 仅支持 11|12: {c.protocol}")


def load_config(path: str = DEFAULT_CONFIG_PATH) -> Config:
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(DEFAULT_CONFIG_TEMPLATE)
        print(f"[q2o] 已生成默认配置文件 {path}，请填写 qqbot.app_id / client_secret 后重新启动。")
        sys.exit(0)

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    cfg = _dict_to_config(data)
    _validate(cfg)
    return cfg
