"""数据模型：监控运行记录."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CrawlStats:
    """单次爬取统计."""

    pages_crawled: int = 0
    posts_seen: int = 0
    new_posts: int = 0
    updated_posts: int = 0
    new_media: int = 0
    errors: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    @property
    def total_saved(self) -> int:
        return self.new_posts + self.updated_posts

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0
