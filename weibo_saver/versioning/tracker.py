"""版本追踪：检测编辑、创建版本、保存差异."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..models.post import Post
from ..storage.database import Database
from ..storage.file_writer import FileWriter
from ..utils.text_sanitizer import now_iso
from .differ import Differ

logger = logging.getLogger("weibo_saver.versioning.tracker")


class VersionTracker:
    """博文版本管理器.

    职责:
    1. 检测到编辑时创建新版本
    2. 计算版本间差异
    3. 存储差异和版本快照
    4. 更新主博文记录
    """

    def __init__(self, db: Database, file_writer: FileWriter, differ: Differ):
        self._db = db
        self._file_writer = file_writer
        self._differ = differ

    async def track_edit(
        self,
        post: Post,
        uid: str,
        old_text: str,
        screen_name: str = "",
        post_dir: Path | None = None,
    ) -> dict | None:
        """追踪博文编辑.

        Args:
            post: 新版本的博文
            uid: 用户 UID
            old_text: 旧版本文本
            screen_name: 用户昵称（用于目录构建）
            post_dir: 博文目录（可选）

        Returns:
            版本信息字典，如无实质性变更返回 None
        """
        # 检查是否有实质性变更
        if not self._differ.has_substantive_change(old_text, post.text_content):
            logger.debug(f"博文变化太小，跳过版本创建 | bid={post.bid}")
            return None

        # 计算差异
        diff_text = self._differ.unified_diff(
            old_text, post.text_content,
            label_old=f"v{post.version_count}",
            label_new=f"v{post.version_count + 1}",
        )

        if not diff_text:
            return None

        # 获取当前版本号
        current_version = await self._db.get_latest_version_num(post.post_id, uid)
        new_version = current_version + 1

        # 保存版本到数据库
        await self._db.create_version({
            "post_id": post.post_id,
            "uid": uid,
            "version_num": new_version,
            "text_content": post.text_content,
            "text_html": post.text_html,
            "content_hash": post.content_hash,
            "diff_from_prev": diff_text,
        })

        # 保存版本到磁盘
        if post_dir:
            versions_dir = post_dir / "versions"
            await self._file_writer.write_version(post, new_version, diff_text, versions_dir)

        # 更新主博文记录
        await self._db.conn.execute(
            """
            UPDATE posts SET
                text_content=?,
                text_html=?,
                current_content_hash=?,
                version_count=?,
                last_updated_at=datetime('now'),
                last_checked_at=datetime('now')
            WHERE post_id=? AND uid=?
            """,
            (post.text_content, post.text_html, post.content_hash,
             new_version, post.post_id, uid),
        )
        await self._db.conn.commit()

        # 更新 post 对象的版本号
        post.version_count = new_version

        diff_summary = self._differ.diff_summary(diff_text)

        logger.info(
            f"版本已创建 | bid={post.bid} | v{current_version}→v{new_version} | "
            f"summary={diff_summary}"
        )

        return {
            "old_version": current_version,
            "new_version": new_version,
            "diff": diff_text,
            "diff_summary": diff_summary,
            "content_hash": post.content_hash,
        }

    async def get_version_history(self, post_id: str, uid: str) -> list[dict]:
        """获取博文的版本历史."""
        return await self._db.get_versions(post_id, uid)

    async def compare_versions(
        self, post_id: str, uid: str, v1: int, v2: int
    ) -> str:
        """对比两个指定版本."""
        versions = await self._db.get_versions(post_id, uid)
        text_v1 = ""
        text_v2 = ""

        for v in versions:
            if v["version_num"] == v1:
                text_v1 = v["text_content"]
            if v["version_num"] == v2:
                text_v2 = v["text_content"]

        return self._differ.unified_diff(
            text_v1, text_v2,
            label_old=f"v{v1}",
            label_new=f"v{v2}",
        )
