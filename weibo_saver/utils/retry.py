"""异步重试 + 指数退避."""

from __future__ import annotations

import asyncio
import functools
import logging
import random
from typing import Any, Callable, TypeVar

from ..exceptions import WeiboSaverError

logger = logging.getLogger("weibo_saver.utils.retry")

F = TypeVar("F", bound=Callable[..., Any])


class RetryExhaustedError(WeiboSaverError):
    """重试次数用尽."""

    def __init__(self, message: str, *, original_errors: list[Exception] | None = None):
        super().__init__(message)
        self.original_errors = original_errors or []


def async_retry(
    max_retries: int = 5,
    base_delay: float = 2.0,
    max_delay: float = 120.0,
    *,
    retryable_exceptions: tuple[type[Exception], ...] = (
        WeiboSaverError,
        ConnectionError,
        TimeoutError,
        OSError,
    ),
    jitter: bool = True,
):
    """异步函数重试装饰器，带指数退避.

    Args:
        max_retries: 最大重试次数
        base_delay: 基础延迟（秒）
        max_delay: 最大延迟上限（秒）
        retryable_exceptions: 可重试的异常类型
        jitter: 是否添加随机抖动
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_error: Exception | None = None
            errors: list[Exception] = []

            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except retryable_exceptions as e:
                    last_error = e
                    errors.append(e)

                    if attempt >= max_retries:
                        break

                    # 如果异常自带 retry_after 属性，优先使用
                    retry_after = getattr(e, "retry_after", None)
                    if retry_after is not None:
                        delay = min(float(retry_after), max_delay)
                    else:
                        delay = min(base_delay * (2**attempt), max_delay)

                    if jitter:
                        delay *= random.uniform(0.75, 1.25)

                    logger.warning(
                        f"重试 {attempt + 1}/{max_retries} | "
                        f"延迟 {delay:.1f}s | 异常: {e}"
                    )
                    await asyncio.sleep(delay)

            raise RetryExhaustedError(
                f"重试 {max_retries} 次后仍然失败: {last_error}",
                original_errors=errors,
            )

        return wrapper  # type: ignore[return-value]

    return decorator
