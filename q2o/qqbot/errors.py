"""QQ 开放平台错误码表与严重程度映射。

来源: https://bot.q.qq.com/wiki/develop/api-v2/dev/error/opcode.html 等官方文档。
每个错误码映射到一个严重级别:
  fatal - 无法继续运行（凭据错误、被封禁等）      -> ERROR 日志
  major - 当前操作失败但连接可保持（权限不足等）  -> WARNING 或 ERROR
  minor - 可重试/可忽略（频控、参数小错等）       -> WARNING
配置 logging.qq_error_min_level = warn 时，仅 fatal 记 ERROR；
= error 时，fatal 与 major 均记 ERROR。
"""

# by:xiaoliang - 2026.10.17

from __future__ import annotations

import logging

QQ_ERROR_CODES: dict[int, tuple[str, str]] = {
    4001: ("无效的 opcode", "major"),
    4002: ("无效的 payload", "major"),
    4007: ("seq 错误，resume 重试", "minor"),
    4008: ("发送 payload 过快，触发限频", "minor"),
    4009: ("连接过期，需要重连", "major"),
    4010: ("无效的 shard", "fatal"),
    4011: ("需要分片连接，当前切片数不足", "fatal"),
    4012: ("无效的 API version", "fatal"),
    4013: ("无效的 intent(s)", "fatal"),
    4014: ("无权订阅该 intent(s)，请检查开放平台权限", "fatal"),
    10001: ("账号异常/Token 无效", "fatal"),
    10003: ("无权限访问该接口", "major"),
    10004: ("接口不存在", "major"),
    11001: ("系统繁忙，请稍后重试", "minor"),
    11241: ("Token 过期", "minor"),
    11242: ("Token 校验失败", "major"),
    11243: ("签名错误", "major"),
    11251: ("应用不存在/被下架", "fatal"),
    11252: ("无效的应用 ID", "fatal"),
    11253: ("应用未上线，无法使用该能力", "major"),
    11254: ("应用接口调用超出频控", "minor"),
    11261: ("参数错误", "minor"),
    11262: ("机器人不在该群/频道内", "major"),
    11263: ("消息长度超限", "major"),
    11264: ("消息发送频率超限", "minor"),
    11265: ("消息内容触雷/审核不通过", "major"),
    11281: ("系统中未找到对应用户", "major"),
    304010: ("ark 消息失败", "major"),
    304023: ("消息推送失败，被动回复超期", "minor"),
    304036: ("无 Markdown 模板权限", "major"),
    304037: ("无消息按钮组件权限", "major"),
    304038: ("消息按钮组件不存在", "major"),
    304039: ("消息按钮组件解析错误", "major"),
    304040: ("消息按钮组件消息内容错误", "major"),
    304047: ("主动消息推送超过限制的子频道数", "major"),
    304103: ("消息 ID 已过期，不能回复", "minor"),
    305007: ("键盘样式参数错误", "minor"),
    40034004: ("富媒体信息转存失败", "minor"),
    40034005: ("回复消息 msg_id 已过期", "minor"),
    40034006: ("消息内容违规", "major"),
    40034008: ("Markdown 参数有空值", "minor"),
    40034009: ("Markdown 参数有换行符", "minor"),
    40034010: ("模板参数中不能含有 Markdown 语法", "minor"),
    40034011: ("无效的 Markdown 内容", "major"),
    40034024: ("请求参数 msg_id 无效或越权", "minor"),
    40034025: ("请求参数 event_id 无效", "minor"),
    40034026: ("请求参数 event_id 已过期", "minor"),
    40034027: ("该事件不支持回复消息", "major"),
    40034029: ("内联键盘行/列超限", "minor"),
    40034100: ("主动消息发送超过频控限制", "minor"),
    40034101: ("机器人非群成员", "major"),
    40034105: ("主动消息发送失败，无权限", "major"),
    40034106: ("消息不支持该指令类型", "major"),
    40034108: ("指令参数长度超限", "minor"),
    40034109: ("指令参数解析失败", "minor"),
    40034122: ("召回消息已达区间上限", "major"),
    40034123: ("不支持召回消息", "major"),
    40034124: ("Markdown 消息参数错误", "minor"),
    40034127: ("无 Markdown 模板权限", "major"),
    40034128: ("被动回复时间或次数超限", "minor"),
    40054001: ("消息被去重", "minor"),
    40054005: ("消息被去重，请使用不同 msg_seq", "minor"),
    40054007: ("消息长度超限", "major"),
    40054010: ("不允许发送 URL", "major"),
    40054013: ("用户拒收消息", "major"),
    40054016: ("机器人已下线", "fatal"),
    40054018: ("消息过长或异常", "major"),
    630001: ("互动回调参数无效", "minor"),
    630003: ("互动回调 AppID 不匹配", "major"),
    630004: ("互动回调数据更新失败", "minor"),
    630005: ("互动回调数据读取失败", "minor"),
    630006: ("互动回调读取请求 AppID 失败", "major"),
    630007: ("互动回调数据太大", "major"),
    630008: ("互动回调预处理失败", "major"),
}

QQ_WS_CLOSE_RESUMEABLE = {
    4007: True,
    4008: True,
    4009: False,
    4010: False,
    4011: False,
    4012: False,
    4013: False,
    4014: False,
}


class QQAPIError(Exception):
    """QQ 开放平台 HTTP API 返回的业务错误。"""

    def __init__(self, code: int, message: str = "", http_status: int = 0):
        self.code = code
        self.message = message
        self.http_status = http_status
        desc, severity = QQ_ERROR_CODES.get(code, ("未知错误", "major"))
        self.description = desc
        self.severity = severity
        super().__init__(f"[QQ {code}] {desc}: {message}")


class QQWSCloseError(Exception):
    """QQ 网关下发的关闭码。"""

    def __init__(self, code: int, reason: str = ""):
        self.code = code
        self.reason = reason
        desc, severity = QQ_ERROR_CODES.get(code, ("未知关闭码", "major"))
        self.description = desc
        self.severity = severity
        self.resumeable = QQ_WS_CLOSE_RESUMEABLE.get(code, False)
        super().__init__(f"[QQ WS {code}] {desc}: {reason}")


def log_qq_error(logger: logging.Logger, err: QQAPIError | QQWSCloseError,
                 min_level: str = "warn", context: str = "") -> None:
    """按严重程度把 QQ 错误写入日志，并向 OneBot 侧反馈。

    返回需要记日志的级别名（供上层把错误回填进 OneBot 响应）。
    """
    ctx = f" ({context})" if context else ""
    text = f"{err}{ctx}"
    if err.severity == "fatal" or (min_level == "error" and err.severity == "major"):
        logger.error(text)
    else:
        logger.warning(text)
