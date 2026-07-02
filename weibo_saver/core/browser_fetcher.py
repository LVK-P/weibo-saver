"""浏览器抓取器（兜底模式）：基于 crawl4weibo / Playwright.

仅在 ApiFetcher 遇到持续封锁时使用。
Playwright 启动开销大（~3-8s），但反爬能力最强。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from ..config import Config
from ..exceptions import APIError, CrawlError
from ..models.post import Post
from ..models.user import User
from ..models.media_item import MediaItem

logger = logging.getLogger("weibo_saver.core.browser_fetcher")


class BrowserFetcher:
    """基于 crawl4weibo 的浏览器抓取器（兜底模式）.

    注意: 这是一个可选组件。如果 crawl4weibo 未安装，会自动降级。
    """

    def __init__(self, config: Config):
        self._config = config
        self._client: Any = None  # WeiboClient
        self._available = False

    async def start(self) -> bool:
        """尝试启动浏览器抓取器.

        Returns:
            是否成功启动
        """
        try:
            from crawl4weibo import WeiboClient

            storage_path = (
                self._config.archive_root / "cookies" / "weibo_storage_state.json"
            )
            storage_path.parent.mkdir(parents=True, exist_ok=True)

            # 在 executor 中创建客户端（Playwright 操作是同步的）
            loop = asyncio.get_event_loop()
            self._client = await loop.run_in_executor(
                None,
                lambda: WeiboClient(
                    login_cookies=True,
                    cookie_storage_path=str(storage_path),
                    browser_headless=True,
                    login_timeout=180,
                ),
            )

            self._available = True
            logger.info("BrowserFetcher (crawl4weibo) 已启动")
            return True

        except ImportError:
            logger.warning(
                "crawl4weibo 未安装，浏览器兜底模式不可用。"
                "安装方法: pip install crawl4weibo && playwright install chromium"
            )
            self._available = False
            return False
        except Exception as e:
            logger.error(f"BrowserFetcher 启动失败: {e}")
            self._available = False
            return False

    async def stop(self) -> None:
        """关闭浏览器抓取器."""
        if self._client:
            try:
                # WeiboClient 的关闭
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._client.close)
            except Exception:
                pass
            self._client = None
        self._available = False
        logger.info("BrowserFetcher 已停止")

    @property
    def is_available(self) -> bool:
        return self._available and self._client is not None

    # ---- 用户 ----

    async def get_user_info(self, uid: str) -> User:
        """获取用户信息."""
        if not self.is_available:
            raise CrawlError("BrowserFetcher 不可用")

        try:
            loop = asyncio.get_event_loop()
            user = await loop.run_in_executor(
                None, lambda: self._client.get_user_by_uid(uid)
            )

            return User(
                uid=str(user.id) if hasattr(user, "id") else uid,
                screen_name=user.screen_name if hasattr(user, "screen_name") else "",
                description=getattr(user, "description", ""),
                followers_count=getattr(user, "followers_count", 0),
                friends_count=getattr(user, "friends_count", 0),
                statuses_count=getattr(user, "statuses_count", 0),
                avatar_url=getattr(user, "avatar_hd", ""),
            )
        except Exception as e:
            raise APIError(
                f"Browser 获取用户信息失败: {e}",
                endpoint=f"get_user_by_uid({uid})",
                detail={"error": str(e)},
            ) from e

    # ---- 时间线 ----

    async def get_timeline(
        self,
        uid: str,
        page: int = 1,
        since_id: str | None = None,
    ) -> list[Post]:
        """获取时间线."""
        if not self.is_available:
            raise CrawlError("BrowserFetcher 不可用")

        try:
            loop = asyncio.get_event_loop()
            raw_posts = await loop.run_in_executor(
                None,
                lambda: self._client.get_user_posts(
                    uid, page=page, expand=True
                ),
            )

            posts: list[Post] = []
            for rp in raw_posts:
                # 跳过转发
                if hasattr(rp, "retweeted_status") and rp.retweeted_status is not None:
                    continue

                post = self._convert_post(rp)
                if post:
                    posts.append(post)

            logger.debug(f"Browser 获取时间线 | uid={uid} | page={page} | posts={len(posts)}")
            return posts

        except Exception as e:
            error_str = str(e)
            if "432" in error_str or "rate" in error_str.lower():
                from ..exceptions import RateLimitError

                raise RateLimitError(
                    f"Browser 触发限流: {e}",
                    status_code=432,
                    endpoint=f"get_user_posts({uid})",
                    retry_after=60.0,
                ) from e
            raise APIError(
                f"Browser 获取时间线失败: {e}",
                endpoint=f"get_user_posts({uid})",
                detail={"error": str(e)},
            ) from e

    # ---- 媒体下载 ----

    async def download_images(self, post: Post, dest_dir: Path) -> list[Path]:
        """下载博文中的所有图片."""
        if not self.is_available or not self._client:
            return []

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self._client.download_post_images(
                    # 需要构造 crawl4weibo 的 post 对象结构
                    type("Post", (), {"id": post.post_id, "pics": [
                        type("Pic", (), {"pid": p.weibo_pid, "large": {"url": p.original_url}})
                        for p in post.pics
                    ]})(),
                    download_dir=str(dest_dir / "images"),
                    subdir="",
                ),
            )

            # 返回下载的文件路径
            images_dir = dest_dir / "images"
            if images_dir.exists():
                return list(images_dir.glob("*"))
            return []

        except Exception as e:
            logger.warning(f"Browser 图片下载失败: {e}")
            return []

    # ---- 内部 ----

    def _convert_post(self, raw_post: Any) -> Post | None:
        """将 crawl4weibo 的 Post 转换为我们的 Post 模型."""
        try:
            text = getattr(raw_post, "text", "")
            text_html = getattr(raw_post, "text_html", text)

            pics: list[MediaItem] = []
            if hasattr(raw_post, "pics") and raw_post.pics:
                for pic in raw_post.pics:
                    pid = getattr(pic, "pid", "")
                    url = ""
                    if hasattr(pic, "large") and pic.large:
                        url = getattr(pic.large, "url", "")
                    pics.append(MediaItem(
                        media_type="image",
                        weibo_pid=pid,
                        original_url=url,
                    ))

            video: MediaItem | None = None
            if hasattr(raw_post, "page_info") and raw_post.page_info:
                pi = raw_post.page_info
                if getattr(pi, "type", "") == "video":
                    stream = getattr(pi, "stream_url", "") or getattr(pi, "mp4_url", "")
                    if stream:
                        video = MediaItem(
                            media_type="video",
                            weibo_pid=f"v_{getattr(raw_post, 'id', '')}",
                            original_url=stream,
                            duration=getattr(pi, "duration", 0.0),
                        )

            post = Post(
                post_id=str(getattr(raw_post, "id", "")),
                bid=str(getattr(raw_post, "bid", "")),
                uid=str(getattr(raw_post, "user", {}).get("id", "") if hasattr(raw_post, "user") else ""),
                screen_name=str(getattr(raw_post, "user", {}).get("screen_name", "") if hasattr(raw_post, "user") else ""),
                text_content=Post._clean_html(text_html),
                text_html=text_html,
                created_at=str(getattr(raw_post, "created_at", "")),
                source=str(getattr(raw_post, "source", "")),
                reposts_count=getattr(raw_post, "reposts_count", 0),
                comments_count=getattr(raw_post, "comments_count", 0),
                attitudes_count=getattr(raw_post, "attitudes_count", 0),
                is_pinned=bool(getattr(raw_post, "is_pinned", False)),
                page_url=f"https://m.weibo.cn/detail/{getattr(raw_post, 'id', '')}",
                pics=pics,
                video=video,
            )
            post.content_hash = post._compute_hash()
            return post

        except Exception as e:
            logger.warning(f"转换 crawl4weibo Post 失败: {e}")
            return None
