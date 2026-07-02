"""数据模型：用户."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


def _parse_weibo_number(value: str | int) -> int:
    """解析微博的数字格式（支持 72.8万、1.2亿 等中文格式）.

    Args:
        value: 数字或格式化字符串

    Returns:
        整数
    """
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)

    value = str(value).strip()
    if not value:
        return 0

    # 纯数字
    try:
        return int(value)
    except ValueError:
        pass

    # 中文格式: 72.8万, 1.2亿
    match = re.match(r'([\d.]+)\s*万', value)
    if match:
        return int(float(match.group(1)) * 10000)

    match = re.match(r'([\d.]+)\s*亿', value)
    if match:
        return int(float(match.group(1)) * 100000000)

    # 尝试直接转 float
    try:
        return int(float(value))
    except ValueError:
        return 0


@dataclass(slots=True)
class User:
    """微博用户信息."""

    uid: str
    screen_name: str
    description: str = ""
    profile_url: str = ""
    avatar_url: str = ""
    followers_count: int = 0
    friends_count: int = 0
    statuses_count: int = 0
    raw_json: str = ""

    @classmethod
    def from_api_response(cls, data: dict) -> "User":
        """从 m.weibo.cn API 响应构建."""
        user_info = data.get("userInfo", data)
        return cls(
            uid=str(user_info.get("id", "")),
            screen_name=user_info.get("screen_name", ""),
            description=user_info.get("description", ""),
            profile_url=f"https://m.weibo.cn/u/{user_info.get('id', '')}",
            avatar_url=user_info.get("avatar_hd", user_info.get("avatar_large", "")),
            followers_count=_parse_weibo_number(user_info.get("followers_count", 0)),
            friends_count=_parse_weibo_number(user_info.get("friends_count", 0)),
            statuses_count=_parse_weibo_number(user_info.get("statuses_count", 0)),
        )
