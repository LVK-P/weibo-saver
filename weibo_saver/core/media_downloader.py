"""媒体下载器：异步下载图片和视频（原图质量）."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import struct
from pathlib import Path

import httpx

from ..config import DownloadConfig
from ..constants import MOBILE_HEADERS, SINAIMG_CDN_HOSTS
from ..exceptions import MediaDownloadError
from ..models.media_item import MediaItem
from ..models.post import Post
from ..utils.retry import async_retry

logger = logging.getLogger("weibo_saver.core.media_downloader")


class MediaDownloader:
    """异步媒体下载器.

    特性:
    - 并发控制（Semaphore 限制同时下载数）
    - 自动重试
    - 原图优先（original -> large -> mw690 降级）
    - 基于 magic bytes 的格式检测
    - 文件哈希去重
    """

    # 常见图片格式的 magic bytes
    MAGIC_BYTES: dict[bytes, str] = {
        b"\xff\xd8\xff": "jpg",
        b"\x89PNG\r\n\x1a\n": "png",
        b"GIF87a": "gif",
        b"GIF89a": "gif",
        b"RIFF": "webp",  # 需进一步检查 WEBP 标记
        b"\x00\x00\x01\x00": "ico",
    }

    def __init__(self, download_config: DownloadConfig, client: httpx.AsyncClient):
        self._config = download_config
        self._client = client
        self._semaphore = asyncio.Semaphore(download_config.max_concurrent_downloads)

    # ---- 图片下载 ----

    async def download_image(self, pic: MediaItem, dest_dir: Path) -> Path | None:
        """下载单张图片.

        Args:
            pic: 图片信息
            dest_dir: 目标目录

        Returns:
            本地文件路径，失败返回 None
        """
        dest_dir.mkdir(parents=True, exist_ok=True)

        # 构建 URL 优先级: original > large > mw690
        urls = self._build_image_urls(pic.weibo_pid)

        async with self._semaphore:
            for attempt, url in enumerate(urls):
                try:
                    response = await self._client.get(url, headers=MOBILE_HEADERS)
                    if response.status_code == 200 and len(response.content) > 100:
                        data = response.content
                        ext = self._detect_format(data) or "jpg"
                        filename = f"{pic.weibo_pid}_original.{ext}"
                        path = dest_dir / filename
                        path.write_bytes(data)

                        # 更新 pic 信息
                        pic.local_path = str(path)
                        pic.file_size = len(data)
                        pic.file_hash = hashlib.sha256(data).hexdigest()
                        pic.download_status = "complete"

                        logger.debug(
                            f"图片下载完成 | pid={pic.weibo_pid} | "
                            f"size={len(data)} | path={path}"
                        )
                        return path

                    elif response.status_code == 404:
                        continue  # 尝试下一个 URL
                    else:
                        logger.warning(
                            f"图片下载 HTTP {response.status_code} | "
                            f"pid={pic.weibo_pid} | url={url[:60]}..."
                        )

                except Exception as e:
                    logger.warning(f"图片下载异常 | pid={pic.weibo_pid} | {e}")
                    continue

            # 所有 URL 都失败
            pic.download_status = "failed"
            pic.retry_count += 1
            logger.error(f"图片下载全部失败 | pid={pic.weibo_pid}")
            return None

    async def download_all_images(self, post: Post, post_dir: Path) -> list[Path]:
        """并发下载博文的所有图片."""
        images_dir = post_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        if not post.pics:
            return []

        tasks = [self.download_image(pic, images_dir) for pic in post.pics]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        paths: list[Path] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"图片 [{i}] 下载异常: {result}")
            elif result is not None:
                paths.append(result)

        return paths

    # ---- 视频下载 ----

    async def download_video(self, video: MediaItem, dest_dir: Path) -> Path | None:
        """下载视频.

        Args:
            video: 视频信息
            dest_dir: 目标目录

        Returns:
            本地文件路径，失败返回 None
        """
        if not video.original_url:
            return None

        dest_dir.mkdir(parents=True, exist_ok=True)

        # 尝试多个视频 URL
        urls = [video.original_url]
        # 如果有 HD URL，优先尝试
        hd_url = video.original_url.replace("mp4_sd", "mp4_hd").replace("sd", "hd")
        if hd_url != video.original_url:
            urls.insert(0, hd_url)

        async with self._semaphore:
            for url in urls:
                try:
                    # 视频可能较大，使用流式下载 + 长超时（5分钟）
                    async with self._client.stream("GET", url, headers=MOBILE_HEADERS,
                                                    timeout=300.0) as response:
                        if response.status_code == 200:
                            total = int(response.headers.get("content-length", 0))
                            # 检查文件大小限制（0=不限制）
                            max_bytes = self._config.max_video_size_mb * 1024 * 1024
                            if max_bytes > 0 and total > max_bytes:
                                logger.warning(
                                    f"视频过大 ({total / 1024 / 1024:.1f}MB > "
                                    f"{self._config.max_video_size_mb}MB)，跳过"
                                )
                                continue

                            ext = self._detect_video_ext(url, response.headers.get("content-type", ""))
                            filename = f"{video.weibo_pid}.{ext}"
                            path = dest_dir / filename

                            data = bytearray()
                            async for chunk in response.aiter_bytes(chunk_size=8192):
                                data.extend(chunk)

                            path.write_bytes(data)

                            # 更新 video 信息
                            video.local_path = str(path)
                            video.file_size = len(data)
                            video.file_hash = hashlib.sha256(data).hexdigest()
                            video.download_status = "complete"

                            logger.debug(
                                f"视频下载完成 | vid={video.weibo_pid} | "
                                f"size={len(data)} | path={path}"
                            )
                            return path

                except Exception as e:
                    logger.warning(f"视频下载异常 | vid={video.weibo_pid} | {e}")
                    continue

            video.download_status = "failed"
            video.retry_count += 1
            logger.error(f"视频下载全部失败 | vid={video.weibo_pid}")
            return None

    async def download_post_video(self, post: Post, post_dir: Path) -> Path | None:
        """下载博文的视频."""
        if not post.video:
            return None

        videos_dir = post_dir / "videos"
        return await self.download_video(post.video, videos_dir)

    # ---- 批量下载 ----

    async def download_all(self, post: Post, post_dir: Path) -> int:
        """下载博文的所有媒体.

        Returns:
            成功下载的媒体数量
        """
        count = 0

        if self._config.images and post.pics:
            image_paths = await self.download_all_images(post, post_dir)
            count += len(image_paths)

        if self._config.videos and post.video:
            video_path = await self.download_post_video(post, post_dir)
            if video_path:
                count += 1

        return count

    # ---- 工具方法 ----

    def _build_image_urls(self, pid: str) -> list[str]:
        """构建图片 URL（original > large > mw690 优先级）.

        Weibo 图片 URL 格式: https://wx{n}.sinaimg.cn/{size}/{pid}.{ext}
        """
        urls: list[str] = []
        sizes = ["original", "large", "mw690"]
        extensions = ["jpg", "png", "gif"]

        for host in SINAIMG_CDN_HOSTS:
            for size in sizes:
                for ext in extensions:
                    urls.append(f"https://{host}/{size}/{pid}.{ext}")

        return urls

    @classmethod
    def _detect_format(cls, data: bytes) -> str | None:
        """通过 magic bytes 检测图片格式."""
        for magic, fmt in cls.MAGIC_BYTES.items():
            if data.startswith(magic):
                if fmt == "webp" and data[8:12] == b"WEBP":
                    return "webp"
                return fmt
        return None

    @staticmethod
    def _detect_video_ext(url: str, content_type: str) -> str:
        """检测视频文件扩展名."""
        if "mp4" in content_type:
            return "mp4"
        if "webm" in content_type:
            return "webm"
        if "quicktime" in content_type:
            return "mov"
        if "x-flv" in content_type:
            return "flv"
        # 从 URL 推断
        url_lower = url.lower()
        if ".mp4" in url_lower:
            return "mp4"
        if ".webm" in url_lower:
            return "webm"
        if ".flv" in url_lower:
            return "flv"
        return "mp4"  # 默认
