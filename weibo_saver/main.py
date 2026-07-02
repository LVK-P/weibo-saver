"""主入口：连接所有组件，启动监控和托盘.

Usage:
    python -m weibo_saver --uid 1234567890
    python -m weibo_saver --once --uid 1234567890
    python -m weibo_saver --export-config config.json

Environment:
    WEIBO_COOKIE_STRING - 直接提供 Cookie 字符串（跳过浏览器提取）
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from .config import Config
from .core.api_fetcher import ApiFetcher
from .core.block_detector import BlockDetector
from .core.browser_fetcher import BrowserFetcher
from .core.dedup import Dedup
from .core.engine import Engine
from .core.media_downloader import MediaDownloader
from .core.proxy_pool import ProxyPool, ProxyStrategy
from .core.session_manager import SessionManager
from .exceptions import ConfigError, CookieError
from .monitor.scheduler import Scheduler
from .monitor.state import MonitorState
from .storage.database import Database
from .storage.file_writer import FileWriter
from .storage.layout import Layout
from .ui.tray import TrayApp
from .utils.logging_setup import configure_logging
from .utils.rate_limiter import AdaptiveRateLimiter

logger: logging.Logger | None = None


async def async_main() -> int:
    """异步主入口.

    Returns:
        退出码
    """
    global logger

    # ---- 1. 加载配置 ----
    config = Config.from_cli()

    # 导出默认配置
    args = Config._parse_args()
    if args.export_config:
        config.save(args.export_config)
        print(f"默认配置已导出到: {args.export_config}")
        return 0

    # ---- 2. 验证配置 ----
    issues = config.validate()
    if issues:
        for issue in issues:
            print(f"[WARNING] {issue}")

    # ---- 3. 初始化日志 ----
    log_dir = config.archive_root / "logs"
    logger = configure_logging(
        log_dir,
        level=config.logging.level,
        max_files=config.logging.max_log_files,
        max_size_mb=config.logging.max_log_size_mb,
        console=args.verbose or args.list_cookies or args.once,
    )
    logger.info(f"Weibo Saver v1.0.0 启动")
    logger.info(f"存档根目录: {config.archive_root}")
    logger.info(f"目标用户: {config.target_uids}")

    # ---- 4. 提取 Cookie ----
    session = SessionManager(browser=config.browser)

    # 优先使用环境变量
    cookie_env = os.environ.get("WEIBO_COOKIE_STRING")
    if cookie_env:
        logger.info("使用环境变量 WEIBO_COOKIE_STRING 中的 Cookie")
        session.load_cookies_from_string(cookie_env)
    else:
        try:
            session.extract_from_browser()
        except CookieError as e:
            logger.error(f"Cookie 提取失败: {e}")
            print(f"[ERROR] Cookie 提取失败: {e}")
            print("请确保:")
            print("  1. 已在 Chrome/Edge 中登录微博 (https://m.weibo.cn)")
            print("  2. 已安装 browser_cookie3: pip install browser_cookie3")
            print("  3. 或设置环境变量 WEIBO_COOKIE_STRING")
            return 1

    # 仅测试 Cookie
    if args.list_cookies:
        print(f"提取到 {len(session.cookies)} 个 Cookie:")
        for k, v in session.cookies.items():
            print(f"  {k}: {v[:20]}..." if len(v) > 20 else f"  {k}: {v}")
        return 0

    # ---- 5. 初始化组件 ----
    # 数据库
    db_path = config.archive_root / "db" / "weibo_saver.db"
    db = Database(db_path)

    # 目录布局
    layout = Layout(config.archive_root)

    # 文件写入器
    file_writer = FileWriter(
        save_json=config.output.save_json,
        save_md=config.output.save_markdown,
        save_txt=config.output.save_txt,
    )

    # 速率限制器
    rate_limiter = AdaptiveRateLimiter(config.rate_limit)

    # 封锁检测器
    block_detector = BlockDetector()

    # 代理池
    proxy_pool: ProxyPool | None = None
    if config.proxy.enabled and config.proxy.proxies:
        strategy = {
            "round_robin": ProxyStrategy.ROUND_ROBIN,
            "random": ProxyStrategy.RANDOM,
            "weighted": ProxyStrategy.WEIGHTED,
            "best_first": ProxyStrategy.BEST_FIRST,
        }.get(config.proxy.strategy, ProxyStrategy.WEIGHTED)

        proxy_pool = ProxyPool(
            proxies=config.proxy.proxies,
            strategy=strategy,
            health_check_interval=config.proxy.health_check_interval,
            max_consecutive_fails=config.proxy.max_consecutive_fails,
            min_alive_proxies=config.proxy.min_alive_proxies,
        )

        if config.proxy.auto_fetch_free:
            logger.info("自动抓取免费代理...")
            added = await proxy_pool.fetch_from_sources(config.proxy.proxy_sources)
            logger.info(f"从免费源获取了 {added} 个代理")

        # 启动时进行健康检查
        await proxy_pool.health_check()
        logger.info(
            f"代理池已初始化 | total={proxy_pool.total_count} | "
            f"alive={proxy_pool.alive_count} | strategy={config.proxy.strategy}"
        )
    else:
        logger.info("代理池未启用（直连模式）")

    # API 抓取器（主模式）
    api_fetcher = ApiFetcher(
        session, rate_limiter, block_detector, config.retry, proxy_pool
    )

    # 浏览器抓取器（兜底模式）
    browser_fetcher = BrowserFetcher(config)

    # 去重管理
    dedup = Dedup(db)

    # 媒体下载器（先占位，engine 启动后由 httpx client 初始化）
    # media_dl 在 engine.start() 中通过 api_fetcher 的 client 创建
    # 先用一个临时占位
    media_dl = None  # 将在 engine 初始化前设置

    # 引擎
    engine = Engine(
        config, db, layout, file_writer, session,
        api_fetcher, browser_fetcher, media_dl,  # type: ignore[arg-type]
        dedup, block_detector, proxy_pool,
    )

    # 启动引擎（初始化 DB 和 API）
    await db.init()
    await api_fetcher.start()

    # 现在创建正确的 media_dl（需要 api_fetcher 的 client）
    media_dl = MediaDownloader(config.download, api_fetcher.client)
    engine._media_dl = media_dl

    # 状态机
    state = MonitorState()

    # 调度器
    scheduler = Scheduler(engine, state, config)

    # ---- 6. 设置托盘 ----
    tray: TrayApp | None = None

    if not args.once:
        # 回调闭包
        def on_start_monitor():
            asyncio.run_coroutine_threadsafe(
                scheduler.resume(), loop
            )

        def on_stop_monitor():
            asyncio.run_coroutine_threadsafe(
                scheduler.pause(), loop
            )

        def on_force_crawl():
            async def _force():
                for uid in config.target_uids:
                    await scheduler.force_full_crawl(uid)
            asyncio.run_coroutine_threadsafe(_force(), loop)

        def on_exit():
            scheduler._stop_event.set()
            asyncio.run_coroutine_threadsafe(_do_shutdown(), loop)

        tray = TrayApp(
            archive_root=config.archive_root,
            on_start_monitor=on_start_monitor,
            on_stop_monitor=on_stop_monitor,
            on_force_crawl=on_force_crawl,
            on_exit=on_exit,
        )

        # 连接状态回调
        engine.set_status_callback(tray.update_status)
        scheduler.set_status_callback(tray.update_status)

        # 启动托盘
        tray.start()
        tray.update_status("Weibo Saver - 启动中...")

    # ---- 7. 执行抓取 ----
    loop = asyncio.get_event_loop()
    shutdown_event = asyncio.Event()

    async def _do_shutdown():
        """优雅关闭."""
        logger.info("正在关闭...")
        if tray:
            tray.update_status("正在关闭...")
            tray.stop()
        await scheduler.stop()
        if proxy_pool:
            await proxy_pool.close()
        await engine.stop()
        shutdown_event.set()

    # 信号处理
    if sys.platform == "win32":
        signal.signal(signal.SIGINT, lambda s, f: asyncio.run_coroutine_threadsafe(
            _do_shutdown(), loop
        ))

    try:
        for uid in config.target_uids:
            logger.info(f"开始处理用户: {uid}")
            if tray:
                tray.update_status(f"处理用户: {uid}")

            # 全量抓取（如果需要）
            crawl_state = await db.get_crawl_state()
            if not crawl_state.get("is_first_crawl_complete"):
                state.to_full_crawl()
                stats = await engine.full_crawl(uid)
                logger.info(
                    f"全量抓取完成 | new={stats.new_posts} | "
                    f"updated={stats.updated_posts} | errors={len(stats.errors)}"
                )
                state.to_incremental()
            else:
                # 增量检查
                stats = await engine.incremental_crawl(uid)
                logger.info(
                    f"增量检查完成 | new={stats.new_posts} | "
                    f"updated={stats.updated_posts}"
                )

        if args.once:
            logger.info("单次抓取完成，退出")
            await engine.stop()
            return 0

        # ---- 8. 启动后台监控 ----
        if tray:
            tray.update_status("Weibo Saver - 监控中")
            tray.notify("Weibo Saver", "后台监控已启动")

        scheduler_task = asyncio.create_task(scheduler.run())

        # 等待关闭信号
        await shutdown_event.wait()

        # 取消调度任务
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass

    except KeyboardInterrupt:
        logger.info("收到中断信号")
    except Exception as e:
        logger.critical(f"致命错误: {e}", exc_info=True)
        if tray:
            tray.notify("Weibo Saver 错误", str(e))
        raise
    finally:
        await _do_shutdown()

    logger.info("Weibo Saver 已退出")
    return 0


def main() -> int:
    """同步入口（供 PyInstaller 调用）."""
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    return asyncio.run(async_main())


if __name__ == "__main__":
    sys.exit(main())
