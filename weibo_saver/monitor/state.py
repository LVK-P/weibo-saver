"""监控状态机."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("weibo_saver.monitor.state")


class State(str, Enum):
    """监控状态."""

    IDLE = "idle"
    FULL_CRAWL = "full_crawl"
    INCREMENTAL = "incremental"
    PAUSED = "paused"
    ERROR = "error"


# 合法的状态转换
_TRANSITIONS: dict[State, set[State]] = {
    State.IDLE: {State.FULL_CRAWL},
    State.FULL_CRAWL: {State.INCREMENTAL, State.ERROR, State.PAUSED},
    State.INCREMENTAL: {State.PAUSED, State.ERROR, State.FULL_CRAWL},
    State.PAUSED: {State.INCREMENTAL, State.IDLE},
    State.ERROR: {State.INCREMENTAL, State.IDLE},
}


@dataclass
class MonitorState:
    """监控状态机."""

    current: State = State.IDLE
    previous: State | None = None
    full_crawl_page: int = 0
    full_crawl_total: int = 0
    last_check_at: str | None = None
    last_error: str | None = None
    last_error_at: str | None = None
    consecutive_failures: int = 0

    def can_transition(self, to: State) -> bool:
        """检查是否允许转换."""
        return to in _TRANSITIONS.get(self.current, set())

    def transition(self, to: State) -> bool:
        """执行状态转换.

        Returns:
            是否转换成功
        """
        if not self.can_transition(to):
            logger.warning(
                f"不允许的状态转换: {self.current.value} -> {to.value}"
            )
            return False

        self.previous = self.current
        self.current = to
        logger.info(
            f"状态转换: {self.previous.value} -> {self.current.value}"
        )
        return True

    def to_incremental(self) -> None:
        """进入增量监控状态."""
        self.transition(State.INCREMENTAL)

    def to_paused(self) -> None:
        """暂停."""
        self.transition(State.PAUSED)

    def to_error(self, reason: str) -> None:
        """进入错误状态."""
        self.last_error = reason
        from ..utils.text_sanitizer import now_iso

        self.last_error_at = now_iso()
        self.consecutive_failures += 1
        self.transition(State.ERROR)

    def to_full_crawl(self) -> None:
        """开始全量抓取."""
        self.transition(State.FULL_CRAWL)

    def reset(self) -> None:
        """重置状态机."""
        self.consecutive_failures = 0
        self.last_error = None

    @property
    def is_running(self) -> bool:
        return self.current in (State.FULL_CRAWL, State.INCREMENTAL)

    @property
    def is_paused(self) -> bool:
        return self.current == State.PAUSED

    @property
    def has_error(self) -> bool:
        return self.current == State.ERROR
