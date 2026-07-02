"""日志系统：文件轮转 + 控制台输出 + 结构化错误记录."""

from __future__ import annotations

import logging
import logging.handlers
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# 自定义日志级别：API 结构变更
API_CHANGE_LEVEL = 35  # 介于 WARNING(30) 和 ERROR(40) 之间
logging.addLevelName(API_CHANGE_LEVEL, "API_CHANGE")


def api_change(self: logging.Logger, message: str, *args: Any, **kwargs: Any) -> None:
    """记录 API 结构变更（自定义日志级别）."""
    if self.isEnabledFor(API_CHANGE_LEVEL):
        self._log(API_CHANGE_LEVEL, message, args, **kwargs)


logging.Logger.api_change = api_change  # type: ignore[attr-defined]


class StructuredFormatter(logging.Formatter):
    """结构化日志格式化器，方便后续分析."""

    def format(self, record: logging.LogRecord) -> str:
        """格式化为 key=value 风格."""
        extra_fields = []
        for key in ("uid", "endpoint", "status_code", "elapsed", "session_id"):
            val = getattr(record, key, None)
            if val is not None:
                extra_fields.append(f"{key}={val}")

        base = super().format(record)
        if extra_fields:
            base = f"{base} | {' '.join(extra_fields)}"
        return base


def configure_logging(
    log_dir: str | Path,
    level: str = "INFO",
    max_files: int = 30,
    max_size_mb: int = 10,
    *,
    console: bool = False,
) -> logging.Logger:
    """配置全局日志系统.

    Args:
        log_dir: 日志文件目录
        level: 日志级别 (DEBUG/INFO/WARNING/ERROR)
        max_files: 保留的日志文件数量
        max_size_mb: 单个日志文件最大 MB
        console: 是否同时输出到控制台

    Returns:
        根 logger
    """
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger("weibo_saver")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # 避免重复添加 handler
    if root.handlers:
        return root

    # ---- 通用日志：所有级别 ----
    app_log_path = log_dir / "weibo_saver.log"
    app_handler = logging.handlers.RotatingFileHandler(
        app_log_path,
        maxBytes=max_size_mb * 1024 * 1024,
        backupCount=max_files,
        encoding="utf-8",
    )
    app_handler.setLevel(logging.DEBUG)
    app_handler.setFormatter(
        StructuredFormatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(app_handler)

    # ---- 错误日志：WARNING 及以上 ----
    error_log_path = log_dir / "weibo_saver_error.log"
    error_handler = logging.handlers.RotatingFileHandler(
        error_log_path,
        maxBytes=max_size_mb * 1024 * 1024,
        backupCount=max_files,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.WARNING)
    error_handler.setFormatter(
        StructuredFormatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(error_handler)

    # ---- API 变更日志：专用文件 ----
    api_log_path = log_dir / "weibo_saver_api_changes.log"
    api_handler = logging.handlers.RotatingFileHandler(
        api_log_path,
        maxBytes=max_size_mb * 1024 * 1024,
        backupCount=max_files,
        encoding="utf-8",
    )
    api_handler.setLevel(API_CHANGE_LEVEL)
    api_handler.setFormatter(
        StructuredFormatter(
            "%(asctime)s [API_CHANGE] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(api_handler)

    # ---- 崩溃日志：未捕获异常 ----
    crash_log_path = log_dir / "weibo_saver_crash.log"

    def _crash_handler(exc_type: type, exc_value: BaseException, exc_tb: Any) -> None:
        """全局未捕获异常处理器."""
        tb_lines = traceback.format_exception(exc_type, exc_value, exc_tb)
        crash_msg = "".join(tb_lines)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        with open(crash_log_path, "a", encoding="utf-8") as f:
            f.write(f"\n{'=' * 80}\n")
            f.write(f"CRASH at {now}\n")
            f.write(f"{'=' * 80}\n")
            f.write(crash_msg)
            f.write(f"{'=' * 80}\n")

        # 同时写入通用日志
        root.critical(f"未捕获异常导致崩溃:\n{crash_msg}")

        # 调用默认处理器（打印到 stderr）
        sys.__excepthook__(exc_type, exc_value, exc_tb)  # type: ignore[attr-defined]

    sys.excepthook = _crash_handler

    # ---- 控制台输出（可选） ----
    if console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(getattr(logging, level.upper(), logging.INFO))
        console_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        root.addHandler(console_handler)

    root.info(f"日志系统已初始化 | log_dir={log_dir} | level={level}")
    return root


def get_logger(name: str) -> logging.Logger:
    """获取模块级别的 logger."""
    return logging.getLogger(f"weibo_saver.{name}")
