"""SQLite 数据库：异步操作、WAL 模式、全部 CRUD."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import aiosqlite

from ..constants import SQL_CREATE_TABLES
from ..exceptions import DatabaseError

logger = logging.getLogger("weibo_saver.storage.database")


class Database:
    """异步 SQLite 封装.

    特性:
    - WAL 模式支持并发读写
    - 自动重试 busy 错误
    - 所有操作异步
    """

    def __init__(self, db_path: str | Path):
        self._path = Path(db_path)
        self._conn: aiosqlite.Connection | None = None

    # ---- 生命周期 ----

    async def init(self) -> None:
        """初始化数据库：创建连接、执行 DDL、完整性检查."""
        self._path.parent.mkdir(parents=True, exist_ok=True)

        self._conn = await aiosqlite.connect(
            str(self._path),
            timeout=10.0,
        )
        self._conn.row_factory = aiosqlite.Row

        # 启用 WAL 模式
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.execute("PRAGMA cache_size=-32000")
        await self._conn.execute("PRAGMA busy_timeout=5000")

        # 清理上次崩溃可能残留的 WAL 文件
        try:
            await self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception:
            pass

        # 执行 DDL
        await self._conn.executescript(SQL_CREATE_TABLES)
        await self._conn.commit()

        # 完整性检查
        try:
            cursor = await self._conn.execute("PRAGMA integrity_check")
            row = await cursor.fetchone()
            if row and row[0] != "ok":
                raise DatabaseError(f"数据库完整性检查失败: {row[0]}")
        except DatabaseError:
            raise
        except Exception as e:
            # 非致命，仅记录
            import logging
            logging.getLogger("weibo_saver").warning(f"DB 完整性检查跳过: {e}")

        # 迁移：添加可见性相关字段（兼容旧数据库）
        await self._migrate_visibility()
        await self._conn.commit()

        logger.info(f"数据库已初始化 | path={self._path}")

    async def close(self) -> None:
        """关闭数据库连接."""
        if self._conn:
            await self._conn.close()
            self._conn = None
            logger.info("数据库连接已关闭")

    @property
    def conn(self) -> aiosqlite.Connection:
        """获取数据库连接（需先 init）."""
        if self._conn is None:
            raise DatabaseError("数据库未初始化，请先调用 init()")
        return self._conn

    # ---- 迁移 ----

    async def _migrate_visibility(self) -> None:
        """兼容旧数据库：添加可见性相关字段."""
        migrations = [
            "ALTER TABLE users ADD COLUMN visibility_limit TEXT",
            "ALTER TABLE users ADD COLUMN earliest_visible_post_at TEXT",
            "ALTER TABLE users ADD COLUMN visibility_checked_at TEXT",
            "ALTER TABLE posts ADD COLUMN visibility_hidden INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE crawl_state ADD COLUMN visibility_limit TEXT",
            "ALTER TABLE crawl_state ADD COLUMN earliest_post_date TEXT",
            "ALTER TABLE crawl_state ADD COLUMN hidden_posts_count INTEGER NOT NULL DEFAULT 0",
        ]
        for sql in migrations:
            try:
                await self._conn.execute(sql)
            except Exception:
                pass  # 列已存在则忽略
        await self._conn.commit()

    # ---- Users ----

    async def upsert_user(self, uid: str, screen_name: str, **kwargs: Any) -> None:
        """插入或更新用户."""
        await self.conn.execute(
            """
            INSERT INTO users (uid, screen_name, description, profile_url, avatar_url,
                               followers_count, friends_count, statuses_count,
                               raw_json, first_seen_at, last_updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            ON CONFLICT(uid) DO UPDATE SET
                screen_name=excluded.screen_name,
                description=excluded.description,
                avatar_url=excluded.avatar_url,
                followers_count=excluded.followers_count,
                statuses_count=excluded.statuses_count,
                raw_json=excluded.raw_json,
                last_updated_at=datetime('now')
            """,
            (
                uid,
                screen_name,
                kwargs.get("description", ""),
                kwargs.get("profile_url", ""),
                kwargs.get("avatar_url", ""),
                kwargs.get("followers_count", 0),
                kwargs.get("friends_count", 0),
                kwargs.get("statuses_count", 0),
                kwargs.get("raw_json", ""),
            ),
        )
        await self.conn.commit()

    async def get_user(self, uid: str) -> dict | None:
        """获取用户信息."""
        cursor = await self.conn.execute("SELECT * FROM users WHERE uid=?", (uid,))
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_all_users(self) -> list[dict]:
        """获取所有用户."""
        cursor = await self.conn.execute("SELECT * FROM users")
        return [dict(row) for row in await cursor.fetchall()]

    # ---- Posts ----

    async def upsert_post(self, post: dict) -> None:
        """插入或更新博文."""
        await self.conn.execute(
            """
            INSERT INTO posts (
                post_id, bid, uid, screen_name, text_content, text_html,
                created_at, source, reposts_count, comments_count, attitudes_count,
                is_pinned, is_repost, page_url, region_name,
                current_content_hash, version_count,
                first_seen_at, last_updated_at, last_checked_at, raw_json
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                datetime('now'), datetime('now'), datetime('now'), ?
            )
            ON CONFLICT(post_id, uid) DO UPDATE SET
                text_content=excluded.text_content,
                text_html=excluded.text_html,
                source=excluded.source,
                reposts_count=excluded.reposts_count,
                comments_count=excluded.comments_count,
                attitudes_count=excluded.attitudes_count,
                is_pinned=excluded.is_pinned,
                current_content_hash=excluded.current_content_hash,
                version_count=excluded.version_count,
                last_updated_at=datetime('now'),
                last_checked_at=datetime('now'),
                is_deleted=0,
                raw_json=excluded.raw_json
            """,
            (
                post["post_id"],
                post.get("bid", ""),
                post["uid"],
                post.get("screen_name", ""),
                post.get("text_content", ""),
                post.get("text_html", ""),
                post.get("created_at", ""),
                post.get("source", ""),
                post.get("reposts_count", 0),
                post.get("comments_count", 0),
                post.get("attitudes_count", 0),
                int(post.get("is_pinned", False)),
                int(post.get("is_repost", False)),
                post.get("page_url", ""),
                post.get("region_name", ""),
                post.get("current_content_hash", ""),
                post.get("version_count", 1),
                post.get("raw_json", "{}"),
            ),
        )
        await self.conn.commit()

    async def get_post(self, post_id: str, uid: str) -> dict | None:
        """获取单条博文."""
        cursor = await self.conn.execute(
            "SELECT * FROM posts WHERE post_id=? AND uid=?", (post_id, uid)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_post_by_bid(self, bid: str, uid: str) -> dict | None:
        """通过 BID 获取博文."""
        cursor = await self.conn.execute(
            "SELECT * FROM posts WHERE bid=? AND uid=?", (bid, uid)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def post_exists(self, post_id: str, uid: str) -> bool:
        """检查博文是否已存在."""
        cursor = await self.conn.execute(
            "SELECT 1 FROM posts WHERE post_id=? AND uid=? LIMIT 1",
            (post_id, uid),
        )
        return await cursor.fetchone() is not None

    async def get_newest_post_id(self, uid: str) -> str | None:
        """获取最新的 post_id."""
        cursor = await self.conn.execute(
            "SELECT post_id FROM posts WHERE uid=? ORDER BY post_id DESC LIMIT 1",
            (uid,),
        )
        row = await cursor.fetchone()
        return row["post_id"] if row else None

    async def get_post_count(self, uid: str) -> int:
        """获取用户的博文数量."""
        cursor = await self.conn.execute(
            "SELECT COUNT(*) as cnt FROM posts WHERE uid=? AND is_deleted=0",
            (uid,),
        )
        row = await cursor.fetchone()
        return row["cnt"] if row else 0

    async def mark_post_deleted(self, post_id: str, uid: str) -> None:
        """标记博文已删除."""
        await self.conn.execute(
            "UPDATE posts SET is_deleted=1, last_checked_at=datetime('now') WHERE post_id=? AND uid=?",
            (post_id, uid),
        )
        await self.conn.commit()

    async def get_all_bids(self, uid: str) -> set[str]:
        """获取指定用户的所有 BID."""
        cursor = await self.conn.execute(
            "SELECT bid FROM posts WHERE uid=? AND is_deleted=0", (uid,)
        )
        return {row["bid"] for row in await cursor.fetchall()}

    async def get_post_text(self, post_id: str, uid: str) -> str | None:
        """获取博文文本内容."""
        cursor = await self.conn.execute(
            "SELECT text_content FROM posts WHERE post_id=? AND uid=?",
            (post_id, uid),
        )
        row = await cursor.fetchone()
        return row["text_content"] if row else None

    # ---- Versions ----

    async def create_version(self, version: dict) -> int:
        """创建版本记录，返回新版本号."""
        cursor = await self.conn.execute(
            """
            INSERT INTO post_versions (post_id, uid, version_num, text_content,
                                       text_html, content_hash, diff_from_prev, captured_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                version["post_id"],
                version["uid"],
                version["version_num"],
                version["text_content"],
                version.get("text_html", ""),
                version["content_hash"],
                version.get("diff_from_prev"),
            ),
        )
        await self.conn.commit()
        return version["version_num"]

    async def get_versions(self, post_id: str, uid: str) -> list[dict]:
        """获取博文的所有历史版本."""
        cursor = await self.conn.execute(
            "SELECT * FROM post_versions WHERE post_id=? AND uid=? ORDER BY version_num ASC",
            (post_id, uid),
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def get_latest_version_num(self, post_id: str, uid: str) -> int:
        """获取最新版本号."""
        cursor = await self.conn.execute(
            "SELECT MAX(version_num) as max_v FROM post_versions WHERE post_id=? AND uid=?",
            (post_id, uid),
        )
        row = await cursor.fetchone()
        return row["max_v"] or 1

    # ---- Media ----

    async def upsert_media(self, media: dict) -> None:
        """插入或更新媒体记录."""
        await self.conn.execute(
            """
            INSERT INTO media (post_id, uid, media_type, weibo_pid, original_url,
                               local_path, file_size, file_hash, width, height, duration,
                               downloaded_at, download_status, retry_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), ?, ?)
            ON CONFLICT(post_id, weibo_pid) DO UPDATE SET
                local_path=excluded.local_path,
                file_size=excluded.file_size,
                file_hash=excluded.file_hash,
                download_status=excluded.download_status,
                retry_count=excluded.retry_count
            """,
            (
                media["post_id"],
                media["uid"],
                media["media_type"],
                media.get("weibo_pid", ""),
                media.get("original_url", ""),
                media.get("local_path", ""),
                media.get("file_size", 0),
                media.get("file_hash", ""),
                media.get("width", 0),
                media.get("height", 0),
                media.get("duration", 0.0),
                media.get("download_status", "complete"),
                media.get("retry_count", 0),
            ),
        )
        await self.conn.commit()

    async def get_post_media(self, post_id: str, uid: str) -> list[dict]:
        """获取博文的所有媒体."""
        cursor = await self.conn.execute(
            "SELECT * FROM media WHERE post_id=? AND uid=?",
            (post_id, uid),
        )
        return [dict(row) for row in await cursor.fetchall()]

    async def media_exists(self, post_id: str, weibo_pid: str) -> bool:
        """检查媒体是否已下载."""
        cursor = await self.conn.execute(
            "SELECT 1 FROM media WHERE post_id=? AND weibo_pid=? LIMIT 1",
            (post_id, weibo_pid),
        )
        return await cursor.fetchone() is not None

    # ---- Crawl Sessions ----

    async def create_crawl_session(self, session: dict) -> int:
        """创建爬取会话."""
        cursor = await self.conn.execute(
            """
            INSERT INTO crawl_sessions (uid, started_at, crawl_type, status)
            VALUES (?, datetime('now'), ?, 'running')
            """,
            (session["uid"], session.get("crawl_type", "incremental")),
        )
        await self.conn.commit()
        return cursor.lastrowid

    async def update_crawl_session(self, session_id: int, **kwargs: Any) -> None:
        """更新爬取会话."""
        sets = []
        values: list[Any] = []
        for key, val in kwargs.items():
            if val is not None:
                sets.append(f"{key}=?")
                values.append(val)
        if not sets:
            return
        sets.append("finished_at=datetime('now')")
        values.append(session_id)
        await self.conn.execute(
            f"UPDATE crawl_sessions SET {', '.join(sets)} WHERE id=?",
            values,
        )
        await self.conn.commit()

    async def get_last_crawl_session(self, uid: str) -> dict | None:
        """获取最近的爬取会话."""
        cursor = await self.conn.execute(
            "SELECT * FROM crawl_sessions WHERE uid=? ORDER BY started_at DESC LIMIT 1",
            (uid,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    # ---- Crawl State ----

    async def get_crawl_state(self) -> dict:
        """获取爬取状态."""
        cursor = await self.conn.execute("SELECT * FROM crawl_state WHERE id=1")
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return {
            "id": 1,
            "uid": "",
            "is_first_crawl_complete": 0,
            "last_page_crawled": 0,
            "total_posts_crawled": 0,
            "total_media_downloaded": 0,
            "last_full_crawl_at": None,
            "last_incremental_at": None,
            "last_error": None,
            "last_error_at": None,
            "consecutive_failures": 0,
        }

    async def update_crawl_state(self, **kwargs: Any) -> None:
        """更新爬取状态."""
        # 确保行存在
        await self.conn.execute(
            """
            INSERT OR IGNORE INTO crawl_state (id, uid) VALUES (1, '')
            """
        )
        sets = []
        values: list[Any] = []
        for key, val in kwargs.items():
            if val is not None:
                sets.append(f"{key}=?")
                values.append(val)
        if not sets:
            return
        values.append(1)
        await self.conn.execute(
            f"UPDATE crawl_state SET {', '.join(sets)} WHERE id=?",
            values,
        )
        await self.conn.commit()

    # ---- Visibility ----

    async def update_user_visibility(
        self, uid: str, limit: str, earliest_post_at: str = ""
    ) -> None:
        """更新用户可见性限制."""
        await self.conn.execute(
            """UPDATE users SET visibility_limit=?, earliest_visible_post_at=?,
               visibility_checked_at=datetime('now')
               WHERE uid=?""",
            (limit, earliest_post_at, uid),
        )
        await self.conn.commit()

    async def log_visibility_change(
        self, uid: str, previous_limit: str, new_limit: str,
        earliest_post: str = "", total_visible: int = 0, hidden_count: int = 0,
        notes: str = ""
    ) -> None:
        """记录可见性变更."""
        await self.conn.execute(
            """INSERT INTO visibility_change_log
               (uid, detected_at, previous_limit, new_limit, earliest_post,
                total_visible, hidden_count, notes)
               VALUES (?, datetime('now'), ?, ?, ?, ?, ?, ?)""",
            (uid, previous_limit, new_limit, earliest_post, total_visible, hidden_count, notes),
        )
        await self.conn.commit()

    async def mark_posts_hidden_by_date(
        self, uid: str, before_date: str
    ) -> int:
        """将指定日期之前的博文标记为因可见性限制而隐藏.

        Returns:
            标记的博文数
        """
        cursor = await self.conn.execute(
            """UPDATE posts SET visibility_hidden=1
               WHERE uid=? AND created_at < ? AND visibility_hidden=0""",
            (uid, before_date),
        )
        await self.conn.commit()
        return cursor.rowcount

    async def get_hidden_posts_count(self, uid: str) -> int:
        """获取被隐藏的博文数."""
        cursor = await self.conn.execute(
            "SELECT COUNT(*) as cnt FROM posts WHERE uid=? AND visibility_hidden=1",
            (uid,),
        )
        row = await cursor.fetchone()
        return row["cnt"] if row else 0

    async def get_earliest_visible_post(self, uid: str) -> dict | None:
        """获取最早可见（未隐藏）的博文."""
        cursor = await self.conn.execute(
            """SELECT * FROM posts WHERE uid=? AND visibility_hidden=0
               AND is_deleted=0 AND created_at != ''
               ORDER BY post_id ASC LIMIT 1""",
            (uid,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_user_visibility(self, uid: str) -> dict | None:
        """获取用户的可见性设置."""
        cursor = await self.conn.execute(
            "SELECT visibility_limit, earliest_visible_post_at, visibility_checked_at FROM users WHERE uid=?",
            (uid,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def get_visibility_history(self, uid: str) -> list[dict]:
        """获取用户的可见性变更历史."""
        cursor = await self.conn.execute(
            "SELECT * FROM visibility_change_log WHERE uid=? ORDER BY detected_at DESC LIMIT 20",
            (uid,),
        )
        return [dict(row) for row in await cursor.fetchall()]

    # ---- API Structure Log ----

    async def log_api_structure(
        self,
        endpoint: str,
        response_keys: list[str],
        sample_post_id: str = "",
        hash_changed: bool = False,
        notes: str = "",
    ) -> None:
        """记录 API 响应结构（用于检测 API 变更）."""
        await self.conn.execute(
            """
            INSERT INTO api_structure_log (timestamp, endpoint, response_keys, sample_post_id, hash_changed, notes)
            VALUES (datetime('now'), ?, ?, ?, ?, ?)
            """,
            (endpoint, json.dumps(response_keys), sample_post_id, int(hash_changed), notes),
        )
        await self.conn.commit()

    async def get_api_structure_history(self, endpoint: str) -> list[dict]:
        """获取 API 结构变更历史."""
        cursor = await self.conn.execute(
            "SELECT * FROM api_structure_log WHERE endpoint=? ORDER BY timestamp DESC LIMIT 50",
            (endpoint,),
        )
        return [dict(row) for row in await cursor.fetchall()]
