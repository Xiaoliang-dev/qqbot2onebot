"""日志系统：控制台彩色输出，日志文件纯净无色。"""

# by:xiaoliang - 2026.10.17

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

_COLOR = {
    "DEBUG": "\033[36m",
    "INFO": "\033[32m",
    "WARNING": "\033[33m",
    "ERROR": "\033[31m",
    "CRITICAL": "\033[1;31m",
}
_RESET = "\033[0m"

_FMT = "[%(asctime)s] [%(levelname)-7s] [%(name)s] %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


class _ColorFormatter(logging.Formatter):
    """仅控制台使用的彩色格式化器。"""

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        color = _COLOR.get(record.levelname)
        if color:
            return f"{color}{text}{_RESET}"
        return text


def setup_logging(level: str = "INFO",
                  file: str = "logs/q2o.log",
                  max_size_mb: int = 10,
                  backup_count: int = 5) -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()

    sh = logging.StreamHandler()
    sh.setFormatter(_ColorFormatter(_FMT, datefmt=_DATEFMT))
    root.addHandler(sh)

    os.makedirs(os.path.dirname(os.path.abspath(file)), exist_ok=True)
    fh = RotatingFileHandler(
        file, maxBytes=max_size_mb * 1024 * 1024,
        backupCount=backup_count, encoding="utf-8",
    )
    fh.setFormatter(logging.Formatter(_FMT, datefmt=_DATEFMT))
    root.addHandler(fh)

    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
