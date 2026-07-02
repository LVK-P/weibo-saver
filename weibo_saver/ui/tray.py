"""系统托盘应用：pystray 图标 + 右键菜单 + 状态通知."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Any

try:
    import pystray
    HAS_PYSTRAY = True
except ImportError:
    HAS_PYSTRAY = False

from ..utils.text_sanitizer import truncate_for_display

logger = logging.getLogger("weibo_saver.ui.tray")


class TrayApp:
    """系统托盘应用.

    在 Windows 任务栏显示图标，提供右键菜单控制程序。
    运行在独立线程中，不阻塞主 asyncio 循环。
    """

    def __init__(
        self,
        archive_root: Path,
        on_start_monitor: Any = None,
        on_stop_monitor: Any = None,
        on_force_crawl: Any = None,
        on_exit: Any = None,
    ):
        """
        Args:
            archive_root: 存档根目录（"打开目录" 功能用）
            on_start_monitor: 启动监控回调
            on_stop_monitor: 停止监控回调
            on_force_crawl: 强制执行全量抓取回调
            on_exit: 退出回调
        """
        self._archive_root = archive_root
        self._on_start_monitor = on_start_monitor
        self._on_stop_monitor = on_stop_monitor
        self._on_force_crawl = on_force_crawl
        self._on_exit = on_exit

        self._icon: pystray.Icon | None = None
        self._running = False
        self._monitoring = True
        self._status_text = "Weibo Saver - 就绪"
        self._thread: threading.Thread | None = None

    # ---- 生命周期 ----

    def start(self) -> None:
        """在后台线程启动托盘."""
        if not HAS_PYSTRAY:
            logger.warning("pystray 未安装，托盘功能不可用")
            return

        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._run_tray, daemon=True)
        self._thread.start()
        logger.info("托盘已启动")

    def stop(self) -> None:
        """停止托盘."""
        self._running = False
        if self._icon:
            self._icon.stop()
            self._icon = None
        logger.info("托盘已停止")

    # ---- 状态更新 ----

    def update_status(self, text: str) -> None:
        """更新托盘提示文本."""
        self._status_text = text[:128]  # 限制长度
        # pystray 的 update_menu 可以在任意线程调用
        if self._icon:
            try:
                self._icon.title = self._status_text
            except Exception:
                pass

    def notify(self, title: str, message: str) -> None:
        """弹出 Windows 通知."""
        if self._icon and HAS_PYSTRAY:
            try:
                self._icon.notify(message, title)
            except Exception as e:
                logger.debug(f"通知失败: {e}")

    # ---- 内部 ----

    def _run_tray(self) -> None:
        """托盘主循环（运行在独立线程）."""
        if not HAS_PYSTRAY:
            return

        from .icon import generate_tray_icon

        # 生成图标
        icon_image = generate_tray_icon(64)
        if icon_image is None:
            logger.warning("无法生成托盘图标")
            return

        # 构建菜单
        menu = self._build_menu()

        # 创建并运行
        self._icon = pystray.Icon(
            "weibo_saver",
            icon_image,
            title=self._status_text,
            menu=menu,
        )

        try:
            self._icon.run()
        except Exception as e:
            logger.error(f"托盘运行异常: {e}")
        finally:
            self._icon = None

    def _build_menu(self) -> "pystray.Menu":
        """构建右键菜单."""
        if not HAS_PYSTRAY:
            return pystray.Menu()  # type: ignore

        # 状态项（不可点击）
        status_item = pystray.MenuItem(
            self._status_text,
            lambda: None,
            enabled=False,
        )

        # 分隔线
        sep = pystray.Menu.SEPARATOR

        # 控制按钮
        def _toggle_monitor(icon, item):
            self._monitoring = not self._monitoring
            if self._monitoring:
                if self._on_start_monitor:
                    self._on_start_monitor()
            else:
                if self._on_stop_monitor:
                    self._on_stop_monitor()

        toggle_item = pystray.MenuItem(
            "暂停监控",
            _toggle_monitor,
            checked=lambda item: self._monitoring,
        )

        def _force_crawl(icon, item):
            if self._on_force_crawl:
                self._on_force_crawl()

        force_item = pystray.MenuItem(
            "立即全量抓取",
            _force_crawl,
        )

        # 打开存档目录
        def _open_folder(icon, item):
            try:
                os.startfile(str(self._archive_root))
            except Exception:
                subprocess.Popen(['explorer', str(self._archive_root)])

        open_item = pystray.MenuItem(
            "打开存档目录",
            _open_folder,
        )

        # 退出
        def _exit(icon, item):
            self._running = False
            if self._on_exit:
                self._on_exit()
            if self._icon:
                self._icon.stop()

        exit_item = pystray.MenuItem(
            "退出",
            _exit,
        )

        return pystray.Menu(
            status_item,
            sep,
            toggle_item,
            force_item,
            open_item,
            sep,
            exit_item,
        )
