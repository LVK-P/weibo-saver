# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec — 打包 Weibo Saver 为单个免安装 exe."""

import sys
from pathlib import Path

_block_cipher = None

# 项目根目录
ROOT = Path(SPECPATH)  # noqa: F821

a = Analysis(
    ['gui.py'],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # 资源文件
        ('weibo_saver/resources/*', 'weibo_saver/resources'),
        # 子进程抓取脚本（GUI 通过 subprocess.run 调用）
        ('run_full_crawl.py', '.'),
        # 使用指南 PDF
        ('Weibo Saver使用指南.pdf', '.'),
    ],
    hiddenimports=[
        # 核心依赖
        'customtkinter',
        'httpx',
        'aiosqlite',
        'browser_cookie3',
        'browser_cookie3.chromium',
        'PIL',
        'PIL.Image',
        'PIL.ImageTk',
        # 标准库可能被遗漏的
        'asyncio',
        'json',
        'sqlite3',
        're',
        'hashlib',
        'difflib',
        'pathlib',
        'datetime',
        'logging',
        'queue',
        'threading',
        'subprocess',
        # weibo_saver 内部模块
        'weibo_saver',
        'weibo_saver.config',
        'weibo_saver.constants',
        'weibo_saver.exceptions',
        'weibo_saver.models',
        'weibo_saver.models.post',
        'weibo_saver.models.user',
        'weibo_saver.models.media_item',
        'weibo_saver.models.monitor_run',
        'weibo_saver.storage',
        'weibo_saver.storage.database',
        'weibo_saver.storage.layout',
        'weibo_saver.storage.file_writer',
        'weibo_saver.core',
        'weibo_saver.core.engine',
        'weibo_saver.core.api_fetcher',
        'weibo_saver.core.browser_fetcher',
        'weibo_saver.core.session_manager',
        'weibo_saver.core.block_detector',
        'weibo_saver.core.dedup',
        'weibo_saver.core.media_downloader',
        'weibo_saver.core.card_renderer',
        'weibo_saver.core.proxy_pool',
        'weibo_saver.core.visibility_detector',
        'weibo_saver.core.edit_detector',
        'weibo_saver.core.qr_login',
        'weibo_saver.versioning',
        'weibo_saver.versioning.differ',
        'weibo_saver.versioning.tracker',
        'weibo_saver.monitor',
        'weibo_saver.monitor.state',
        'weibo_saver.monitor.scheduler',
        'weibo_saver.ui',
        'weibo_saver.ui.gui_app',
        'weibo_saver.ui.gui_config',
        'weibo_saver.ui.tray',
        'weibo_saver.ui.icon',
        'weibo_saver.utils',
        'weibo_saver.utils.text_sanitizer',
        'weibo_saver.utils.retry',
        'weibo_saver.utils.rate_limiter',
        'weibo_saver.utils.logging_setup',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter.test',
        'unittest',
        'http.server',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=_block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=_block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='WeiboSaver',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,        # 无控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='weibo_saver/resources/logo.ico',
)
