"""Weibo Saver - 顶层入口（用于 PyInstaller 打包）.

Usage:
    python main.py --uid 1234567890
    python main.py --once --uid 1234567890
"""

import sys

# 确保项目路径在 sys.path 中
from pathlib import Path

_project_root = Path(__file__).parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from weibo_saver.main import main

if __name__ == "__main__":
    sys.exit(main())
