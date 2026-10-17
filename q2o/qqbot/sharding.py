"""切片(sharding)数量决策。

规则（按需求）:
  - 配置关闭切片 -> 1 片
  - 配置显式数量 count > 0 -> 直接使用
  - count = 0 (auto):
      真实切片数 = round(0.8 * 网关建议切片数 + 0.2 * 主机评估值)
    其中主机评估值综合 CPU 核数与可用内存（网络条件通过连接质量间接反映在
    重连统计中，这里以本机资源为主）。
"""

# by:xiaoliang - 2026.10.17

from __future__ import annotations

import logging
import os

log = logging.getLogger("q2o.sharding")

W_GATEWAY_SUGGESTION = 0.8
W_HOST_ASSESSMENT = 0.2


def _available_memory_gb() -> float:
    """读取可用内存（GB），优先 /proc/meminfo，失败退回保守值。"""
    try:
        with open("/proc/meminfo", "r", encoding="ascii") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) / 1024 / 1024
    except OSError:
        pass
    return 4.0


def host_assessment() -> int:
    """根据本机 CPU 与内存评估可承载的切片数。

    经验模型：每 2 个 CPU 核可稳定承载 1 片；每 2GB 可用内存承载 1 片；
    取二者较小值，范围 [1, 32]。
    """
    cpus = os.cpu_count() or 2
    mem_gb = _available_memory_gb()
    by_cpu = max(1, cpus // 2)
    by_mem = max(1, int(mem_gb // 2))
    est = min(by_cpu, by_mem, 32)
    log.debug("主机评估: cpu=%d mem=%.1fGB -> %d 片", cpus, mem_gb, est)
    return est


def decide_shard_count(suggested: int, enable: bool, count_cfg: int) -> int:
    """决定真实切片数量。

    :param suggested: GET /gateway/bot 返回的建议切片数
    :param enable: 配置 sharding.enable
    :param count_cfg: 配置 sharding.count（0 = auto）
    """
    if not enable:
        return 1
    if count_cfg > 0:
        return count_cfg

    host = host_assessment()
    final = round(W_GATEWAY_SUGGESTION * suggested + W_HOST_ASSESSMENT * host)
    final = max(1, final)
    log.info(
        "切片决策: 网关建议 %d (权重 %.0f%%), 主机评估 %d (权重 %.0f%%) -> %d 片",
        suggested, W_GATEWAY_SUGGESTION * 100,
        host, W_HOST_ASSESSMENT * 100, final,
    )
    return final
