"""编辑检测器：通过 HTML 页面解析 + API 哈希对比 双重检测博文编辑.

微博移动端页面会在已编辑博文的头部显示"已编辑"标记：
/html/body/.../h4/span[3] 文本 = "已编辑"

同时提供编辑频率追踪功能，对近期有多次编辑的博文增加监测频率。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("weibo_saver.core.edit_detector")


class EditDetector:
    """博文编辑检测器.

    双重检测策略:
    1. API 内容哈希对比（主检测，快速）
    2. HTML 页面 "已编辑" 标记（辅助确认，精确）
    """

    # 用于从 HTML 中提取 "已编辑" 标记的正则
    EDITED_SPAN_RE = re.compile(
        r'<span[^>]*class="[^"]*"[^>]*>已编辑</span>',
        re.IGNORECASE,
    )
    # 另一种可能的格式
    EDITED_TEXT_RE = re.compile(r'已编辑')

    def __init__(self, db=None):
        self._db = db
        # 编辑频率追踪: {uid: {post_id: [edit_timestamps]}}
        self._edit_tracker: dict[str, dict[str, list[datetime]]] = {}

    # ---- HTML 页面检测 ----

    @staticmethod
    def check_html_for_edit(html: str) -> bool:
        """从微博移动端 HTML 页面检测"已编辑"标记.

        Args:
            html: 博文详情页的 HTML 内容

        Returns:
            是否检测到 "已编辑" 标记
        """
        if not html:
            return False
        return bool(EditDetector.EDITED_TEXT_RE.search(html))

    async def fetch_and_check(self, client, post_id: str) -> bool:
        """获取博文详情页并检测是否已编辑.

        Args:
            client: httpx.AsyncClient
            post_id: 博文 ID

        Returns:
            是否已编辑
        """
        try:
            url = f"https://m.weibo.cn/detail/{post_id}"
            r = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15",
                "Accept": "text/html,application/xhtml+xml",
            })
            if r.status_code == 200:
                return self.check_html_for_edit(r.text)
        except Exception as e:
            logger.debug(f"HTML编辑检测失败 | post_id={post_id} | {e}")
        return False

    @staticmethod
    def count_edits_in_html(html: str) -> int:
        """从编辑历史页面统计编辑次数.

        编辑历史页面 URL: https://m.weibo.cn/detail/{post_id}/edit/history
        """
        if not html:
            return 0
        # 编辑历史页面中每个版本通常是一个卡片
        # 统计 version 相关标记
        version_matches = re.findall(r'编辑于|版本|edit', html, re.IGNORECASE)
        return len(version_matches) if version_matches else 0

    # ---- 编辑频率追踪 ----

    def record_edit(self, uid: str, post_id: str) -> None:
        """记录一次编辑事件."""
        if uid not in self._edit_tracker:
            self._edit_tracker[uid] = {}
        if post_id not in self._edit_tracker[uid]:
            self._edit_tracker[uid][post_id] = []

        self._edit_tracker[uid][post_id].append(datetime.now(timezone.utc))

    def get_edit_count_last_week(self, uid: str, post_id: str) -> int:
        """获取近一周内的编辑次数."""
        if uid not in self._edit_tracker:
            return 0
        timestamps = self._edit_tracker[uid].get(post_id, [])
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        return sum(1 for ts in timestamps if ts > cutoff)

    def should_boost_monitoring(self, uid: str, post_id: str) -> bool:
        """判断是否需要对该博文增加监测频率.

        条件: 近一周内编辑超过 1 次
        """
        return self.get_edit_count_last_week(uid, post_id) > 1

    def get_boosted_posts(self, uid: str) -> list[str]:
        """获取需要加强监测的博文列表."""
        if uid not in self._edit_tracker:
            return []
        boosted = []
        for post_id in self._edit_tracker[uid]:
            if self.should_boost_monitoring(uid, post_id):
                boosted.append(post_id)
        return boosted

    def get_edit_stats(self, uid: str) -> dict[str, Any]:
        """获取某用户的编辑统计."""
        if uid not in self._edit_tracker:
            return {"total_edits": 0, "posts_edited": 0, "boosted": []}

        posts = self._edit_tracker[uid]
        total = sum(len(ts) for ts in posts.values())
        return {
            "total_edits": total,
            "posts_edited": len(posts),
            "boosted": self.get_boosted_posts(uid),
            "posts": {
                pid: len(ts) for pid, ts in posts.items()
            },
        }

    def clear(self, uid: str | None = None) -> None:
        """清除编辑追踪数据."""
        if uid:
            self._edit_tracker.pop(uid, None)
        else:
            self._edit_tracker.clear()
