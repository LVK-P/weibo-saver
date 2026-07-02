"""去重与编辑检测."""

from __future__ import annotations

import logging
from typing import Any

from ..storage.database import Database

logger = logging.getLogger("weibo_saver.core.dedup")


class Dedup:
    """博文去重管理.

    使用内存集合 + 数据库双向检查。
    首次初始化时从 DB 预加载所有已知 BID。
    """

    def __init__(self, database: Database):
        self._db = database
        self._known_bids: dict[str, set[str]] = {}  # uid -> {bids}
        self._loaded: set[str] = set()  # 已预加载的 uid

    async def load_for_user(self, uid: str) -> None:
        """为指定用户预加载已知 BID."""
        if uid in self._loaded:
            return
        bids = await self._db.get_all_bids(uid)
        self._known_bids[uid] = bids
        self._loaded.add(uid)
        logger.debug(f"Dedup 预加载 | uid={uid} | known_bids={len(bids)}")

    async def is_new(self, uid: str, bid: str) -> bool:
        """检查博文是否为新发布的."""
        if uid not in self._loaded:
            await self.load_for_user(uid)
        return bid not in self._known_bids.get(uid, set())

    async def is_known(self, uid: str, bid: str) -> bool:
        """检查博文是否为已知的."""
        return not await self.is_new(uid, bid)

    async def mark_seen(self, uid: str, bid: str) -> None:
        """标记博文为已处理."""
        if uid not in self._known_bids:
            self._known_bids[uid] = set()
        self._known_bids[uid].add(bid)

    async def get_stored_hash(self, uid: str, bid: str) -> str | None:
        """获取已存储的文本哈希."""
        post = await self._db.get_post_by_bid(bid, uid)
        if post:
            return post.get("current_content_hash")
        return None

    async def get_stored_text(self, uid: str, bid: str) -> str | None:
        """获取已存储的文本内容."""
        post = await self._db.get_post_by_bid(bid, uid)
        if post:
            return post.get("text_content")
        return None

    def has_loaded(self, uid: str) -> bool:
        """是否已加载该用户的数据."""
        return uid in self._loaded

    def clear(self, uid: str | None = None) -> None:
        """清除缓存."""
        if uid:
            self._known_bids.pop(uid, None)
            self._loaded.discard(uid)
        else:
            self._known_bids.clear()
            self._loaded.clear()
