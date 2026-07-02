"""工具函数：文本清理、文件名处理."""

from __future__ import annotations

import re
import unicodedata

# Windows 文件名非法字符
_ILLEGAL_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# 多个空白字符
_MULTI_SPACE = re.compile(r"\s+")
# HTML 标签
_HTML_TAG = re.compile(r"<[^>]+>")


def sanitize_filename(name: str, replacement: str = "_") -> str:
    """替换 Windows 文件名中的非法字符.

    Args:
        name: 原始文件名
        replacement: 替换字符

    Returns:
        安全的文件名
    """
    name = _ILLEGAL_FILENAME_CHARS.sub(replacement, name)
    name = name.strip(". ")
    # 限制长度（Windows 路径最大 260 字符，保守处理）
    if len(name) > 200:
        name = name[:200]
    return name or "untitled"


def strip_html(text: str) -> str:
    """移除 HTML 标签.

    Args:
        text: 可能包含 HTML 的文本

    Returns:
        纯文本
    """
    return _HTML_TAG.sub("", text)


def normalize_text(text: str) -> str:
    """规范化文本（用于对比）.

    - Unicode NFC 规范化
    - 合并连续空白
    - 去除首尾空白

    Args:
        text: 原始文本

    Returns:
        规范化后的文本
    """
    text = unicodedata.normalize("NFC", text)
    text = _MULTI_SPACE.sub(" ", text)
    return text.strip()


def truncate_for_display(text: str, max_len: int = 80) -> str:
    """截断文本用于日志/托盘显示.

    Args:
        text: 原始文本
        max_len: 最大长度

    Returns:
        截断后的文本
    """
    text = text.replace("\n", " ")
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def format_duration(seconds: float) -> str:
    """格式化时间长度.

    Args:
        seconds: 秒数

    Returns:
        人类可读的时间长度字符串
    """
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{int(m)}m {int(s)}s"
    else:
        h, r = divmod(seconds, 3600)
        m, s = divmod(r, 60)
        return f"{int(h)}h {int(m)}m {int(s)}s"


def now_iso() -> str:
    """以 ISO 8601 格式返回当前 UTC 时间字符串."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
