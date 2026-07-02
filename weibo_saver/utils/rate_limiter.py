"""自适应速率限制器：滑动窗口 + 令牌桶 + 432 退避."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import deque
from dataclasses import dataclass

from ..config import RateLimitConfig

logger = logging.getLogger("weibo_saver.utils.rate_limiter")


@dataclass
class RateLimitStats:
    """速率限制统计."""

    total_requests: int = 0
    total_delays: float = 0.0
    backoff_count: int = 0
    current_penalty: float = 1.0
    requests_in_window: int = 0


class AdaptiveRateLimiter:
    """自适应速率限制器.

    特性:
    - 滑动窗口限制每分钟请求数
    - 请求间随机延迟（防止模式识别）
    - 432 错误后退避 + 惩罚因子
    - 连续成功时逐渐降低惩罚因子
    """

    def __init__(self, config: RateLimitConfig):
        self._config = config
        self._window: deque[float] = deque()
        self._lock = asyncio.Lock()
        self._penalty_multiplier: float = 1.0
        self._consecutive_success: int = 0
        self._stats = RateLimitStats()

    async def acquire(self) -> float:
        """请求前调用，等待直到可以发送请求.

        Returns:
            实际等待的秒数
        """
        async with self._lock:
            now = time.monotonic()

            # 清理 60 秒窗口外的记录
            while self._window and now - self._window[0] > 60.0:
                self._window.popleft()

            # 如果窗口内请求已达上限，等待最旧的请求过期
            if len(self._window) >= self._config.max_requests_per_minute:
                wait_time = 60.0 - (now - self._window[0]) + random.uniform(0, 1.0)
                if wait_time > 0:
                    logger.debug(
                        f"速率限制: 窗口已满 ({len(self._window)}/{self._config.max_requests_per_minute}), "
                        f"等待 {wait_time:.1f}s"
                    )
                    await asyncio.sleep(wait_time)
                    now = time.monotonic()

            # 计算请求间延迟（带抖动和惩罚因子）
            jittered = random.uniform(
                self._config.min_delay_seconds,
                self._config.max_delay_seconds,
            )
            delay = jittered * self._penalty_multiplier

            # 限制最大单次延迟为 30 秒
            delay = min(delay, 30.0)

            if delay > 0:
                await asyncio.sleep(delay)

            # 记录
            self._window.append(time.monotonic())
            self._stats.total_requests += 1
            self._stats.total_delays += delay
            self._stats.current_penalty = self._penalty_multiplier
            self._stats.requests_in_window = len(self._window)

            return delay

    async def report_432(self) -> float:
        """收到 432 错误时调用，执行退避.

        Returns:
            退避等待的秒数
        """
        async with self._lock:
            backoff = random.uniform(30.0, 60.0)
            self._penalty_multiplier = min(self._penalty_multiplier * 1.5, 10.0)
            self._consecutive_success = 0
            self._stats.backoff_count += 1

            logger.warning(
                f"432 退避: 等待 {backoff:.0f}s, "
                f"惩罚因子提升至 {self._penalty_multiplier:.2f}"
            )
            await asyncio.sleep(backoff)
            return backoff

    async def report_success(self) -> None:
        """请求成功后调用，逐渐降低惩罚因子."""
        async with self._lock:
            self._consecutive_success += 1
            if self._consecutive_success >= 10 and self._penalty_multiplier > 1.0:
                self._penalty_multiplier = max(
                    1.0, self._penalty_multiplier / 1.1
                )
                self._consecutive_success = 0
                logger.debug(f"惩罚因子衰减至 {self._penalty_multiplier:.2f}")

    @property
    def penalty(self) -> float:
        """当前惩罚因子."""
        return self._penalty_multiplier

    @property
    def stats(self) -> RateLimitStats:
        """获取统计信息."""
        return self._stats

    def reset(self) -> None:
        """重置所有状态."""
        self._window.clear()
        self._penalty_multiplier = 1.0
        self._consecutive_success = 0
        self._stats = RateLimitStats()
