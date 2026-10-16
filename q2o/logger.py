"""日志系统：控制台彩色输出，日志文件纯净无色。"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

# ANSI 颜色
_COLOR = {
    "DEBUG": "\033[36m",      # 青
    "INFO": "\033[32m",       # 绿
    "WARNING": "\033[33m",    # 黄
    "ERROR": "\033[31m",      # 红
    "CRITICAL": "\033[1;31m",  # 加粗红
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

    # 控制台：彩色
    sh = logging.StreamHandler()
    sh.setFormatter(_ColorFormatter(_FMT, datefmt=_DATEFMT))
    root.addHandler(sh)

    # 文件：无颜色符号
    os.makedirs(os.path.dirname(os.path.abspath(file)), exist_ok=True)
    fh = RotatingFileHandler(
        file, maxBytes=max_size_mb * 1024 * 1024,
        backupCount=backup_count, encoding="utf-8",
    )
    fh.setFormatter(logging.Formatter(_FMT, datefmt=_DATEFMT))
    root.addHandler(fh)

    # 抑制第三方库的噪音
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
