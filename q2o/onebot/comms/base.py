"""OneBot 通信连接基类。"""

# by:xiaoliang - 2026.10.17

from __future__ import annotations

import logging
from typing import Any

from ..actions import ActionHandler

log = logging.getLogger("q2o.onebot.comm")


class BaseComm:
    """四种通信方式（正向/反向 HTTP(S)、正向/反向 WS）的统一接口。"""

    def __init__(self, name: str, protocol: int, handler: ActionHandler,
                 self_id: str):
        self.name = name
        self.protocol = protocol
        self.handler = handler
        self.self_id = self_id
        self.log = logging.getLogger(f"q2o.onebot.comm.{name}")

    async def start(self) -> None:  # pragma: no cover
        raise NotImplementedError

    async def stop(self) -> None:  # pragma: no cover
        raise NotImplementedError

    async def push_event(self, event: dict[str, Any]) -> None:  # pragma: no cover
        raise NotImplementedError

    def lifecycle_event(self) -> dict[str, Any]:
        """连接建立后的生命周期事件。"""
        import time
        if self.protocol == 12:
            return {
                "id": f"meta-{time.time_ns()}", "time": time.time(),
                "type": "meta", "detail_type": "connect", "sub_type": "",
                "version": {"impl": "qqbot2onebot", "version": "1.0.0",
                            "onebot_version": "12"},
                "self": {"platform": "qq", "user_id": str(self.self_id)},
            }
        sid = int(self.self_id) if str(self.self_id).isdigit() else 0
        return {"time": int(time.time()), "self_id": sid,
                "post_type": "meta_event", "meta_event_type": "lifecycle",
                "sub_type": "connect"}
