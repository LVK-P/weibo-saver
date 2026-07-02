"""差异计算引擎：基于 difflib 的统一差异."""

from __future__ import annotations

import difflib
import logging

from ..utils.text_sanitizer import normalize_text

logger = logging.getLogger("weibo_saver.versioning.differ")


class Differ:
    """文本差异计算器."""

    def __init__(self, context_lines: int = 3):
        self._context_lines = context_lines

    def unified_diff(
        self,
        old_text: str,
        new_text: str,
        label_old: str = "old",
        label_new: str = "new",
    ) -> str:
        """生成统一格式的差异.

        Args:
            old_text: 旧文本
            new_text: 新文本
            label_old: 旧版本标签
            label_new: 新版本标签

        Returns:
            unified diff 字符串，无差异时返回空字符串
        """
        # 规范化
        old_normalized = normalize_text(old_text)
        new_normalized = normalize_text(new_text)

        if old_normalized == new_normalized:
            return ""

        old_lines = old_text.splitlines(keepends=True)
        new_lines = new_text.splitlines(keepends=True)

        diff = difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=label_old,
            tofile=label_new,
            n=self._context_lines,
        )

        return "".join(diff)

    def diff_summary(self, diff_text: str) -> str:
        """生成差异摘要.

        Args:
            diff_text: unified diff 字符串

        Returns:
            人类可读的摘要
        """
        if not diff_text:
            return "无变化"

        added = 0
        removed = 0
        hunks = 0

        for line in diff_text.splitlines():
            if line.startswith("@@"):
                hunks += 1
            elif line.startswith("+") and not line.startswith("+++"):
                added += 1
            elif line.startswith("-") and not line.startswith("---"):
                removed += 1

        parts: list[str] = []
        if added:
            parts.append(f"+{added}行")
        if removed:
            parts.append(f"-{removed}行")
        if hunks:
            parts.append(f"{hunks}处改动")

        return ", ".join(parts) if parts else "格式变化"

    def has_substantive_change(
        self,
        old_text: str,
        new_text: str,
        min_change_ratio: float = 0.05,
    ) -> bool:
        """判断是否为实质性变更（过滤极小改动）.

        Args:
            old_text: 旧文本
            new_text: 新文本
            min_change_ratio: 最小变化比例

        Returns:
            是否为实质性变更
        """
        old_norm = normalize_text(old_text)
        new_norm = normalize_text(new_text)

        if old_norm == new_norm:
            return False

        # 长度变化超过阈值
        len_old = len(old_norm)
        len_new = len(new_norm)
        if len_old > 0:
            ratio = abs(len_new - len_old) / len_old
            if ratio > min_change_ratio:
                return True

        # 单词级比较
        old_words = set(old_norm.split())
        new_words = set(new_norm.split())
        total_words = old_words | new_words
        if total_words:
            changed_words = old_words ^ new_words
            if len(changed_words) / len(total_words) > min_change_ratio:
                return True

        return False
