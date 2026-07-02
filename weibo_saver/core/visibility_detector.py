"""可见性检测器：识别微博用户的可见时间限制.

微博允许用户设置"仅展示最近半年/一年"的微博。
此模块负责：
1. 全量抓取后检测可见时间限制
2. 增量监控时检测限制变更
3. 标记因限制变更而不再可见的历史博文
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from ..storage.database import Database

logger = logging.getLogger("weibo_saver.core.visibility_detector")

# 检测阈值（允许 ±15 天的误差）
SIX_MONTHS_DAYS = 180
ONE_YEAR_DAYS = 365
DETECTION_TOLERANCE_DAYS = 15


class VisibilityLimit:
    """可见性限制类型."""

    NONE = "none"           # 无限制（全部可见）
    SIX_MONTHS = "six_months"   # 仅半年
    ONE_YEAR = "one_year"       # 仅一年
    CUSTOM = "custom"           # 自定义天数（非标准间隔）
    UNKNOWN = "unknown"         # 无法确定


class VisibilityDetector:
    """微博可见时间限制检测器.

    每个用户独立检测，支持:
    - 全量抓取后自动检测限制类型
    - 增量监控时检测限制变更
    - 标记被隐藏的历史博文
    """

    def __init__(self, db: Database):
        self._db = db

    # ---- 检测入口 ----

    async def detect_after_full_crawl(
        self, uid: str, total_crawled: int, profile_statuses_count: str
    ) -> dict[str, Any]:
        """全量抓取完成后检测可见性限制.

        Args:
            uid: 用户 UID
            total_crawled: 实际抓取到的博文数
            profile_statuses_count: 用户资料中显示的微博总数（含转发）

        Returns:
            检测结果
        """
        result = {
            "limit": VisibilityLimit.UNKNOWN,
            "earliest_post_date": "",
            "days_visible": 0,
            "total_visible": total_crawled,
            "hidden_in_db": 0,
            "changed": False,
        }

        # 获取最早可见博文
        earliest = await self._db.get_earliest_visible_post(uid)
        if not earliest:
            logger.info(f"可见性检测 | uid={uid} | 无博文数据，跳过")
            result["limit"] = VisibilityLimit.NONE
            return result

        earliest_date_str = earliest.get("created_at", "")
        earliest_date = self._parse_weibo_date(earliest_date_str)
        if not earliest_date:
            logger.warning(f"可见性检测 | uid={uid} | 无法解析日期: {earliest_date_str}")
            return result

        result["earliest_post_date"] = earliest_date_str

        # 计算可见天数
        now = datetime.now(timezone.utc)
        days_visible = (now - earliest_date).days
        result["days_visible"] = days_visible

        # 检测限制类型
        detected_limit = self._classify_limit(days_visible, total_crawled)

        # 获取之前的限制
        prev = await self._db.get_user_visibility(uid)
        previous_limit = prev.get("visibility_limit") if prev else None

        # 检测变更
        if previous_limit and previous_limit != detected_limit:
            result["changed"] = True
            logger.warning(
                f"可见性限制变更 | uid={uid} | "
                f"{previous_limit} -> {detected_limit} | "
                f"可见天数={days_visible}"
            )

        # 更新数据库
        await self._db.update_user_visibility(
            uid, detected_limit, earliest_date_str
        )

        # 更新 crawl_state
        await self._db.update_crawl_state(
            visibility_limit=detected_limit,
            earliest_post_date=earliest_date_str,
        )

        # 记录变更日志
        await self._db.log_visibility_change(
            uid=uid,
            previous_limit=previous_limit or VisibilityLimit.UNKNOWN,
            new_limit=detected_limit,
            earliest_post=earliest_date_str,
            total_visible=total_crawled,
            hidden_count=0,
            notes=f"全量抓取后检测 | 可见{days_visible}天 | 抓取{total_crawled}条",
        )

        result["limit"] = detected_limit

        logger.info(
            f"可见性检测完成 | uid={uid} | limit={detected_limit} | "
            f"earliest={earliest_date_str} | days={days_visible}"
        )
        return result

    async def detect_during_monitoring(self, uid: str) -> dict[str, Any]:
        """增量监控时检测可见性是否变更.

        Args:
            uid: 用户 UID

        Returns:
            检测结果
        """
        result = {
            "limit": VisibilityLimit.UNKNOWN,
            "changed": False,
            "hidden_count": 0,
            "new_earliest": "",
        }

        prev = await self._db.get_user_visibility(uid)
        previous_limit = prev.get("visibility_limit") if prev else None

        # 获取当前最早可见博文
        earliest = await self._db.get_earliest_visible_post(uid)
        if not earliest:
            return result

        earliest_date_str = earliest.get("created_at", "")
        earliest_date = self._parse_weibo_date(earliest_date_str)
        if not earliest_date:
            return result

        now = datetime.now(timezone.utc)
        days_visible = (now - earliest_date).days
        current_limit = self._classify_limit(days_visible, 0)

        result["limit"] = current_limit
        result["new_earliest"] = earliest_date_str

        # 检查是否有之前可见的博文现在消失了
        # 如果之前的限制是 'none' 而现在有限制，说明用户刚开启了限制
        if previous_limit == VisibilityLimit.NONE and current_limit != VisibilityLimit.NONE:
            result["changed"] = True
            hidden = await self._mark_hidden_posts(uid, current_limit, earliest_date_str)
            result["hidden_count"] = hidden
            logger.warning(
                f"用户开启了可见性限制 | uid={uid} | "
                f"{previous_limit} -> {current_limit} | 隐藏了{hidden}条"
            )

        # 限制解除：restricted → none（恢复被隐藏的博文）
        elif (previous_limit in (VisibilityLimit.SIX_MONTHS, VisibilityLimit.ONE_YEAR)
              and current_limit == VisibilityLimit.NONE):
            result["changed"] = True
            restored = await self._unhide_posts(uid)
            result["hidden_count"] = 0  # 已恢复
            logger.warning(
                f"可见性限制已解除 | uid={uid} | "
                f"{previous_limit} -> {current_limit} | 恢复了{restored}条博文"
            )

        # 如果限制变得更严格（如半年→不可见，或一年→半年）
        elif previous_limit and previous_limit != VisibilityLimit.UNKNOWN:
            if current_limit != previous_limit:
                result["changed"] = True
                # 检查是否因为限制变更而隐藏了博文
                hidden = await self._mark_hidden_posts(uid, current_limit, earliest_date_str)
                result["hidden_count"] = hidden
                logger.warning(
                    f"可见性限制变更 | uid={uid} | "
                    f"{previous_limit} -> {current_limit} | 隐藏了{hidden}条"
                )

        # 更新记录
        if result["changed"]:
            await self._db.update_user_visibility(
                uid, current_limit, earliest_date_str
            )
            await self._db.log_visibility_change(
                uid=uid,
                previous_limit=previous_limit or VisibilityLimit.UNKNOWN,
                new_limit=current_limit,
                earliest_post=earliest_date_str,
                hidden_count=result["hidden_count"],
                notes=f"监控中检测到变更 | 可见{days_visible}天",
            )

        return result

    # ---- 内部方法 ----

    def _classify_limit(self, days_visible: int, total_crawled: int) -> str:
        """根据可见天数和数量判断限制类型.

        Args:
            days_visible: 最早博文距今多少天
            total_crawled: 实际抓取到的数量

        Returns:
            限制类型
        """
        # 如果可见天数很短但博文数也少，可能是新用户
        if days_visible < 60:
            return VisibilityLimit.NONE

        # 检查是否接近半年
        if abs(days_visible - SIX_MONTHS_DAYS) <= DETECTION_TOLERANCE_DAYS:
            return VisibilityLimit.SIX_MONTHS

        # 检查是否接近一年
        if abs(days_visible - ONE_YEAR_DAYS) <= DETECTION_TOLERANCE_DAYS:
            return VisibilityLimit.ONE_YEAR

        # 如果明显少于半年且博文数多，可能也是半年限制（无法精确判定）
        if days_visible < SIX_MONTHS_DAYS - DETECTION_TOLERANCE_DAYS:
            return VisibilityLimit.SIX_MONTHS

        # 超过一年范围 → 可能是自定义天数限制
        # 如果可见天数 < 3年 且用户总发博量 > 实际可见数，大概率有限制
        if days_visible < 1095:  # 3年
            return VisibilityLimit.CUSTOM

        # 无明显限制
        return VisibilityLimit.NONE

    async def _unhide_posts(self, uid: str) -> int:
        """解除隐藏：当用户从有限制变为无限制时，恢复之前被标记隐藏的博文."""
        await self._db.conn.execute(
            "UPDATE posts SET visibility_hidden=0 WHERE uid=? AND visibility_hidden=1",
            (uid,))
        await self._db.conn.commit()
        count = self._db.conn.total_changes
        await self._db.update_crawl_state(
            hidden_posts_count=0,
        )
        logger.info(f"恢复隐藏博文 | uid={uid} | count={count}")
        return count

    async def _mark_hidden_posts(
        self, uid: str, new_limit: str, earliest_date_str: str
    ) -> int:
        """标记因可见限制变更而隐藏的博文.

        当限制从宽松变严格（如无限制→仅半年），
        之前保存的更早博文需要标记为已隐藏。
        """
        earliest_date = self._parse_weibo_date(earliest_date_str)
        if not earliest_date:
            return 0

        # 只标记有限制的情况（含自定义天数）
        if new_limit in (VisibilityLimit.SIX_MONTHS, VisibilityLimit.ONE_YEAR, VisibilityLimit.CUSTOM):
            # 格式化为标准时间字符串前缀用于比较
            date_prefix = earliest_date.strftime("%Y-%m-%d")
            count = await self._db.mark_posts_hidden_by_date(uid, date_prefix)
            if count > 0:
                await self._db.update_crawl_state(
                    hidden_posts_count=await self._db.get_hidden_posts_count(uid),
                )
            return count

        return 0

    @staticmethod
    def _parse_weibo_date(date_str: str) -> datetime | None:
        """解析微博时间格式."""
        if not date_str:
            return None

        formats = [
            "%a %b %d %H:%M:%S %z %Y",   # Mon Jan 01 12:00:00 +0800 2024
            "%Y-%m-%dT%H:%M:%S",           # ISO 8601
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ]
        for fmt in formats:
            try:
                dt = datetime.strptime(date_str, fmt)
                # 如果没有时区信息，假设为 UTC
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue

        # 尝试解析中文格式
        try:
            # "2025年06月15日" 等
            import re
            match = re.match(r'(\d{4})年(\d{1,2})月(\d{1,2})日', date_str)
            if match:
                return datetime(
                    int(match.group(1)),
                    int(match.group(2)),
                    int(match.group(3)),
                    tzinfo=timezone.utc,
                )
        except Exception:
            pass

        return None

    @staticmethod
    def format_limit(limit: str, days: int = 0) -> str:
        """格式化限制类型为人类可读文本."""
        if limit == VisibilityLimit.CUSTOM and days > 0:
            months = days // 30
            return f"仅展示最近约{months}个月 ({days}天)"
        mapping = {
            VisibilityLimit.NONE: "无限制（全部可见）",
            VisibilityLimit.SIX_MONTHS: "仅展示最近半年",
            VisibilityLimit.ONE_YEAR: "仅展示最近一年",
            VisibilityLimit.UNKNOWN: "无法确定",
        }
        return mapping.get(limit, limit)
