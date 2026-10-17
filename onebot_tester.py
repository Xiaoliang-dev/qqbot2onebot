# -*- coding: utf-8 -*-
"""OneBot v11 测试客户端，支持事件监听、API 调用与交互式测试。"""

# by:xiaoliang - 2026.10.17

import argparse
import asyncio
import itertools
import json
import queue
import threading
import time
from collections import deque

import requests
import websockets


class EventStore:
    """保存最近收到的事件，供 history / reply 等命令使用。"""

    def __init__(self, maxlen: int = 200):
        self.events = deque(maxlen=maxlen)
        self.lock = threading.Lock()

    def add(self, event: dict):
        with self.lock:
            self.events.append((time.time(), event))

    def last_message(self, msg_type: str | None = None) -> dict | None:
        """取最近一条消息事件，可按 private/group 过滤。"""
        with self.lock:
            for _, ev in reversed(self.events):
                if ev.get("post_type") == "message" and \
                        (msg_type is None or ev.get("message_type") == msg_type):
                    return ev
        return None

    def recent(self, msg_type: str | None = None, limit: int = 20) -> list:
        with self.lock:
            items = list(self.events)
        if msg_type:
            items = [(t, ev) for t, ev in items
                     if ev.get("post_type") == "message"
                     and ev.get("message_type") == msg_type]
        return items[-limit:]


class WsApiBridge:
    """
    同步线程 -> asyncio WS 循环 的调用桥。

    命令行线程调用 call(action, params) 会阻塞等待结果；
    asyncio 循环负责发送带 echo 的请求帧，并按 echo 匹配响应。
    """

    def __init__(self):
        self._pending: queue.Queue = queue.Queue()
        self._futures: dict[str, "queue.Queue"] = {}
        self._echo_counter = itertools.count(1)
        self._ws = None
        self.connected = threading.Event()

    def call(self, action: str, params: dict, timeout: float = 10) -> dict:
        """同步调用 WS API，返回 OneBot 响应 dict。"""
        if not self.connected.is_set() or self._ws is None:
            raise RuntimeError("WS 未连接，无法通过 WS 调用 API")
        echo = f"api-{next(self._echo_counter)}"
        result_q: queue.Queue = queue.Queue(maxsize=1)
        self._futures[echo] = result_q
        self._pending.put((echo, action, params))
        try:
            return result_q.get(timeout=timeout)
        except queue.Empty:
            raise RuntimeError(f"WS API {action} 调用超时")
        finally:
            self._futures.pop(echo, None)

    async def pump(self):
        """从待发送队列取请求并写入 WS。由 listen_events 的任务并发运行。"""
        loop = asyncio.get_running_loop()
        while True:
            echo, action, params = await loop.run_in_executor(
                None, self._pending.get)
            if self._ws is None:
                continue
            frame = {"action": action, "params": params, "echo": echo}
            await self._ws.send(json.dumps(frame))

    def dispatch_response(self, resp: dict):
        """收到带 echo 的响应时，唤醒等待的同步线程。"""
        echo = resp.get("echo")
        q = self._futures.get(echo)
        if q is not None:
            q.put(resp)


class OneBotClient:
    """OneBot v11 客户端：API 走 HTTP 或 WS，事件走 WS。"""

    def __init__(self, host: str, http_port: int, ws_port: int,
                 token: str | None = None, use_https: bool = False,
                 via_ws: bool = False):
        self.host = host
        self.http_port = http_port
        self.ws_port = ws_port
        self.token = token
        self.scheme = "https" if use_https else "http"
        self.ws_scheme = "wss" if use_https else "ws"
        self.via_ws = via_ws
        self.bridge = WsApiBridge()

    def api(self, action: str, params: dict | None = None) -> dict:
        """调用 OneBot API，返回响应 dict。via_ws 时走 WS，否则走 HTTP。"""
        params = params or {}
        if self.via_ws:
            return self._check(self.bridge.call(action, params), action)
        return self._check(self._api_http(action, params), action)

    @staticmethod
    def _check(data: dict, action: str) -> dict:
        if data.get("status") != "ok":
            raise RuntimeError(f"API {action} 调用失败: {data}")
        return data

    def _api_http(self, action: str, params: dict) -> dict:
        url = f"{self.scheme}://{self.host}:{self.http_port}/{action}"
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        resp = requests.post(url, json=params, headers=headers, timeout=10)
        resp.raise_for_status()
        return resp.json()

    def get_login_info(self) -> dict:
        return self.api("get_login_info")["data"]

    def get_group_list(self) -> list[dict]:
        """获取群列表。每个元素含 group_id / group_name / member_count 等。"""
        return self.api("get_group_list")["data"]

    def get_friend_list(self) -> list[dict]:
        return self.api("get_friend_list")["data"]

    def send_group_msg(self, group_id: int, message: str) -> dict:
        return self.api("send_group_msg",
                        {"group_id": group_id, "message": message})["data"]

    def send_private_msg(self, user_id: int, message: str) -> dict:
        return self.api("send_private_msg",
                        {"user_id": user_id, "message": message})["data"]

    def set_friend_add_request(self, flag: str, approve: bool = True,
                               remark: str = "") -> dict:
        return self.api("set_friend_add_request",
                        {"flag": flag, "approve": approve,
                         "remark": remark})["data"]

    def set_group_add_request(self, flag: str, sub_type: str,
                              approve: bool = True, reason: str = "") -> dict:
        return self.api("set_group_add_request",
                        {"flag": flag, "type": sub_type, "approve": approve,
                         "reason": reason})["data"]

    async def listen_events(self, store: EventStore):
        """连接 WS，持续接收事件：缓存 + 打印解析结果 + 响应 WS API 调用。"""
        url = f"{self.ws_scheme}://{self.host}:{self.ws_port}"
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else None
        print(f"[WS] 正在连接 {url} ...")
        async with websockets.connect(url, additional_headers=headers,
                                      max_size=16 * 1024 * 1024) as ws:
            self.bridge._ws = ws
            self.bridge.connected.set()
            print("[WS] 已连接，开始监听事件\n")
            pump = asyncio.create_task(self.bridge.pump())
            try:
                async for raw in ws:
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if "echo" in event:
                        self.bridge.dispatch_response(event)
                        continue
                    store.add(event)
                    self._print_event(event)
            finally:
                pump.cancel()
                self.bridge._ws = None
                self.bridge.connected.clear()

    NOTICE_NAMES = {
        "group_upload": "群文件上传",
        "group_admin": "群管理员变更",
        "group_decrease": "群成员减少",
        "group_increase": "群成员增加",
        "group_ban": "群禁言",
        "friend_add": "好友添加",
        "group_recall": "群消息撤回",
        "friend_recall": "好友消息撤回",
        "notify": "互动通知",
        "group_card": "群名片变更",
        "offline_file": "离线文件",
        "client_status": "客户端状态",
        "essence": "群精华消息",
    }

    @classmethod
    def _parse_notice(cls, ev: dict) -> str:
        """把 notice 事件转成易读描述。"""
        nt = ev.get("notice_type", "?")
        desc = cls.NOTICE_NAMES.get(nt, nt)

        if nt == "group_upload":
            f = ev.get("file", {})
            return (f"{desc}: 群 {ev.get('group_id')} 的 "
                    f"{ev.get('user_id')} 上传了文件「{f.get('name')}」"
                    f"（{f.get('size', 0) // 1024} KB）")

        if nt == "group_admin":
            act = "设为" if ev.get("sub_type") == "set" else "取消"
            return f"{desc}: 群 {ev.get('group_id')} {act}管理员 {ev.get('user_id')}"

        if nt in ("group_decrease", "group_increase"):
            sub = {"leave": "退群", "kick": "被移出", "kick_me": "Bot被移出",
                   "approve": "批准入群", "invite": "邀请入群"}.get(
                ev.get("sub_type"), ev.get("sub_type"))
            op = f"，操作者 {ev.get('operator_id')}" if ev.get("operator_id") else ""
            return (f"{desc}: {ev.get('user_id')} {sub}"
                    f"（群 {ev.get('group_id')}{op}）")

        if nt == "group_ban":
            if ev.get("sub_type") == "ban":
                secs = ev.get("duration", 0)
                if secs == 0:
                    dur = "永久"
                elif secs % 3600 == 0:
                    dur = f"{secs // 3600} 小时"
                elif secs % 60 == 0:
                    dur = f"{secs // 60} 分钟"
                else:
                    dur = f"{secs} 秒"
                return (f"{desc}: 群 {ev.get('group_id')} "
                        f"{ev.get('user_id')} 被禁言 {dur}"
                        f"（操作者 {ev.get('operator_id')}）")
            return (f"{desc}: 群 {ev.get('group_id')} "
                    f"{ev.get('user_id')} 被解除禁言")

        if nt == "friend_add":
            return f"{desc}: 新增好友 {ev.get('user_id')}"

        if nt in ("group_recall", "friend_recall"):
            who = f"群 {ev.get('group_id')} " if nt == "group_recall" else ""
            return (f"{desc}: {who}消息 {ev.get('message_id')} 被撤回"
                    f"（操作者 {ev.get('operator_id')}）")

        if nt == "notify":
            sub = ev.get("sub_type")
            if sub == "poke":
                suffix = f"，{ev.get('target_id')}" if ev.get("target_id") != ev.get("user_id") else ""
                return (f"戳一戳: 群 {ev.get('group_id')} 内 "
                        f"{ev.get('user_id')}{suffix}")
            if sub == "lucky_king":
                return f"运气王: 群 {ev.get('group_id')} {ev.get('target_id')}"
            if sub == "honor":
                return (f"群荣誉: 群 {ev.get('group_id')} "
                        f"{ev.get('user_id')} 获得 {ev.get('honor_type')}")
            return f"{desc}: {json.dumps(ev, ensure_ascii=False)}"

        return f"{desc}: {json.dumps(ev, ensure_ascii=False)}"

    @classmethod
    def _parse_request(cls, ev: dict) -> str:
        """把 request 事件转成易读描述，附审批提示。"""
        rt = ev.get("request_type")
        if rt == "friend":
            return (f"好友请求: {ev.get('user_id')} 申请添加好友"
                    f"（备注: {ev.get('comment', '')}，flag={ev.get('flag')}）\n"
                    f"    处理: approve 好友 {ev.get('flag')}  或  "
                    f"reject 好友 {ev.get('flag')}")
        if rt == "group":
            sub = "邀请Bot进群" if ev.get("sub_type") == "invite" else "申请加群"
            return (f"加群请求: {ev.get('user_id')} {sub}「{ev.get('group_id')}」"
                    f"（理由: {ev.get('comment', '')}，flag={ev.get('flag')}）\n"
                    f"    处理: approve 群 {ev.get('flag')}  或  "
                    f"reject 群 {ev.get('flag')} [理由]")
        return f"请求:{rt} {json.dumps(ev, ensure_ascii=False)}"

    @classmethod
    def _print_event(cls, event: dict):
        """把事件格式化成易读的消息打印出来。"""
        post_type = event.get("post_type")
        t = time.strftime("%H:%M:%S")

        if post_type == "message":
            msg_type = event.get("message_type")
            sender = event.get("sender", {})
            name = sender.get("card") or sender.get("nickname", "")
            uid = event.get("user_id", "")
            raw_msg = event.get("raw_message", "")
            msg_id = event.get("message_id", "")

            if msg_type == "group":
                role = sender.get("role")
                tag = {"owner": "群主", "admin": "管理", "member": ""}.get(role, "")
                print(f"[{t}] [群消息] 群 {event.get('group_id')} | "
                      f"{tag}{name}({uid}): {raw_msg}  (msg_id={msg_id})")
            elif msg_type == "private":
                sub = {"friend": "", "group": "[临时]", "other": "[其他]"}.get(
                    event.get("sub_type"), "")
                print(f"[{t}] [私聊{sub}] {name}({uid}): {raw_msg}  "
                      f"(msg_id={msg_id})")
            else:
                print(f"[{t}] [消息:{msg_type}] "
                      f"{json.dumps(event, ensure_ascii=False)}")

        elif post_type == "notice":
            print(f"[{t}] [通知] {cls._parse_notice(event)}")

        elif post_type == "request":
            print(f"[{t}] [请求] {cls._parse_request(event)}")

        elif post_type == "meta_event":
            pass

        else:
            print(f"[{t}] [其他事件] {json.dumps(event, ensure_ascii=False)}")


HELP_TEXT = """
可用命令：
  send 群 <群号> <内容>       发送群消息
  send 私 <QQ号> <内容>       发送私聊消息
  reply 群 <群号> [内容]      快速回复该群最近收到的消息（内容留空则读取输入）
  reply 私 <QQ号> [内容]      快速回复该好友最近收到的消息
  groups                      列出所有群（总数 + 群号/群名/成员数）
  friends                     列出所有好友
  me                          查看当前登录账号信息
  history                     查看最近 20 条事件
  history 群 / history 私     只查看群消息 / 私聊消息记录
  approve 好友 <flag>         同意好友请求
  reject 好友 <flag>          拒绝好友请求
  approve 群 <flag>           同意加群/邀请请求
  reject 群 <flag> [理由]     拒绝加群/邀请请求
  raw <action> <json参数>     调用任意 OneBot API（高级用法）
  help                        显示本帮助
  quit / exit                 退出程序
""".strip()


def run_cli(client: OneBotClient, store: EventStore):
    while True:
        try:
            cmd = input("\n>>> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break
        if not cmd:
            continue

        parts = cmd.split(" ", 2)
        op = parts[0].lower()

        try:
            if op in ("quit", "exit"):
                break

            elif op == "help":
                print(HELP_TEXT)

            elif op == "me":
                info = client.get_login_info()
                print(f"当前账号: {info.get('nickname')} (QQ: {info.get('user_id')})")

            elif op == "groups":
                groups = client.get_group_list()
                print(f"共 {len(groups)} 个群：")
                for g in sorted(groups, key=lambda x: x.get("member_count", 0),
                                reverse=True):
                    print(f"  {g.get('group_id'):>12} | {g.get('group_name')} "
                          f"| {g.get('member_count', '?')} 人")

            elif op == "friends":
                friends = client.get_friend_list()
                total = sum(len(c.get("friends", [])) for c in friends)
                print(f"共 {total} 个好友：")
                for cat in friends:
                    for f in cat.get("friends", []):
                        print(f"  {f.get('user_id'):>12} | {f.get('nickname')}")

            elif op == "history":
                scope = parts[1] if len(parts) > 1 else None
                mt = {"群": "group", "私": "private"}.get(scope)
                items = store.recent(msg_type=mt, limit=20)
                if not items:
                    print("暂无记录")
                    continue
                for ts, ev in items:
                    t = time.strftime("%H:%M:%S", time.localtime(ts))
                    if ev.get("post_type") == "message":
                        sender = ev.get("sender", {})
                        name = sender.get("card") or sender.get("nickname", "")
                        loc = (f"群 {ev.get('group_id')}"
                               if ev.get("message_type") == "group"
                               else "私聊")
                        print(f"[{t}] [{loc}] {name}({ev.get('user_id')}): "
                              f"{ev.get('raw_message')}")
                    elif ev.get("post_type") == "notice":
                        print(f"[{t}] [通知] {OneBotClient._parse_notice(ev)}")
                    elif ev.get("post_type") == "request":
                        print(f"[{t}] [请求] {OneBotClient._parse_request(ev)}")
                    else:
                        print(f"[{t}] {json.dumps(ev, ensure_ascii=False)}")

            elif op == "send" and len(parts) == 3:
                scope, target = parts[1], int(parts[2])
                message = input("消息内容: ").strip()
                if scope == "群":
                    ret = client.send_group_msg(target, message)
                    print(f"已发送，message_id = {ret.get('message_id')}")
                elif scope == "私":
                    ret = client.send_private_msg(target, message)
                    print(f"已发送，message_id = {ret.get('message_id')}")
                else:
                    print("用法: send 群|私 <目标号> <内容>")

            elif op == "reply" and len(parts) >= 2:
                scope = parts[1]
                if scope not in ("群", "私"):
                    print("用法: reply 群|私 <目标号> [内容]")
                    continue
                target = int(parts[2].split(" ", 1)[0]) if len(parts) == 3 else None
                message = ""
                if len(parts) == 3 and " " in parts[2]:
                    message = parts[2].split(" ", 1)[1]
                if not message:
                    message = input("回复内容: ").strip()
                if scope == "群":
                    ret = client.send_group_msg(target, message)
                else:
                    ret = client.send_private_msg(target, message)
                print(f"已回复，message_id = {ret.get('message_id')}")

            elif op in ("approve", "reject") and len(parts) >= 3:
                scope, flag = parts[1], parts[2].strip()
                ok = op == "approve"
                if scope == "好友":
                    client.set_friend_add_request(flag, approve=ok)
                    print(f"已{'同意' if ok else '拒绝'}好友请求 {flag}")
                elif scope == "群":
                    reason = "" if ok else input("拒绝理由(可空): ").strip()
                    try:
                        client.set_group_add_request(flag, "invite", approve=ok,
                                                     reason=reason)
                    except RuntimeError:
                        client.set_group_add_request(flag, "add", approve=ok,
                                                     reason=reason)
                    print(f"已{'同意' if ok else '拒绝'}加群/邀请请求 {flag}")
                else:
                    print("用法: approve|reject 好友|群 <flag>")

            elif op == "raw" and len(parts) >= 2:
                action = parts[1]
                params = json.loads(parts[2]) if len(parts) == 3 else {}
                ret = client.api(action, params)
                print(json.dumps(ret, ensure_ascii=False, indent=2))

            else:
                print("无法识别的命令，输入 help 查看帮助")

        except ValueError:
            print("参数格式错误，目标号必须是数字")
        except (requests.ConnectionError, requests.Timeout):
            print("连接失败：请确认 OneBot 服务已启动，且端口/协议正确")
        except Exception as e:  # noqa: BLE001
            print(f"出错: {e}")


def main():
    parser = argparse.ArgumentParser(description="OneBot v11 协议测试工具")
    parser.add_argument("--host", default="127.0.0.1", help="OneBot 服务地址")
    parser.add_argument("--http-port", type=int, default=5700,
                        help="HTTP API 端口（go-cqhttp默认5700 / NapCat默认3000）")
    parser.add_argument("--ws-port", type=int, default=6700,
                        help="WS 事件端口（go-cqhttp默认6700 / NapCat默认3001）")
    parser.add_argument("--token", default=None, help="access_token（如有）")
    parser.add_argument("--https", action="store_true", help="使用 HTTPS/WSS")
    parser.add_argument("--via-ws", action="store_true",
                        help="API 调用也走 WS 通道（收发共用一条连接）")
    parser.add_argument("--no-listen", action="store_true",
                        help="只发不收，不启动 WebSocket 监听")
    args = parser.parse_args()

    client = OneBotClient(args.host, args.http_port, args.ws_port,
                          args.token, args.https, args.via_ws)
    store = EventStore(maxlen=200)

    if not args.no_listen:
        def ws_thread():
            while True:
                try:
                    asyncio.run(client.listen_events(store))
                except (ConnectionRefusedError, websockets.InvalidStatus,
                        OSError) as e:
                    print(f"[WS] 连接失败（{e}），3 秒后重试...")
                    time.sleep(3)
                except Exception as e:  # noqa: BLE001
                    print(f"[WS] 异常: {e}，3 秒后重试...")
                    time.sleep(3)

        threading.Thread(target=ws_thread, daemon=True).start()

    if args.via_ws:
        print("等待 WS 连接...", end="", flush=True)
        if not client.bridge.connected.wait(timeout=15):
            print("\n警告: WS 连接超时，仍可继续尝试")
    elif not args.no_listen:
        time.sleep(0.5)

    try:
        info = client.get_login_info()
        print(f"\r已连接: {info.get('nickname')} (QQ: {info.get('user_id')})")
        groups = client.get_group_list()
        print(f"当前共有 {len(groups)} 个群聊\n")
    except Exception as e:  # noqa: BLE001
        print(f"\r警告: 无法获取登录信息（{e}），仍可尝试发消息\n")

    print("输入 help 查看可用命令\n")
    run_cli(client, store)


if __name__ == "__main__":
    main()