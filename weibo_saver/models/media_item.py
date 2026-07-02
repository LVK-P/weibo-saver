"""数据模型：媒体项."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class MediaItem:
    """图片或视频."""

    media_type: str  # "image" | "video" | "gif"
    weibo_pid: str   # 微博 pic_id 或 video_id
    original_url: str
    local_path: str = ""
    file_size: int = 0
    file_hash: str = ""
    width: int = 0
    height: int = 0
    duration: float = 0.0  # 仅视频
    download_status: str = "pending"  # pending | downloading | complete | failed
    retry_count: int = 0
