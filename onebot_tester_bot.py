import json
import re
import time
import websocket  # pip install websocket-client
from urllib import request

# ============ 配置 ============
WS_URL = "ws://127.0.0.1:6700"          # 协议端 WebSocket 服务地址
HTTP_URL = "http://127.0.0.1:5700"      # 协议端 HTTP API 地址
ACCESS_TOKEN = ""                        # 如协议端设置了 token 填这里
HEARTBEAT_INTERVAL = 30                  # 心跳间隔（秒）

echo_history = {}  # 记忆最近接收的消息（测试用）


# ============ API 调用（OneBot v11 标准） ============
def call_api(action: str, params: dict = None):
    """POST /{action}，params 直接作为请求体。
    返回 {"status": "ok", "retcode": 0, "data": ...}"""
    data = json.dumps(params or {}).encode()
    headers = {"Content-Type": "application/json"}
    if ACCESS_TOKEN:
        headers["Authorization"] = f"Bearer {ACCESS_TOKEN}"
    req = request.Request(f"{HTTP_URL}/{action}", data=data, headers=headers)
    try:
        with request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
            if result.get("status") != "ok":
                print(f"[API] {action} 返回错误: retcode={result.get('retcode')} msg={result.get('msg')} wording={result.get('wording')}")
            return result
    except Exception as e:
        print(f"[API] {action} 调用失败: {e}")
        return None


def send_msg(msg_type: str, id_: int, message="", **extensions):
    """发送消息：支持 Q2OEX markdown、keyboard、buttons 参数。"""
    params = {
        "message_type": msg_type,
        "user_id" if msg_type == "private" else "group_id": id_,
        "message": message,
        "passive": True,
    }
    params.update(extensions)
    return call_api("send_msg", params)


def reply(event, text: str = "", **extensions):
    """按消息类型快速回复"""
    if event.get("message_type") == "private":
        return send_msg("private", event["user_id"], text, **extensions)
    elif event.get("message_type") == "group":
        return send_msg("group", event["group_id"], text, **extensions)


def _demo_button(label: str, data: str, button_id: str):
    return {
        "id": button_id,
        "label": label,
        "style": 1,
        "type": 1,
        "permission_type": 2,
        "data": data,
    }


# ============ 消息解析 ============
def extract_text(event: dict) -> str:
    """从消息事件中提取纯文本，兼容字符串(CQ码)和数组两种格式"""
    msg = event.get("message")

    if isinstance(msg, str):
        # CQ 码字符串：去掉所有 [CQ:xxx,...] 标签，保留文本
        return re.sub(r"\[CQ:.*?\]", "", msg).strip()

    if isinstance(msg, list):
        # 消息段数组：拼接所有 text 段
        texts = [seg["data"]["text"] for seg in msg
                 if isinstance(seg, dict) and seg.get("type") == "text"
                 and isinstance(seg.get("data"), dict)]
        return "".join(texts).strip()

    return ""


# ============ 指令处理 ============
def handle_message(event: dict):
    """处理私聊/群聊消息事件"""
    user_id = event.get("user_id")
    msg_id = event.get("message_id")

    text = extract_text(event)
    raw_message = event.get("raw_message", event.get("message", ""))
    print(f"[消息] {user_id}: {text} | 原始消息: {raw_message}")

    if text == "/ping":
        reply(event, "pong！机器人运行中")

    elif text == "/echo":
        last = echo_history.get(user_id)
        reply(event, f"你上一条消息是：{last}" if last else "你还没有发过消息")

    elif text.startswith("/say "):
        reply(event, text[5:])

    elif text == "/info":
        reply(event, f"消息ID: {msg_id}\n用户: {user_id}\n类型: {event.get('message_type')}")

    elif text == "/time":
        reply(event, time.strftime("现在是 %Y-%m-%d %H:%M:%S"))

    elif text == "/md" or text.startswith("/md "):
        content = text[4:].strip() if text.startswith("/md ") else "## Markdown 测试\n\n按钮与 Markdown 测试"
        reply(event, "", markdown={"content": content})

    elif text == "/buttons" or text.startswith("/buttons "):
        content = text[8:].strip() if text.startswith("/buttons ") else "请选择一个按钮"
        reply(event, content, buttons=[
            _demo_button("按钮 A", "test:button_a", "tester_button_a"),
            _demo_button("按钮 B", "test:button_b", "tester_button_b"),
        ])

    elif text == "/keyboard" or text.startswith("/keyboard "):
        content = text[10:].strip() if text.startswith("/keyboard ") else "键盘模板测试"
        reply(event, content, keyboard={
            "rows": [{"buttons": [
                _demo_button("第一行", "test:keyboard_1", "tester_keyboard_1"),
            ]}, {"buttons": [
                _demo_button("第二行 A", "test:keyboard_2a", "tester_keyboard_2a"),
                _demo_button("第二行 B", "test:keyboard_2b", "tester_keyboard_2b"),
            ]}],
        })

    elif text == "/mdbuttons" or text.startswith("/mdbuttons "):
        content = text[11:].strip() if text.startswith("/mdbuttons ") else "## Markdown + 按钮\n\n点击下方按钮测试互动回调。"
        reply(event, "", markdown={"content": content}, keyboard={
            "rows": [{"buttons": [
                _demo_button("确认", "test:confirm", "tester_md_confirm"),
                _demo_button("取消", "test:cancel", "tester_md_cancel"),
            ]}],
        })

    echo_history[user_id] = text  # 记录最近消息


def handle_event(event: dict):
    """事件分发"""
    post_type = event.get("post_type")
    if post_type == "message":
        handle_message(event)
    elif post_type == "notice":
        print(f"[通知] {event.get('notice_type')}: {event.get('user_id')}")
    elif post_type == "request":
        print(f"[请求] {event.get('request_type')}: {event.get('user_id')}")
    elif post_type == "q2oex" and event.get("q2oex_type") == "interaction_create":
        interaction_id = event.get("interaction_id")
        print(f"[按钮互动] id={interaction_id} data={event.get('button_data')}")
        if interaction_id:
            call_api("q2oex.respond_interaction", {
                "interaction_id": interaction_id,
                "code": 0,
            })


# ============ WebSocket 连接 ============
def on_open(ws):
    print(f"[连接] 已连接 {WS_URL}")


def on_message(ws, msg: str):
    try:
        data = json.loads(msg)
    except json.JSONDecodeError:
        return
    # meta_event 是生命周期/心跳，直接跳过
    if data.get("post_type") == "meta_event":
        return
    print(f"[事件] {data.get('post_type')}: {json.dumps(data, ensure_ascii=False)[:200]}")
    try:
        handle_event(data)
    except Exception as e:
        print(f"[处理异常] {e}")


def on_error(ws, error):
    print(f"[错误] {error}")


def on_close(ws, code, msg):
    print(f"[断开] code={code}, msg={msg}，5秒后重连...")
    time.sleep(5)
    main()


def main():
    ws = websocket.WebSocketApp(
        WS_URL,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close,
        header=[f"Authorization: Bearer {ACCESS_TOKEN}"] if ACCESS_TOKEN else None,
    )
    ws.run_forever(ping_interval=HEARTBEAT_INTERVAL, ping_timeout=10)


if __name__ == "__main__":
    main()