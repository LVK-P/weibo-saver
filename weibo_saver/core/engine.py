"""核心编排引擎：整合抓取、存储、版本管理、媒体下载.

这是整个程序的核心——所有爬取操作都通过此引擎协调。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from ..config import Config
from ..exceptions import (
    APIError,
    BlockDetectedError,
    CookieExpiredError,
    CrawlError,
    RateLimitError,
)
from ..models.media_item import MediaItem
from ..models.monitor_run import CrawlStats
from ..models.post import Post
from ..models.user import User
from ..storage.database import Database
from ..storage.file_writer import FileWriter
from ..storage.layout import Layout
from ..utils.text_sanitizer import now_iso, truncate_for_display
from .api_fetcher import ApiFetcher
from .block_detector import BlockDetector
from .browser_fetcher import BrowserFetcher
from .dedup import Dedup
from .media_downloader import MediaDownloader
from .proxy_pool import ProxyPool
from .session_manager import SessionManager
from .visibility_detector import VisibilityDetector, VisibilityLimit

logger = logging.getLogger("weibo_saver.core.engine")


class Engine:
    """核心引擎：所有操作的编排者.

    负责:
    1. 全量历史爬取 (full_crawl)
    2. 增量监控爬取 (incremental_crawl)
    3. 新博文处理 (process_new_post)
    4. 编辑检测和版本管理 (process_edited_post)
    5. 在 API 模式和浏览器模式之间切换
    """

    def __init__(
        self,
        config: Config,
        db: Database,
        layout: Layout,
        file_writer: FileWriter,
        session: SessionManager,
        api_fetcher: ApiFetcher,
        browser_fetcher: BrowserFetcher,
        media_dl: MediaDownloader,
        dedup: Dedup,
        block_detector: BlockDetector,
        proxy_pool: ProxyPool | None = None,
    ):
        self._config = config
        self._db = db
        self._layout = layout
        self._file_writer = file_writer
        self._session = session
        self._api = api_fetcher
        self._browser = browser_fetcher
        self._media_dl = media_dl
        self._dedup = dedup
        self._block_detector = block_detector
        self._proxy_pool = proxy_pool
        self._visibility = VisibilityDetector(db)
        self._use_browser: bool = False
        self._running: bool = False

        # 回调
        self._on_status: Any = None  # 状态更新的回调

    # ---- 生命周期 ----

    async def start(self) -> None:
        """启动引擎."""
        self._running = True
        await self._db.init()
        await self._api.start()
        logger.info("引擎已启动")

    async def stop(self) -> None:
        """停止引擎."""
        self._running = False
        await self._api.stop()
        if self._browser.is_available:
            await self._browser.stop()
        await self._db.close()
        logger.info("引擎已停止")

    def set_status_callback(self, callback: Any) -> None:
        """设置状态更新回调（用于托盘 UI）."""
        self._on_status = callback

    def _update_status(self, text: str) -> None:
        if self._on_status:
            try:
                self._on_status(text)
            except Exception:
                pass

    @property
    def is_running(self) -> bool:
        return self._running

    # ---- 全量历史爬取 ----

    async def full_crawl(self, uid: str) -> CrawlStats:
        """首次全量爬取：从第一页开始抓取用户所有历史博文.

        Args:
            uid: 用户 UID

        Returns:
            爬取统计
        """
        stats = CrawlStats()
        start_time = time.monotonic()

        # 获取用户信息
        try:
            user = await self._fetch_user_info(uid)
            screen_name = user.screen_name
            await self._save_user(uid, user)
            self._update_status(f"全量抓取: {screen_name}")
        except Exception as e:
            logger.error(f"获取用户信息失败: {e}")
            stats.errors.append(f"获取用户信息: {e}")
            return stats

        # 预加载已知 BID
        await self._dedup.load_for_user(uid)

        # 创建爬取会话
        session_id = await self._db.create_crawl_session({
            "uid": uid,
            "crawl_type": "full",
        })

        page = 1
        consecutive_empty = 0

        try:
            while self._running:
                self._update_status(f"全量抓取: {screen_name} 第{page}页")

                try:
                    posts = await self._fetch_timeline(uid, page=page)
                except RateLimitError as e:
                    logger.warning(f"全量抓取限流 | page={page} | {e}")
                    stats.errors.append(f"限流 (page {page})")
                    # 尝试升级到浏览器模式
                    if self._block_detector.should_escalate():
                        self._switch_to_browser()
                    await asyncio.sleep(60)
                    continue
                except BlockDetectedError:
                    self._switch_to_browser()
                    continue
                except CookieExpiredError:
                    stats.errors.append("Cookie 已过期")
                    break
                except APIError as e:
                    stats.errors.append(f"API错误 (page {page}): {e}")
                    break

                if not posts:
                    consecutive_empty += 1
                    if consecutive_empty >= 3:
                        logger.info(f"连续 {consecutive_empty} 页空数据，停止全量抓取")
                        break
                    page += 1
                    continue

                consecutive_empty = 0
                stats.pages_crawled += 1
                stats.posts_seen += len(posts)

                all_known = True
                for post in posts:
                    is_new = await self._dedup.is_new(uid, post.bid)

                    if is_new:
                        all_known = False
                        await self._process_new_post(uid, screen_name, post)
                        stats.new_posts += 1
                    else:
                        # 检查是否被编辑
                        edited = await self._check_edit(uid, screen_name, post)
                        if edited:
                            stats.updated_posts += 1

                # 如果整页都是已知且未修改的博文，且第二页之后，可以提前终止
                if all_known and page > 1:
                    logger.info(f"第 {page} 页全部已知，停止全量抓取")
                    break

                page += 1

                # 页间延迟
                await asyncio.sleep(1.0)

        finally:
            stats.elapsed_seconds = time.monotonic() - start_time
            await self._db.update_crawl_session(session_id,
                pages_crawled=stats.pages_crawled,
                posts_seen=stats.posts_seen,
                new_posts=stats.new_posts,
                updated_posts=stats.updated_posts,
                errors_count=len(stats.errors),
                status="completed" if not stats.errors else "completed_with_errors",
                error_log=str(stats.errors) if stats.errors else None,
            )
            await self._db.update_crawl_state(
                uid=uid,
                is_first_crawl_complete=1,
                last_page_crawled=page - 1,
                total_posts_crawled=stats.new_posts,
                last_full_crawl_at=now_iso(),
                consecutive_failures=0 if crawl_ok else 1,
            )

            # 检测可见时间限制
            try:
                vis_result = await self._visibility.detect_after_full_crawl(
                    uid, stats.new_posts, ""
                )
                limit_label = VisibilityDetector.format_limit(vis_result["limit"])
                self._update_status(
                    f"可见性检测: {limit_label} | 最早: {vis_result.get('earliest_post_date', '?')[:10]}"
                )
                logger.info(
                    f"可见性检测 | uid={uid} | limit={vis_result['limit']} | "
                    f"days={vis_result.get('days_visible', 0)}"
                )
                if vis_result.get("changed"):
                    logger.warning(
                        f"可见性限制已变更 | uid={uid} | {vis_result['limit']}"
                    )
            except Exception as e:
                logger.warning(f"可见性检测失败 | uid={uid} | {e}")

        logger.info(
            f"全量抓取完成 | uid={uid} | pages={stats.pages_crawled} | "
            f"new={stats.new_posts} | updated={stats.updated_posts} | "
            f"errors={len(stats.errors)} | elapsed={stats.elapsed_seconds:.0f}s"
        )
        return stats

    # ---- 增量爬取 ----

    async def incremental_crawl(self, uid: str) -> CrawlStats:
        """增量爬取：只检查最新页面，发现新博文或编辑.

        Args:
            uid: 用户 UID

        Returns:
            爬取统计
        """
        stats = CrawlStats()
        start_time = time.monotonic()

        # 确保 dedup 已加载
        await self._dedup.load_for_user(uid)

        # 获取用户信息（取 screen_name）
        user_rec = await self._db.get_user(uid)
        screen_name = user_rec.get("screen_name", "") if user_rec else ""

        session_id = await self._db.create_crawl_session({
            "uid": uid,
            "crawl_type": "monitor_check",
        })

        try:
            posts = await self._fetch_timeline(uid, page=1)
            stats.posts_seen = len(posts)

            for post in posts:
                is_new = await self._dedup.is_new(uid, post.bid)

                if is_new:
                    await self._process_new_post(uid, screen_name, post)
                    stats.new_posts += 1
                else:
                    edited = await self._check_edit(uid, screen_name, post)
                    if edited:
                        stats.updated_posts += 1

            # 更新状态
            await self._db.update_crawl_state(
                uid=uid,
                last_incremental_at=now_iso(),
                consecutive_failures=0,
                total_posts_crawled=await self._db.get_post_count(uid),
            )

            # 检测可见性变更（仅在成功时）
            try:
                vis_result = await self._visibility.detect_during_monitoring(uid)
                if vis_result.get("changed"):
                    self._update_status(
                        f"可见性变更: {VisibilityDetector.format_limit(vis_result['limit'])}"
                        + (f" | 隐藏{vis_result['hidden_count']}条" if vis_result.get("hidden_count") else "")
                    )
            except Exception as e:
                logger.debug(f"可见性检测跳过 | uid={uid} | {e}")

        except (RateLimitError, BlockDetectedError):
            # 升级到浏览器模式，下次使用
            if self._block_detector.should_escalate():
                self._switch_to_browser()
            stats.errors.append("限流（下次将使用浏览器模式）")
        except CookieExpiredError:
            stats.errors.append("Cookie 已过期")
            await self._db.update_crawl_state(consecutive_failures=1)
        except APIError as e:
            stats.errors.append(str(e))
            await self._db.update_crawl_state(
                consecutive_failures=1,
                last_error=str(e),
                last_error_at=now_iso(),
            )
        except Exception as e:
            stats.errors.append(str(e))
            logger.error(f"增量抓取异常: {e}")
            await self._db.update_crawl_state(
                consecutive_failures=1,
                last_error=str(e),
                last_error_at=now_iso(),
            )
        finally:
            stats.elapsed_seconds = time.monotonic() - start_time
            await self._db.update_crawl_session(session_id,
                posts_seen=stats.posts_seen,
                new_posts=stats.new_posts,
                updated_posts=stats.updated_posts,
                errors_count=len(stats.errors),
                status="completed" if not stats.errors else "completed_with_errors",
                error_log=str(stats.errors) if stats.errors else None,
            )

        if stats.new_posts > 0 or stats.updated_posts > 0:
            logger.info(
                f"增量抓取 | uid={uid} | new={stats.new_posts} | "
                f"updated={stats.updated_posts} | elapsed={stats.elapsed_seconds:.1f}s"
            )

        return stats

    # ---- 博文处理 ----

    async def _process_new_post(self, uid: str, screen_name: str, post: Post) -> None:
        """处理新博文：保存到数据库和磁盘."""
        try:
            # 构建目录
            post_dir = self._layout.post_dir(screen_name, uid, post.created_at, post.bid)
            self._layout.ensure_dirs(
                post_dir,
                self._layout.images_dir(post_dir),
                self._layout.videos_dir(post_dir),
                self._layout.versions_dir(post_dir),
            )

            # 下载媒体
            media_count = await self._media_dl.download_all(post, post_dir)

            # 写入文件
            await self._file_writer.write_post(post, post_dir)

            # 可选：保存原始响应
            if self._config.output.save_raw_response and post.raw_card:
                await self._file_writer.write_raw_response(post.raw_card, post_dir)

            # 存入数据库
            post_dict = post.to_dict()
            post_dict.update({
                "bid": post.bid,
                "uid": uid,
                "screen_name": screen_name,
                "text_content": post.text_content,
                "text_html": post.text_html,
                "current_content_hash": post.content_hash,
                "raw_json": "",  # 原始 JSON 已存文件，DB 中省略
            })
            await self._db.upsert_post(post_dict)

            # 记录媒体
            for pic in post.pics:
                await self._db.upsert_media({
                    "post_id": post.post_id,
                    "uid": uid,
                    "media_type": pic.media_type,
                    "weibo_pid": pic.weibo_pid,
                    "original_url": pic.original_url,
                    "local_path": pic.local_path,
                    "file_size": pic.file_size,
                    "file_hash": pic.file_hash,
                    "width": pic.width,
                    "height": pic.height,
                })
            if post.video and post.video.local_path:
                await self._db.upsert_media({
                    "post_id": post.post_id,
                    "uid": uid,
                    "media_type": "video",
                    "weibo_pid": post.video.weibo_pid,
                    "original_url": post.video.original_url,
                    "local_path": post.video.local_path,
                    "file_size": post.video.file_size,
                    "file_hash": post.video.file_hash,
                    "duration": post.video.duration,
                })

            # 标记已见
            await self._dedup.mark_seen(uid, post.bid)

            # 日志
            preview = truncate_for_display(post.text_content, 50)
            logger.info(
                f"新博文已保存 | uid={uid} | bid={post.bid} | "
                f"media={media_count} | text=\"{preview}\""
            )

        except Exception as e:
            logger.error(f"处理新博文失败 | bid={post.bid} | {e}")
            raise

    async def _check_edit(self, uid: str, screen_name: str, post: Post) -> bool:
        """检查已知博文是否被编辑.

        Returns:
            True 如果检测到编辑并已完成版本保存
        """
        stored_hash = await self._dedup.get_stored_hash(uid, post.bid)

        if stored_hash and stored_hash != post.content_hash:
            # 博文已被编辑
            return await self._process_edited_post(uid, screen_name, post)

        return False

    async def _process_edited_post(
        self, uid: str, screen_name: str, post: Post
    ) -> bool:
        """处理已编辑博文：创建新版本、保存差异."""
        try:
            from ..versioning.differ import Differ
            from ..versioning.tracker import VersionTracker

            tracker = VersionTracker(self._db, self._file_writer, Differ())

            old_text = await self._db.get_post_text(post.post_id, uid)
            if not old_text:
                logger.warning(f"无法获取旧文本 | bid={post.bid}")
                return False

            result = await tracker.track_edit(
                post=post,
                uid=uid,
                old_text=old_text,
                screen_name=screen_name,
                post_dir=self._layout.post_dir(screen_name, uid, post.created_at, post.bid),
            )

            if result:
                preview = truncate_for_display(post.text_content, 50)
                logger.info(
                    f"博文已编辑 | uid={uid} | bid={post.bid} | "
                    f"v{result['old_version']}→v{result['new_version']} | "
                    f"diff_summary={result.get('diff_summary', '')}"
                )
                self._update_status(
                    f"检测到编辑: {screen_name} - {preview}"
                )
                return True

            return False

        except Exception as e:
            logger.error(f"处理编辑博文失败 | bid={post.bid} | {e}")
            return False

    # ---- 用户 ----

    async def _fetch_user_info(self, uid: str) -> User:
        """获取用户信息（自动选择抓取器）."""
        if self._use_browser and self._browser.is_available:
            try:
                return await self._browser.get_user_info(uid)
            except Exception:
                self._use_browser = False  # 降级

        return await self._api.get_user_info(uid)

    async def _save_user(self, uid: str, user: User) -> None:
        """保存用户信息."""
        await self._db.upsert_user(uid, user.screen_name,
            description=user.description,
            profile_url=user.profile_url,
            avatar_url=user.avatar_url,
            followers_count=user.followers_count,
            friends_count=user.friends_count,
            statuses_count=user.statuses_count,
        )
        user_dir = self._layout.user_dir(user.screen_name, uid)
        await self._file_writer.write_user_profile(user.__dict__, user_dir)

    # ---- 时间线 ----

    async def _fetch_timeline(
        self, uid: str, page: int = 1, since_id: str | None = None
    ) -> list[Post]:
        """获取时间线（自动选择抓取器 + 自动升级）."""
        # 如果已经在浏览器模式，直接用
        if self._use_browser and self._browser.is_available:
            try:
                return await self._browser.get_timeline(uid, page, since_id)
            except Exception as e:
                logger.warning(f"浏览器模式失败，尝试降级: {e}")
                self._use_browser = False

        # API 模式
        try:
            return await self._api.get_timeline(uid, page, since_id)
        except RateLimitError:
            # 触发升级检查
            if self._block_detector.should_escalate():
                self._switch_to_browser()
                raise BlockDetectedError("已升级到浏览器模式")
            raise
        except Exception:
            # 尝试切换到浏览器
            if self._browser.is_available and not self._use_browser:
                self._switch_to_browser()
                return await self._browser.get_timeline(uid, page, since_id)
            raise

    # ---- 模式切换 ----

    def _switch_to_browser(self) -> None:
        """切换到浏览器兜底模式."""
        if self._browser.is_available and not self._use_browser:
            self._use_browser = True
            logger.warning("已切换到浏览器兜底模式 (crawl4weibo)")
            self._update_status("切换到浏览器模式（反爬）")

    def switch_to_api(self) -> None:
        """切回 API 轻量模式."""
        self._use_browser = False
        self._block_detector.reset()
        logger.info("已切回 API 轻量模式")

    @property
    def current_mode(self) -> str:
        """当前抓取模式."""
        return "browser" if self._use_browser else "api"
