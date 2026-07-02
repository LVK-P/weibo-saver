"""监控调度器：60 秒异步循环."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from ..config import Config
from ..core.engine import Engine
from ..utils.text_sanitizer import now_iso, truncate_for_display
from .state import MonitorState, State

logger = logging.getLogger("weibo_saver.monitor.scheduler")


class Scheduler:
    """监控调度器.

    负责:
    - 首次全量抓取
    - 定期增量检查
    - 状态管理
    - 静默时段控制
    - Cookie 定期验证
    """

    def __init__(
        self,
        engine: Engine,
        state: MonitorState,
        config: Config,
    ):
        self._engine = engine
        self._state = state
        self._config = config
        self._stop_event = asyncio.Event()
        self._pause_event = asyncio.Event()
        self._pause_event.set()  # 默认不暂停
        self._status_callback: Any = None

    def set_status_callback(self, callback: Any) -> None:
        """设置状态回调."""
        self._status_callback = callback

    async def run(self) -> None:
        """启动调度循环."""
        logger.info(f"调度器启动 | 监控 {len(self._config.target_uids)} 个用户")

        try:
            # 对所有用户执行首次全量抓取
            for uid in self._config.target_uids:
                crawl_state = await self._engine._db.get_crawl_state()
                if not crawl_state.get("is_first_crawl_complete"):
                    self._state.to_full_crawl()
                    await self._do_full_crawl(uid)
                    self._state.to_incremental()

            # 进入多用户增量监控循环
            while not self._stop_event.is_set():
                # 检查暂停
                await self._pause_event.wait()

                # 检查静默时段
                if self._is_quiet_hours():
                    self._notify_status("静默时段")
                    await self._sleep_with_check(30)
                    continue

                # 对每个用户执行增量检查
                if self._state.current == State.INCREMENTAL:
                    for uid in self._config.target_uids:
                        if self._stop_event.is_set():
                            break
                        await self._do_incremental_check(uid)

                        # 用户间短暂延迟
                        if len(self._config.target_uids) > 1:
                            await asyncio.sleep(2.0)

                # 如果是错误状态，等待恢复
                if self._state.current == State.ERROR:
                    self._notify_status(f"错误: {self._state.last_error}")
                    await self._sleep_with_check(60)
                    self._state.reset()
                    self._state.to_incremental()
                    continue

                # 等待下一个监控周期
                self._notify_status(f"监控 {len(self._config.target_uids)} 用户 - {now_iso()[11:19]}")
                await self._sleep_with_check(self._config.monitor.interval_seconds)

        except asyncio.CancelledError:
            logger.info("调度器被取消")
        except Exception as e:
            logger.critical(f"调度器致命错误: {e}")
            self._state.to_error(str(e))
        finally:
            logger.info("调度器已停止")

    async def stop(self) -> None:
        """停止调度器."""
        logger.info("正在停止调度器...")
        self._stop_event.set()
        self._pause_event.set()  # 解除暂停以便退出

    async def pause(self) -> None:
        """暂停监控."""
        self._state.to_paused()
        self._pause_event.clear()
        self._notify_status("已暂停")
        logger.info("监控已暂停")

    async def resume(self) -> None:
        """恢复监控."""
        self._pause_event.set()
        self._state.to_incremental()
        self._notify_status("监控已恢复")
        logger.info("监控已恢复")

    async def force_full_crawl(self, uid: str) -> None:
        """强制执行全量抓取."""
        self._state.to_full_crawl()
        await self._do_full_crawl(uid)
        self._state.to_incremental()

    # ---- 内部 ----

    async def _do_full_crawl(self, uid: str) -> None:
        """执行全量抓取."""
        self._notify_status(f"全量抓取中: {uid}")
        try:
            stats = await self._engine.full_crawl(uid)
            self._state.full_crawl_page = stats.pages_crawled
            self._notify_status(
                f"全量抓取完成: {stats.new_posts} 条新博文"
            )
        except Exception as e:
            logger.error(f"全量抓取失败: {e}")
            self._state.to_error(f"全量抓取: {e}")

    async def _do_incremental_check(self, uid: str) -> None:
        """执行一次增量检查."""
        try:
            stats = await self._engine.incremental_crawl(uid)
            self._state.last_check_at = now_iso()

            if stats.has_errors:
                self._state.consecutive_failures += 1
                if self._state.consecutive_failures >= 5:
                    self._state.to_error(
                        f"连续 {self._state.consecutive_failures} 次失败"
                    )
            else:
                self._state.reset()

            if stats.new_posts > 0 or stats.updated_posts > 0:
                self._notify_status(
                    f"新博文: {stats.new_posts} | 已编辑: {stats.updated_posts}"
                )

        except Exception as e:
            logger.error(f"增量检查失败: {e}")
            self._state.consecutive_failures += 1
            if self._state.consecutive_failures >= 5:
                self._state.to_error(str(e))

    async def _sleep_with_check(self, seconds: float) -> None:
        """睡眠但允许被 stop/pause 中断."""
        try:
            # 分段睡眠，每 5 秒检查一次
            remaining = seconds
            while remaining > 0 and not self._stop_event.is_set():
                chunk = min(5, remaining)
                await asyncio.sleep(chunk)
                remaining -= chunk
                if self._stop_event.is_set():
                    break
                # 暂停检查在 run() 循环中处理
        except asyncio.CancelledError:
            pass

    def _is_quiet_hours(self) -> bool:
        """检查是否在静默时段."""
        if not self._config.monitor.quiet_hours_enabled:
            return False

        from datetime import datetime, time

        now = datetime.now().time()
        start = time.fromisoformat(self._config.monitor.quiet_hours_start)
        end = time.fromisoformat(self._config.monitor.quiet_hours_end)

        if start <= end:
            return start <= now <= end
        else:
            # 跨天（如 23:00 - 07:00）
            return now >= start or now <= end

    def _notify_status(self, text: str) -> None:
        if self._status_callback:
            try:
                self._status_callback(text)
            except Exception:
                pass
