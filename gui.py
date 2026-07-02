"""Weibo Saver GUI 启动器 — 支持 exe 自调用抓取模式."""
import os
import sys
from pathlib import Path

# 确定应用根目录
if getattr(sys, 'frozen', False):
    APP_ROOT = Path(sys.executable).parent
    MEIPASS = Path(sys._MEIPASS) if hasattr(sys, '_MEIPASS') else APP_ROOT
else:
    APP_ROOT = Path(__file__).parent
    MEIPASS = APP_ROOT

sys.path.insert(0, str(APP_ROOT))

# ---- 静默抓取模式（子进程自调用） ----
if "--crawl" in sys.argv:
    import asyncio, os
    output_file = os.environ.get("CRAWL_OUTPUT_FILE", "")
    if output_file:
        f = open(output_file, 'w', encoding='utf-8', errors='replace')
        sys.stdout = f
        sys.stderr = f
    crawl_script = MEIPASS / "run_full_crawl.py"
    if not crawl_script.exists():
        print(f"[FATAL] 找不到抓取脚本: {crawl_script}", flush=True)
        sys.exit(1)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    sys.path.insert(0, str(MEIPASS))
    import runpy
    runpy.run_path(str(crawl_script), run_name="__main__")
    sys.exit(0)

# ---- GUI 模式 ----
from weibo_saver.ui.gui_app import run_gui

ARCHIVE_ROOT = APP_ROOT / "WeiboArchive"
CRASH_LOG = APP_ROOT / "crash.log"
APP_LOG = APP_ROOT / "weibo_saver_all.log"

def _log_crash(msg: str) -> None:
    """将崩溃信息写入文件并弹窗提示."""
    import traceback
    try:
        CRASH_LOG.write_text(
            f"[{__import__('datetime').datetime.now().isoformat()}] {msg}\n{traceback.format_exc()}\n",
            encoding="utf-8"
        )
    except Exception:
        pass
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, f"{msg}\n\n详细信息已写入:\n{CRASH_LOG}",
                                           "Weibo Saver 启动失败", 0x10)
    except Exception:
        pass


# ---- Tcl/Tk 环境修复（PyInstaller 运行时钩子可能遗漏） ----
def _fix_tcl_env():
    """确保 Tcl/Tk 能找到 init.tcl 等运行时文件."""
    if not getattr(sys, 'frozen', False):
        return
    meipass = os.environ.get("_MEIPASS2", sys._MEIPASS if hasattr(sys, '_MEIPASS') else "")
    if not meipass:
        return
    base = Path(meipass)
    # 设置 TCL_LIBRARY（找 tcl 或 tcl8 目录）
    for subdir in ("tcl", "tcl8"):
        candidate = base / subdir
        if candidate.exists() and "TCL_LIBRARY" not in os.environ:
            os.environ["TCL_LIBRARY"] = str(candidate)
            break
    # 设置 TK_LIBRARY（找 tk 或 tk8 目录）
    for subdir in ("tk", "tk8"):
        candidate = base / subdir
        if candidate.exists() and "TK_LIBRARY" not in os.environ:
            os.environ["TK_LIBRARY"] = str(candidate)
            break


if __name__ == "__main__":
    _fix_tcl_env()
    try:
        sys.exit(run_gui(ARCHIVE_ROOT, APP_LOG))
    except SystemExit:
        raise
    except Exception:
        _log_crash("主程序启动失败")
        sys.exit(1)
