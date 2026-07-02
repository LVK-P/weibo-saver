"""目录布局管理器：构建博文在磁盘上的存储路径."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..utils.text_sanitizer import sanitize_filename


class Layout:
    """管理归档文件的目录结构.

    结构:
        <archive_root>/
        └── users/
            └── {screen_name}_{uid}/
                └── posts/
                    └── {YYYY}/
                        └── {MM}/
                            └── {bid}/
                                ├── post.json
                                ├── post.md
                                ├── post.txt
                                ├── images/
                                ├── videos/
                                └── versions/
    """

    def __init__(self, archive_root: str | Path):
        self._root = Path(archive_root)

    @property
    def root(self) -> Path:
        return self._root

    @property
    def db_dir(self) -> Path:
        return self._root / "db"

    @property
    def logs_dir(self) -> Path:
        return self._root / "logs"

    @property
    def users_dir(self) -> Path:
        return self._root / "users"

    def user_dir(self, screen_name: str, uid: str) -> Path:
        """用户目录."""
        name = sanitize_filename(f"{screen_name}_{uid}")
        return self.users_dir / name

    def posts_root(self, screen_name: str, uid: str) -> Path:
        """博文根目录."""
        return self.user_dir(screen_name, uid) / "posts"

    def post_dir(
        self,
        screen_name: str,
        uid: str,
        created_at: str,
        bid: str,
    ) -> Path:
        """单条博文的存储目录.

        Args:
            screen_name: 用户昵称
            uid: 用户 UID
            created_at: 博文发布时间 (ISO 8601 或 'Mon Jan 01 12:00:00 +0800 2024')
            bid: 博文 BID

        Returns:
            博文目录路径
        """
        dt = self._parse_created_at(created_at)
        year = str(dt.year) if dt else "unknown"
        month = f"{dt.month:02d}" if dt else "00"
        return self.posts_root(screen_name, uid) / year / month / bid

    def images_dir(self, post_dir: Path) -> Path:
        return post_dir / "images"

    def videos_dir(self, post_dir: Path) -> Path:
        return post_dir / "videos"

    def versions_dir(self, post_dir: Path) -> Path:
        return post_dir / "versions"

    # ---- 工具 ----

    @staticmethod
    def _parse_created_at(created_at: str) -> datetime | None:
        """尝试解析微博时间格式.

        微博 API 可能返回多种格式:
        - "Mon Jan 01 12:00:00 +0800 2024"
        - ISO 8601
        """
        if not created_at:
            return None

        from datetime import timezone

        # 尝试常见格式
        formats = [
            "%a %b %d %H:%M:%S %z %Y",      # Mon Jan 01 12:00:00 +0800 2024
            "%Y-%m-%dT%H:%M:%S",             # ISO 8601
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(created_at, fmt)
            except ValueError:
                continue

        # 如果带时区
        try:
            from datetime import timezone as tz

            return datetime.strptime(created_at, "%a %b %d %H:%M:%S %z %Y")
        except ValueError:
            pass

        return None

    def ensure_dirs(self, *paths: Path) -> None:
        """确保目录存在."""
        for p in paths:
            p.mkdir(parents=True, exist_ok=True)
