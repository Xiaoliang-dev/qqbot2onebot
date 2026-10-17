# qqbot2onebot (q2o)

**QQ 官方 Bot 协议（QQ 开放平台 API v2）→ OneBot 11 / OneBot 12（实验性）协议转换器。**

让只支持 OneBot 生态的机器人框架/应用（如各类 OneBot SDK、插件平台）可以无缝接入 QQ 官方 Bot 接口，同时通过内置的 **Q2OEX 扩展协议** 补全 OneBot 未覆盖的 QQ 官方能力（频道、帖子、互动按钮、Markdown、表情表态等，详见 [Q2OEX.md](Q2OEX.md)）。

当前版本：**1.1.0**（按 QQ 开放平台 API v2 现行文档更新消息发送、主动推送、Markdown、按钮与互动回应）。

---

## 功能总览

### 上游：QQ 官方 Bot 协议

| 能力 | 说明 |
| --- | --- |
| 双接入方式 | `websocket`（GET /gateway/bot 获取 wss 接入点后长连接）或 `webhook`（HTTP 回调，Ed25519 验签 + op13 回调地址验证） |
| 文字方式订阅事件 | 配置中直接写事件名（如 `group_and_c2c_event`）；特殊事件 `all` 订阅全部，全量位移结果已**预计算硬编码**（`ALL_INTENTS = 2114721283`），运行时零开销，并在启动时自动断言校验 |
| 请求切片（sharding） | 支持关闭 / 显式数量 / `auto`（count=0）。auto 模式下按 **网关建议切片数 80% 权重 + 主机 CPU/内存评估 20% 权重** 加权决策；自动遵守 `session_start_limit` 剩余会话数 |
| 断连补发（Resume） | 断线后自动使用 **Opcode 6** 携带 `session_id + seq` 续期补发。配置 `resume.forward_replayed_events` 默认 `true`（补发事件照常转发）；设为 `false` 后补发事件**只接收并照常记录 s 序号、不转发给 OneBot**，直到服务端下发 **RESUMED** 事件后恢复转发 |
| 错误码反馈 | 内置 QQ 开放平台错误码表（WS 关闭码 / HTTP 业务码），按 fatal / major / minor 三级映射为 ERROR / WARNING 日志，并转换为 OneBot retcode 向上反馈给调用方 |

### 下游：OneBot 11 / 12（实验性）

| 能力 | 说明 |
| --- | --- |
| 双协议 | OneBot 11 与实验性 OneBot 12 同时支持，**每条连接可单独指定协议版本**，允许 11/12 混用 |
| 四种通信方式 | 正向 HTTP / 正向 HTTPS（tls_cert+tls_key）、反向 HTTP(S) Webhook（HMAC-SHA1 签名）、正向 WS / WSS、反向 WS / WSS（自动重连） |
| 多连接 | `onebot.connections` 是一个列表，可配置**任意多条、四种类型任意混用**的连接 |
| 事件映射 | 群消息 / C2C 私聊消息 / 机器人被拉入或移出群 / 好友添加等映射为标准事件；`qqbot-at-user`、图片等附件转为 CQ 码（OB11）或消息段数组（OB12）；入站 `msg_elements` 会合并为可读文本 |
| 动作支持 | **OneBot 11 标准动作全部注册可访问**（见下方完整清单），QQ 官方协议可实现的动作真实实现，未开放能力统一返回 `retcode 1404` 并说明原因 |
| ID 桥接 | QQ openid ↔ OneBot 数字 ID 双向映射（blake2b 稳定哈希 + 持久化反查表）；QQ 字符串消息 ID ↔ OneBot int32 message_id 双向映射（持久化到 `data/idmap.json`） |
| 主动推送 | 现行 QQ 发送接口中 `msg_id/event_id` 均可选；标准发送动作默认**不传被动标识，即主动推送**。也可用 `passive=true` 或显式 `msg_id/event_id` 发被动回复 |
| Markdown / 按钮 | 群、单聊与频道消息均可构造 `markdown` 与 `keyboard`；按钮回调透传为 Q2OEX 事件，并可用 `q2oex.respond_interaction` 回应 |

### Q2OEX 扩展协议

OneBot 未定义的 QQ 官方能力（频道消息、频道/子频道管理、帖子、表情表态、音频、Markdown、消息按钮、互动回应、消息审核等）以 `q2oex.*` 命名空间的事件与动作补全，可通过配置 `q2oex.enable: false` 整体关闭。完整定义与消息格式见 [Q2OEX.md](Q2OEX.md)。

---

## 目录结构

```
qqbot2onebot/
├── main.py                    # 程序入口
├── config.yaml                # 配置（首次运行自动生成，已被 git 忽略）
├── requirements.txt
├── README.md
├── Q2OEX.md                   # Q2OEX 扩展协议定义
├── q2o/
│   ├── config.py              # 配置加载 / 校验 / 自生成模板
│   ├── logger.py              # 彩色控制台 + 纯净文件日志
│   ├── app.py                 # 应用编排（装配全部组件）
│   ├── qqbot/                 # 上游：QQ 官方协议
│   │   ├── api.py             #   REST 客户端（token、gateway、消息/Markdown/按钮/互动接口）
│   │   ├── ws.py              #   网关 WebSocket（心跳/Identify/Resume 补发）
│   │   ├── webhook.py         #   Webhook 接收（Ed25519 验签）
│   │   ├── intents.py         #   事件订阅位移（all 硬编码）
│   │   ├── sharding.py        #   切片决策（80% 网关建议 + 20% 主机评估）
│   │   └── errors.py          #   QQ 错误码表与严重程度映射
│   ├── onebot/                # 下游：OneBot 11/12
│   │   ├── convert.py         #   QQ 事件 -> OB11/OB12 事件转换
│   │   ├── actions.py         #   OneBot/Q2OEX 动作 -> QQ REST 调用
│   │   ├── q2oex.py           #   Q2OEX 扩展事件实现
│   │   └── comms/             #   四种通信连接
│   │       ├── base.py
│   │       ├── http_server.py #     正向 HTTP(S)
│   │       ├── http_post.py   #     反向 HTTP(S) Webhook
│   │       ├── ws_server.py   #     正向 WS(S)
│   │       └── ws_reverse.py  #     反向 WS(S)
│   └── utils/
│       ├── idmap.py           # openid ↔ 虚拟号 / 消息 ID 双向映射（持久化，可开关翻译）
│       └── context.py         # 被动回复上下文 / 消息缓存 / 入群审批 flag 上下文
└── data/                      # 运行时数据（已被 git 忽略）
```

---

## 快速开始

```bash
pip install -r requirements.txt
python main.py            # 首次运行：生成 config.yaml 后退出
# 编辑 config.yaml，填入 qqbot.app_id 与 qqbot.client_secret
python main.py            # 再次运行：正式启动
```

指定其他配置文件：`python main.py -c /path/to/config.yaml`

## 配置要点

- **QQ 接入**：`qqbot.ingress` 选 `websocket` 或 `webhook`；webhook 模式需公网可达并在 QQ 开放平台配置回调地址
- **事件订阅**：`qqbot.events` 用文字书写，`all` 订阅全部
- **切片**：`qqbot.sharding.count: 0` 为 auto 加权决策；正整数为显式数量
- **补发转发**：`qqbot.resume.forward_replayed_events` 默认 `true`，担心补发扰乱业务时设为 `false`
- **发送策略**：`qqbot.message.passive_by_default` 默认 `false`。即标准发送动作默认主动推送；单次调用传 `passive=true` 可改为自动使用最近上下文被动回复
- **OneBot 连接**：`onebot.connections` 列表，支持 `http_server` / `http_post` / `ws_server` / `ws_reverse` 任意组合，每条可单独指定 `protocol: 11|12`
- **openid 翻译**：`onebot.openid_translate`（默认 `true`）。QQ 官方协议用 openid 标识用户/群，而很多 OneBot 生态程序不支持 openid（甚至对 user_id 做 QQ 号格式校验）。启用后每个 openid 分配一个符合 QQ 号规则的虚拟号，对照表持久化于 `data/idmap.json`，可双向互译；设为 `false` 则使用 blake2b 哈希数字 ID
- **入群审批**：机器人需拥有群管理员身份才能收到 `GROUP_JOIN_REQUEST` 事件；业务侧用 `set_group_add_request` 通过/拒绝（flag 来自 request 事件，24 小时内有效，仅可审批一次）

## 消息发送语义

QQ 开放平台现行群/单聊发送接口中，`msg_id`、`event_id` 均为可选被动回复标识；不传即主动消息。q2o 按以下规则构造请求：

| 调用方式 | 行为 |
| --- | --- |
| 不显式传 `msg_id/event_id`，且 `passive` 未开启 | **主动推送**：请求体不带 `msg_id/event_id/msg_seq` |
| 显式传 `msg_id` | 被动回复；优先使用 QQ 原始消息 ID，数字 ID 会自动从消息映射反查 |
| 显式传 `event_id` | 事件被动回复；与 `msg_id` 二选一 |
| 传 `passive=true` | 自动使用当前会话最近收到的消息/事件；过期或达到被动次数上限后自动降级主动推送 |
| 消息含 `CQ:reply` / OB12 `reply` 段 | 作为显式 `msg_id` 被动回复 |
| `qqbot.message.passive_by_default=true` | 未显式传被动标识时，默认启用上一条的自动被动逻辑 |

若平台返回“被动回复过期 / msg_id 无效 / 次数超限”等错误，且该次被动引用不是调用方显式指定的，q2o 会自动移除被动标识并重试一次主动发送。

### 文本 / Markdown / 按钮

标准发送动作均接受 `markdown` 与 `keyboard` 参数；OneBot 12 消息数组和 OneBot 11 CQ 码也支持 Q2OEX 段，详见 [Q2OEX.md](Q2OEX.md)。

HTTP 示例（Markdown + 按钮，主动推送到群）：

```json
{
  "action": "send_group_msg",
  "params": {
    "group_id": 123456789,
    "markdown": "## 每日签到\n\n今日签到成功！",
    "keyboard": {
      "rows": [{
        "buttons": [{
          "label": "签到",
          "style": 1,
          "type": 2,
          "data": "/签到",
          "enter": true
        }]
      }]
    }
  }
}
```

同一条消息中，普通 `content` 与 `markdown` 互斥；`image/record/video/file` 富媒体也不能与 `markdown` 混填。

## OneBot 动作错误码约定

| retcode | 含义 |
| --- | --- |
| 0 | 成功 |
| 1100 | 请求参数/本地构造错误 |
| 1300 | QQ 侧轻微错误（频控、被动过期等，可重试） |
| 1400 | QQ 侧主要错误（权限、参数等） |
| 1404 | 该能力不受 QQ 官方 Bot 协议支持 |
| 1500 | QQ 侧致命错误（凭据、切片配置等） |

`wording` 字段携带 QQ 原始错误码与描述，如 `QQ错误码 40034100: 主动消息发送超过频控限制 - ...`。

## OneBot API 支持一览

**OneBot 11 标准动作已全部注册，任何动作调用都会得到结构化响应，绝不出现「未知动作」。**

### 已实现（真实能力）

| 动作 | 实现方式 |
| --- | --- |
| `send_msg` / `send_group_msg` / `send_private_msg` | QQ 群/C2C 消息接口；支持文本、at、reply、图片/视频/语音/文件（URL 上传）、Markdown、按钮、主动推送、msg_id/event_id 被动回复 |
| `get_msg` | 运行期消息缓存（LRU 1000 条，QQ 无历史消息接口，仅覆盖运行期间收到的消息） |
| `get_image` / `get_record` | CQ 码 file 字段本身即 URL，透传返回 |
| `can_send_image` / `can_send_record` | 恒 `yes: true`（URL 形式） |
| `get_group_member_list` | `GET /v2/groups/{group_openid}/members` 分页拉取（需平台权限，QQ 错误码自动反馈） |
| `set_group_add_request` | QQ 官方入群申请审批接口（flag 来自 request 事件） |
| `get_login_info` / `get_version_info` / `get_status` | 本地应答 |
| `.handle_quick_operation` | 快速操作回执 |
| `q2oex.*` 扩展动作 | 见 Q2OEX.md |

### 已注册但返回 1404（QQ 官方协议无此能力）

`delete_msg`、`get_forward_msg`、`send_private_forward_msg`、`send_group_forward_msg`、`send_like`、`set_group_kick`、`set_group_ban`、`set_group_anonymous_ban`、`set_group_whole_ban`、`set_group_admin`、`set_group_anonymous`、`set_group_card`、`set_group_name`、`set_group_leave`、`set_group_special_title`、`set_friend_add_request`（QQ 好友添加无需审批）、`get_stranger_info`、`get_friend_list`、`get_group_info`、`get_group_list`、`get_group_member_info`、`get_group_honor_info`、`get_cookies` / `get_csrf_token` / `get_credentials`（官方 Bot 无网页凭证体系）、`set_restart`、`clean_cache`

### OneBot 12（实验性）核心动作

`get_supported_actions`、`get_version`、`get_status`、`get_self_info`、`send_message`（private/group/channel 三种 detail_type）、`upload_file`（type=url，需带 group_id/user_id）、`delete_message`、`get_user_info`、`get_friend_list`、`get_group_info`、`get_group_list`、`get_group_member_info`、`get_group_member_list`、`set_group_name`、`leave_group`、`upload_file_fragmented`、`get_file`、`get_file_fragmented` —— 全部可访问，其中 QQ 无对应能力的返回 1404。

## 已知限制（QQ 官方协议侧）

- 主动消息受 QQ 平台授权、认证状态、单关系/机器人维度频控与每日配额约束；未获主动推送权限时平台会返回 `40034105` 等错误
- Markdown、按钮、互动召回（`is_wakeup`）等能力需在 QQ 开放平台完成对应权限/资质申请
- 群/单聊消息目前不支持平台侧撤回；相关动作返回 `retcode 1404`
- bot 自身 QQ 号无法从开放平台接口获得，请在 `onebot.self_id` 手动填写
- QQ 官方接口按 openid 工作，机器人只能向已建立映射/已知 openid 的群或用户发送消息

## 参考文档

- QQ 开放平台 API v2: <https://bot.q.qq.com/wiki/develop/api-v2/>
- QQ 消息按钮: <https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/trans/msg-btn.html>
- QQ 互动事件响应: <https://bot.q.qq.com/wiki/develop/api-v2/autogen/api/interactions_interaction_id.put.html>
- OneBot 11: <https://github.com/botuniverse/onebot-11>
- OneBot 12（实验性）: <https://12.onebot.dev/>
