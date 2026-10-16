# Q2OEX — qqbot2onebot 扩展协议定义

Q2OEX（**Q**QBot **to** **O**neBot **EX**tension）用于补全 OneBot 11/12 标准中未定义、但 QQ 官方 Bot 协议（QQ 开放平台 API v2）具备的频道与互动能力，并提供一套统一的 **Markdown / 消息按钮 / 主动推送** 消息格式。

- 命名空间：所有扩展事件与动作均以 `q2oex` 为标识
- 开关：配置 `q2oex.enable: false` 后，不产生任何 q2oex 事件、不处理任何 `q2oex.*` 动作
- 原始性：每个 q2oex 事件都在 `data` 字段中携带 QQ 官方**原始事件负载**，扩展字段只做平铺冗余，方便业务直接取用
- 版本：Q2OEX 1.1（随 q2o 1.1.0 更新，对齐现行群/单聊消息、`markdown`、`keyboard`、`event_id` 与互动回应接口）

---

## 1. 事件

### 1.1 OneBot 11 形态

```json
{
  "time": 1759900000,
  "self_id": 123456,
  "post_type": "q2oex",
  "q2oex_type": "guild_member_add",
  "qq_event": "GUILD_MEMBER_ADD",
  "data": { "…": "QQ 官方原始事件负载" }
}
```

| 字段 | 说明 |
| --- | --- |
| `post_type` | 固定为 `q2oex` |
| `q2oex_type` | 扩展事件名（见 1.3 表） |
| `qq_event` | QQ 官方事件类型名（如 `GUILD_MEMBER_ADD`） |
| `data` | QQ 官方原始事件负载，结构以官方文档为准 |

### 1.2 OneBot 12 形态

```json
{
  "id": "q2oex-1759900000000000000",
  "time": 1759900000.0,
  "type": "q2oex",
  "detail_type": "guild_member_add",
  "sub_type": "",
  "self": { "platform": "qq", "user_id": "123456" },
  "qq_event": "GUILD_MEMBER_ADD",
  "data": { "…": "QQ 官方原始事件负载" }
}
```

### 1.3 扩展事件名与 QQ 事件对照

| q2oex_type | QQ 事件 | 说明 |
| --- | --- | --- |
| `channel_message` | `AT_MESSAGE_CREATE` / `MESSAGE_CREATE` | 频道文字子频道消息（额外平铺 `guild_id`/`channel_id`/`message_id`/`author`/`content`/`attachments`） |
| `direct_message` | `DIRECT_MESSAGE_CREATE` | 频道私信 |
| `guild_create` / `guild_update` / `guild_delete` | `GUILD_*` | 频道创建/变更/解散（机器人加入/退出频道） |
| `channel_create` / `channel_update` / `channel_delete` | `CHANNEL_*` | 子频道创建/变更/删除 |
| `guild_member_add` / `guild_member_update` / `guild_member_remove` | `GUILD_MEMBER_*` | 频道成员加入/资料变更/移除 |
| `message_audit_pass` / `message_audit_reject` | `MESSAGE_AUDIT_*` | 频道消息审核通过/驳回 |
| `message_reaction_add` / `message_reaction_remove` | `MESSAGE_REACTION_*` | 消息表情表态添加/移除 |
| `thread_create` / `thread_update` / `thread_delete` | `THREAD_*` | 频道帖子主题 |
| `post_create` / `post_delete` | `POST_*` | 频道帖子发帖/删帖 |
| `reply_create` / `reply_delete` | `REPLY_*` | 帖子回复/删回复 |
| `forum_publish_audit_result` | `FORUM_PUBLISH_AUDIT_RESULT` | 论坛发帖审核结果 |
| `audio_start` / `audio_finish` / `audio_on_mic` / `audio_off_mic` | `AUDIO_*` | 音频/直播子频道事件 |
| `interaction_create` | `INTERACTION_CREATE` | 互动事件，字段见 1.4 |
| `friend_del` | `FRIEND_DEL` | 用户删除机器人好友 |
| `c2c_msg_reject` / `c2c_msg_receive` | `C2C_MSG_*` | C2C 主动消息拒绝/送达回执 |
| `group_msg_reject` / `group_msg_receive` | `GROUP_MSG_*` | 群主动消息拒绝/送达回执 |

未在表中的新 QQ 事件将按 `qq_type.lower()` 自动生成 q2oex 事件透传，不会丢失。

### 1.4 `interaction_create` 平铺字段

`INTERACTION_CREATE` 已扩展覆盖消息按钮、快捷菜单、消息反馈、清空会话、故事集、切换模型、用户/群授权等互动类型。事件除 `data` 原始负载外，还平铺：

| 字段 | 说明 |
| --- | --- |
| `interaction_id` | 互动 ID；按钮/快捷菜单回调必须用它调用 `q2oex.respond_interaction` |
| `interaction_type` | QQ 互动类型：11=消息按钮，12=快捷菜单，13=消息反馈，14=清空会话，15=故事集，16=切换模型，18/19/20=授权相关 |
| `scene` | `c2c` / `group` / `guild` |
| `chat_type` | 0=频道，1=群聊，2=单聊 |
| `guild_id` / `channel_id` | 频道场景 ID |
| `group_openid` / `group_member_openid` | 群场景 ID |
| `user_openid` | 单聊场景用户 ID |
| `button_id` | 按钮回调的按钮 ID |
| `button_data` | 按钮回调的 `action.data` 字符串 |
| `message_id` | 被操作消息 ID（若平台提供） |
| `feedback_opt` | 消息反馈：`LIKE` / `UNLIKE` |
| `action` | 故事集/切换模型等操作动作 |

OneBot 12 示例：

```json
{
  "id": "q2oex-1760000000000000000",
  "time": 1760000000.0,
  "type": "q2oex",
  "detail_type": "interaction_create",
  "sub_type": "",
  "self": { "platform": "qq", "user_id": "123456" },
  "qq_event": "INTERACTION_CREATE",
  "interaction_id": "1b13d569-4610-4ab9-bc51-feecc5def6d4",
  "interaction_type": 11,
  "scene": "c2c",
  "chat_type": 2,
  "user_openid": "A1B2C3D4E5F6A1B2C3D4E5F6A1B2C3D4",
  "button_id": "allow-once",
  "button_data": "confirm:once",
  "data": { "…": "QQ 官方原始事件负载" }
}
```

---

## 2. Q2OEX 消息格式

Q2OEX 消息格式让 OneBot 11/12 应用可以统一发送 QQ 官方 `markdown` 与 `keyboard`。标准动作 `send_group_msg`、`send_private_msg`、`send_msg`、OB12 `send_message` 均支持；`q2oex.send_*` 动作也使用同一格式。

### 2.1 OneBot 12 消息段

```json
[
  { "type": "text", "data": { "text": "普通文本" } },
  { "type": "q2oex:markdown", "data": { "content": "## 标题\n\n内容" } },
  {
    "type": "q2oex:button",
    "data": {
      "id": "btn_signin",
      "label": "签到",
      "visited_label": "已签到",
      "style": 1,
      "type": 2,
      "permission_type": 2,
      "data": "/签到",
      "enter": true,
      "reply": true
    }
  }
]
```

支持的消息段：

| 消息段 | 说明 |
| --- | --- |
| `q2oex:markdown` | QQ Markdown；`data` 可为 `{"content":"..."}` 或完整官方 `MessageMarkdown` 对象（如 `template_id`、`custom_template_id`、`force_verify_image_resource`） |
| `q2oex:keyboard` | 完整键盘；`data` 可为官方 `{"id":"模板ID"}`、`{"content":{"rows":[...]}}`，也可直接传 Q2OEX 简写 `{"rows":[...]}` |
| `q2oex:button` | 追加一个按钮到最后一个按钮行；没有行时自动创建一行 |
| `q2oex:keyboard_row` | 追加一个按钮行 |
| `markdown` / `keyboard` / `button` / `keyboard_row` | 上述类型的无前缀别名 |

### 2.2 OneBot 11 CQ 码

为避免 Markdown/JSON 中的逗号、方括号与 CQ 语法冲突，OB11 使用 base64url(JSON) 作为 `data`：

```text
[CQ:q2oex,type=markdown,data=eyJjb250ZW50IjoiIyDlraYlZGInfQ]
```

- `type`：`markdown` / `keyboard` / `button` / `keyboard_row`
- `data`：UTF-8 JSON 的 base64url 编码（可省略末尾 `=`）
- 也可使用简写 `[CQ:markdown,data=...]`、`[CQ:keyboard,data=...]`、`[CQ:button,data=...]`

### 2.3 发送参数直传（OB11 与 OB12 均支持）

无需构造消息段时，可直接在动作参数中传：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `markdown` | string / object | Markdown 字符串，或完整官方 `MessageMarkdown` 对象 |
| `keyboard` | string / object / array | 键盘模板 ID；官方 keyboard 对象；Q2OEX 简写 `rows` 数组 |
| `buttons` | array | 追加按钮；等价于多个 `q2oex:button` 段 |

示例（主动 Markdown + 按钮）：

```json
{
  "action": "send_group_msg",
  "params": {
    "group_id": 123456789,
    "markdown": "## 每日签到\n\n今日签到成功！",
    "keyboard": {
      "rows": [{
        "buttons": [{
          "id": "btn_signin",
          "label": "签到",
          "style": 1,
          "type": 2,
          "permission_type": 2,
          "data": "/签到",
          "enter": true
        }]
      }]
    }
  }
}
```

### 2.4 按钮简写与官方结构

Q2OEX 按钮简写：

| 字段 | 说明 |
| --- | --- |
| `id` | 按钮 ID |
| `label` | 按钮文字（必填） |
| `visited_label` | 点击后文字 |
| `style` | 0 灰框，1 蓝框，3 白底红字，4 蓝底白字 |
| `type` | 0 跳转，1 回调，2 指令；默认 2 |
| `permission_type` | 0 指定用户，1 管理员，2 所有人；默认 2 |
| `permission` | 官方 Permission 对象；优先级高于 `permission_type` |
| `data` | 回调/指令数据；默认取 `label` |
| `enter` | 指令按钮点击后直接发送 |
| `reply` | 指令按钮是否引用回复本消息 |
| `anchor` | 指令按钮唤起选图器等客户端能力 |
| `unsupport_tips` / `click_limit` / `at_bot_show_channel_list` / `modal` / `group_id` | 原样映射到 QQ 官方 Button/Action |

完全官方结构会直接透传，例如：

```json
{
  "render_data": { "label": "确认", "visited_label": "已确认", "style": 1 },
  "action": {
    "type": 1,
    "permission": { "type": 2 },
    "data": "confirm",
    "unsupport_tips": "客户端版本过低",
    "modal": { "content": "确认执行？", "confirm_text": "确认" }
  }
}
```

### 2.5 内容互斥

- 一条消息中普通 `content` 与 `markdown` 互斥；传 `markdown` 后文本段必须为空
- `image/record/video/file` 富媒体与 `markdown` 不能同条混填
- QQ 单条消息最多一个富媒体附件；多余附件会被丢弃并记录日志

---

## 3. 主动推送与被动回复

Q2OEX 与标准动作共用以下发送参数：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `msg_id` | string | 显式被动回复消息 ID；与 `event_id` 二选一 |
| `event_id` | string | 显式被动回复事件 ID（来自事件最外层 `id`）；与 `msg_id` 二选一 |
| `msg_seq` | integer | 被动回复序号；不传由平台默认，自动被动时会递增 |
| `passive` | boolean | `true` 时自动使用当前会话最近上下文做被动回复 |
| `is_wakeup` | boolean | 单聊互动召回消息；与 `msg_id/event_id` 互斥 |
| `stream` | boolean | 单聊流式参数（平台能力支持时） |

行为规则：

1. 显式 `msg_id` / `event_id`：被动回复；
2. `passive=true`：使用最近入站消息/互动事件；过期或次数超限后自动降级主动推送；
3. 两者都没有：**主动推送**，请求体不带 `msg_id/event_id/msg_seq`；
4. 自动被动被平台判定过期/无效/超限时，自动移除被动标识并重试一次主动发送；
5. 配置 `qqbot.message.passive_by_default=true` 可把规则 3 的默认行为改为规则 2。

主动推送受 QQ 平台授权、认证状态、单关系/机器人维度频控与每日配额约束；Markdown、按钮、互动召回等能力也需要平台权限。

---

## 4. 动作

调用方式与所在协议的普通动作一致（HTTP POST / WS 帧），动作名带 `q2oex.` 前缀。动作成功时 `data.message_id` 为 OneBot 数字消息 ID，`data.qq_message_id` 为 QQ 原始消息 ID，`data.qq.ext_info.ref_idx` 为平台返回的引用索引。

### 4.1 `q2oex.send_message` — 统一发送

按 `detail_type` 分发到群、单聊或频道。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `detail_type` | string | 是 | `group` / `private` / `channel` |
| `group_id` / `group_openid` | number/string | group | OneBot 群 ID 或 QQ 群 openid |
| `user_id` / `user_openid` / `openid` | number/string | private | OneBot 用户 ID 或 QQ 用户 openid |
| `channel_id` | string | channel | 子频道 ID |
| `message` | string/array | 否 | OneBot 消息；支持第 2 节 Q2OEX 格式 |
| `content` | string | 否 | 纯文本（与 `message` 二选一） |
| `markdown` / `keyboard` / `buttons` | - | 否 | 第 2 节格式 |
| `msg_id` / `event_id` / `msg_seq` / `passive` / `is_wakeup` | - | 否 | 第 3 节发送语义 |
| `message_reference` | object/string | 否 | 引用回复；字符串会包装为 `{"message_id": "..."}` |

### 4.2 `q2oex.send_group_message` — 发送群消息

参数同 4.1 的 `detail_type=group`。支持主动推送、Markdown、按钮、富媒体 URL、`msg_id/event_id` 被动回复。

### 4.3 `q2oex.send_private_message` — 发送单聊消息

参数同 4.1 的 `detail_type=private`。额外支持：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `is_wakeup` | boolean | 互动召回消息 |
| `stream` | boolean | 流式消息参数 |
| `input_notify` | object/number | 输入中状态；传秒数或 `{"input_type":1,"input_second":60}` |

### 4.4 `q2oex.send_channel_message` — 发送频道子频道消息

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `channel_id` | string | 是 | 子频道 ID |
| `content` | string | 否 | 普通文本 |
| `message` | string/array | 否 | OneBot 消息 |
| `markdown` / `keyboard` / `buttons` | - | 否 | Markdown 与按钮 |
| `msg_id` / `event_id` / `msg_seq` / `passive` | - | 否 | 被动回复参数 |
| `embed` / `ark` / `image` / `message_reference` | object | 否 | 频道消息官方扩展字段，原样透传 |

成功响应 `data`：`{"message_id": <OneBot id>, "qq_message_id": "<QQ 消息 id>", "qq": {...}}`

### 4.5 `q2oex.send_input_notify` — 发送输入中状态（单聊）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `user_id` / `user_openid` / `openid` | number/string | 是 | 目标用户 |
| `input_notify` | object/number | 否 | 默认 `{"input_type":1,"input_second":60}` |

### 4.6 `q2oex.respond_interaction` — 回应互动事件

收到 `interaction_create` 后，特别是 `interaction_type=11/12`，应尽快调用；否则客户端可能一直 loading 直到超时。

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `interaction_id` | string | 是 | 事件平铺字段 `interaction_id` |
| `code` | integer | 否 | 0 成功，1 失败，2 频繁，3 重复，4 无权限，5 仅管理员；默认 0 |

### 4.7 `q2oex.get_gateway_info` — 获取网关接入信息

无参数。返回 `GET /gateway/bot` 的原始结果，包含 wss 接入点、建议切片数与 `session_start_limit`。

### 4.8 错误语义

q2oex 动作与标准动作共用错误码约定：`0` 成功、`1300` QQ 侧可重试错误、`1400` 参数/主要错误、`1404` 平台不支持或 q2oex 已关闭、`1500` QQ 侧致命错误；`wording` 携带 QQ 原始错误码。

---

## 5. 与标准事件/动作的关系

- QQ 群聊消息（`GROUP_AT_MESSAGE_CREATE` / `GROUP_MESSAGE_CREATE`）、C2C 私聊（`C2C_MESSAGE_CREATE`）、机器人入群/被移出群（`GROUP_ADD_ROBOT`/`GROUP_DEL_ROBOT`）、好友添加（`FRIEND_ADD`）已映射为**标准** OneBot 事件，不走 q2oex
- OneBot 12 的标准消息事件额外携带 `qq.group_openid` / `qq.openid` 扩展字段，方便业务直接拿到 QQ 原生 ID 而不必反查映射表
- 标准发送动作与 q2oex 发送动作共用 Markdown、按钮、主动推送与被动回复逻辑；q2oex 主要补齐频道目标、互动回应、输入状态等 OneBot 未定义能力
- 关闭 `q2oex.enable` 后，仅上述标准事件继续下发，其余 QQ 事件被丢弃并记 DEBUG 日志；`q2oex.*` 动作返回 `retcode 1404`
