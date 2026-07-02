"""反爬封锁检测器：检测何时需要从轻量模式升级到浏览器模式."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from ..exceptions import APIStructureChangeError

logger = logging.getLogger("weibo_saver.core.block_detector")


class BlockDetector:
    """检测微博反爬封锁和 API 结构变更.

    封锁信号:
    - HTTP 432: 触发风控
    - 响应为空列表但应该有数据
    - JSON 结构变化（缺少关键字段）
    - 频繁的登录重定向
    """

    # 关键响应字段（用于检测 API 结构变化）
    TIMELINE_REQUIRED_FIELDS = ["ok", "data"]
    TIMELINE_DATA_FIELDS = ["cards"]
    POST_CARD_FIELDS = ["id", "bid", "user", "created_at", "text"]

    def __init__(self):
        self._escalation_score: float = 0.0
        self._432_count: int = 0
        self._empty_response_count: int = 0
        self._structure_mismatch_count: int = 0
        self._last_response_structure: str = ""

    # ---- 封锁检测 ----

    def check_response(
        self,
        status_code: int,
        response_data: dict | None,
        endpoint: str = "",
    ) -> str:
        """检查 API 响应，返回状态.

        Returns:
            "ok" - 正常
            "432" - 触发限流
            "empty" - 空响应
            "structure_change" - API 结构变化
            "blocked" - 疑似被封锁
        """
        # HTTP 432
        if status_code == 432:
            self._432_count += 1
            self._escalation_score += 3.0
            logger.warning(f"检测到 432 错误 | endpoint={endpoint} | 累计: {self._432_count}")
            return "432"

        # 空响应
        if response_data is None or not isinstance(response_data, dict):
            self._empty_response_count += 1
            self._escalation_score += 1.0
            return "empty"

        # 检查顶层结构
        if not response_data.get("ok"):
            self._empty_response_count += 1
            self._escalation_score += 1.0
            return "empty"

        # 检查数据结构是否变化
        data = response_data.get("data", {})
        if isinstance(data, dict) and "cards" in data:
            current_structure = self._compute_structure_hash(data)
            if self._last_response_structure and current_structure != self._last_response_structure:
                self._structure_mismatch_count += 1
                logger.api_change(  # type: ignore[attr-defined]
                    f"API 响应结构发生变化 | endpoint={endpoint} | "
                    f"old_hash={self._last_response_structure[:8]}... | "
                    f"new_hash={current_structure[:8]}..."
                )
                # 结构变化可能是 API 更新，不一定是封锁
                self._escalation_score += 0.5
                self._last_response_structure = current_structure
                return "structure_change"
            self._last_response_structure = current_structure

        # 正常响应
        self._decay_escalation()
        return "ok"

    def check_card_structure(self, card: dict) -> list[str]:
        """检查单条博文卡片的关键字段.

        Returns:
            缺失的字段列表
        """
        mblog = card.get("mblog", card)
        missing = []
        for field in self.POST_CARD_FIELDS:
            # mblog 和 card 可能指向同一对象（当 card 无 mblog 子键时）
            # 此时只需检查 mblog；不同对象时两者都检查
            if field not in mblog and (mblog is card or field not in card):
                missing.append(field)

        if missing:
            logger.api_change(  # type: ignore[attr-defined]
                f"博文卡片缺少关键字段: {missing}"
            )

        return missing

    # ---- 升级判断 ----

    def should_escalate(self) -> bool:
        """是否应该切换到浏览器模式."""
        if self._escalation_score >= 10.0:
            logger.warning(
                f"封锁评分达到 {self._escalation_score:.1f} >= 10.0，建议升级到浏览器模式"
            )
            return True
        return False

    def should_use_browser(self) -> bool:
        """别名."""
        return self.should_escalate()

    @property
    def escalation_score(self) -> float:
        return self._escalation_score

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "escalation_score": self._escalation_score,
            "432_count": self._432_count,
            "empty_response_count": self._empty_response_count,
            "structure_mismatch_count": self._structure_mismatch_count,
        }

    # ---- 重置 ----

    def reset(self) -> None:
        """重置检测状态."""
        self._escalation_score = 0.0
        self._432_count = 0
        self._empty_response_count = 0
        self._structure_mismatch_count = 0

    def _decay_escalation(self) -> None:
        """正常响应时衰减封锁评分."""
        if self._escalation_score > 0:
            self._escalation_score = max(0.0, self._escalation_score - 0.5)

    @staticmethod
    def _compute_structure_hash(data: dict) -> str:
        """计算 API 响应结构的哈希（基于 key 而非 value）."""

        def extract_keys(obj: Any, prefix: str = "") -> list[str]:
            keys: list[str] = []
            if isinstance(obj, dict):
                for k, v in obj.items():
                    full_key = f"{prefix}.{k}" if prefix else k
                    keys.append(full_key)
                    if isinstance(v, (dict, list)):
                        keys.extend(extract_keys(v, full_key))
            elif isinstance(obj, list) and obj:
                # 只检查第一个元素的结构
                keys.extend(extract_keys(obj[0], f"{prefix}[]"))
            return sorted(keys)

        structure_keys = extract_keys(data)
        return hashlib.md5(json.dumps(structure_keys).encode()).hexdigest()
