"""GUI 全局设置持久化 — 独立于后端 Config 的轻量 JSON 存储."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class GuiSettings:
    """GUI 层设置，读写 {archive_root}/config/gui_settings.json.

    与后端的 Config 数据类分离，仅存储 GUI 需要的全局选项.
    """

    default_archive_root: str = ""  # 空字符串 = exe 所在目录
    monitor_interval_seconds: int = 300  # 默认 5 分钟，范围 60-3600

    # ---- 工厂 ----

    @classmethod
    def load(cls, archive_root: str | Path) -> "GuiSettings":
        """从 JSON 文件加载，文件不存在则返回默认值."""
        path = Path(archive_root) / "config" / "gui_settings.json"
        if not path.exists():
            return cls()

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return cls()

        return cls(
            default_archive_root=data.get("default_archive_root", ""),
            monitor_interval_seconds=cls._clamp_interval(
                data.get("monitor_interval_seconds", 60)
            ),
        )

    # ---- 持久化 ----

    def save(self, archive_root: str | Path) -> None:
        """写入 JSON 文件."""
        config_dir = Path(archive_root) / "config"
        config_dir.mkdir(parents=True, exist_ok=True)

        path = config_dir / "gui_settings.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    def to_dict(self) -> dict:
        return {
            "default_archive_root": self.default_archive_root,
            "monitor_interval_seconds": self.monitor_interval_seconds,
        }

    # ---- 验证 ----

    @staticmethod
    def _clamp_interval(value: int) -> int:
        """限制监测间隔在 60-3600 秒之间."""
        return max(60, min(3600, value))
