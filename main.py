#!/usr/bin/env python3
"""qqbot2onebot 程序入口。

用法:
    python main.py [-c config.yaml]
"""

# by:xiaoliang - 2026.10.17

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from q2o.app import Application
from q2o.config import ConfigError, load_config
from q2o.logger import setup_logging


def main() -> int:
    parser = argparse.ArgumentParser(
        description="QQ 官方 Bot 协议 -> OneBot 11/12 协议转换器")
    parser.add_argument("-c", "--config", default="config.yaml",
                        help="配置文件路径（默认 config.yaml，不存在则自动生成）")
    args = parser.parse_args()

    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        print(f"[q2o] 配置错误: {e}")
        return 2

    setup_logging(cfg.logging.level, cfg.logging.file,
                  cfg.logging.max_size_mb, cfg.logging.backup_count)
    log = logging.getLogger("q2o")
    log.info("qqbot2onebot 启动中 (config=%s)", args.config)

    app = Application(cfg)
    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        log.info("收到退出信号，正在停止...")
    return 0


if __name__ == "__main__":
    sys.exit(main())
