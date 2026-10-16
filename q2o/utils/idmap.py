"""QQ openid (字符串) 与 OneBot 数字 ID 的双向映射。

很多 OneBot 生态程序不支持 openid（甚至会把 user_id 当 QQ 号做格式校验），
因此本模块维护一份「openid <-> 虚拟号」对照表：

- 启用翻译时（默认，配置项 onebot.openid_translate）：
  每个 openid 分配一个符合 QQ 号规则的虚拟号
  （纯数字、首位非 0、5~11 位），从虚拟号池顺序分配，
  双向映射持久化到 data/idmap.json，重启后可继续互相翻译。
- 关闭翻译时：退化为 blake2b 稳定哈希（63 位正整数），
  同样登记双向映射并持久化（兼容旧行为）。

- message id: QQ 消息 id 为 UUID 风格字符串，映射到自增 int32，
  持久化到 data/idmap.json，供撤回/回复等动作反查。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading

log = logging.getLogger("q2o.utils.idmap")

DATA_DIR = "data"
IDMAP_FILE = os.path.join(DATA_DIR, "idmap.json")

# ---------------------------------------------------------------------------
# 虚拟号池规则（与 QQ 号规则一致：纯数字、首位非 0、5~11 位）
#   顺序分配：起始 9 位号段，用完后自然增长到 10 位、11 位，
#   上限 99999999999（11 位）。首位恒为 1，满足「首位非 0」。
#   如需更换号段策略，仅需改动下面两个常量。
# ---------------------------------------------------------------------------
VIRTUAL_ID_MIN = 100_000_000        # 9 位起始（首位非 0）
VIRTUAL_ID_MAX = 99_999_999_999     # 11 位上限（QQ 号最长 11 位）


class IdMapper:
    def __init__(self, persist: bool = True, translate: bool = True):
        self._lock = threading.Lock()
        self._msg_to_int: dict[str, int] = {}
        self._int_to_msg: dict[int, str] = {}
        # openid <-> 数字 ID（翻译模式: 虚拟号 o2v/v2o；哈希模式: o2e/e2o）
        self._open_to_num: dict[str, int] = {}
        self._num_to_open: dict[int, str] = {}
        self._next_virtual = VIRTUAL_ID_MIN
        self._next_msg_id = 1
        self._persist = persist
        self._translate = translate
        if persist:
            self._load()

    # ------------------------------------------------------------ 互译入口
    def register(self, openid: str) -> int:
        """openid -> 数字 ID（翻译模式为虚拟号，否则为哈希值）。

        建立双向映射并持久化；重复注册返回已分配值。
        """
        openid = str(openid)
        if not openid:
            return 0
        with self._lock:
            if openid in self._open_to_num:
                return self._open_to_num[openid]
            num = self._alloc_locked(openid)
            self._open_to_num[openid] = num
            self._num_to_open[num] = openid
            self._save()
            return num

    def resolve_external(self, ext_id: int | str) -> str | None:
        """数字 ID -> openid（register 的逆操作，即"翻译回去"）。

        传入非数字字符串则原样返回（视为已是 openid）。
        """
        if isinstance(ext_id, str) and not ext_id.isdigit():
            return ext_id
        with self._lock:
            return self._num_to_open.get(int(ext_id))

    # 别名，语义更直观：两个方向都可称为"翻译"
    translate = register
    translate_back = resolve_external

    # ------------------------------------------------------------ 分配
    @staticmethod
    def external_id(openid: str) -> int:
        """openid -> 稳定的 63 位正整数（关闭翻译时使用）。"""
        digest = hashlib.blake2b(str(openid).encode(), digest_size=8).digest()
        return int.from_bytes(digest, "big") & 0x3FFF_FFFF_FFFF_FFFF or 1

    def _alloc_locked(self, openid: str) -> int:
        if not self._translate:
            return self.external_id(openid)
        num = self._next_virtual
        while num in self._num_to_open:
            num += 1
        if num > VIRTUAL_ID_MAX:
            log.error("虚拟号池耗尽（>%d），回退为哈希 ID: %s",
                      VIRTUAL_ID_MAX, openid[:16])
            return self.external_id(openid)
        self._next_virtual = num + 1
        return num

    # ------------------------------------------------------------ message
    def message_id(self, qq_msg_id: str) -> int:
        with self._lock:
            if qq_msg_id in self._msg_to_int:
                return self._msg_to_int[qq_msg_id]
            mid = self._next_msg_id
            self._next_msg_id = (self._next_msg_id % 0x7FFF_FFFE) + 1
            self._msg_to_int[qq_msg_id] = mid
            self._int_to_msg[mid] = qq_msg_id
            self._save()
            return mid

    def qq_message_id(self, mid: int) -> str | None:
        with self._lock:
            return self._int_to_msg.get(int(mid))

    # ------------------------------------------------------------ 持久化
    def _load(self) -> None:
        try:
            with open(IDMAP_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._msg_to_int = {k: int(v) for k, v in data.get("m2i", {}).items()}
            self._int_to_msg = {int(k): v for k, v in data.get("i2m", {}).items()}
            # 兼容旧字段名 o2e/e2o，新写入统一为 o2n/n2o
            open_to_num = data.get("o2n", data.get("o2e", {}))
            num_to_open = data.get("n2o", data.get("e2o", {}))
            self._open_to_num = {k: int(v) for k, v in open_to_num.items()}
            self._num_to_open = {int(k): v for k, v in num_to_open.items()}
            self._next_virtual = max(
                int(data.get("next_virtual", VIRTUAL_ID_MIN)),
                VIRTUAL_ID_MIN,
            )
            self._next_msg_id = int(data.get("next", 1))
        except (OSError, ValueError):
            pass

    def _save(self) -> None:
        if not self._persist:
            return
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            tmp = IDMAP_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({
                    "m2i": self._msg_to_int,
                    "i2m": {str(k): v for k, v in self._int_to_msg.items()},
                    "o2n": self._open_to_num,
                    "n2o": {str(k): v for k, v in self._num_to_open.items()},
                    "next_virtual": self._next_virtual,
                    "next": self._next_msg_id,
                }, f)
            os.replace(tmp, IDMAP_FILE)
        except OSError as e:
            log.debug("idmap 持久化失败: %s", e)
