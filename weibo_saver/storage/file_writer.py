"""文件写入器：JSON / Markdown / TXT 格式输出."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..models.post import Post
from ..models.media_item import MediaItem
from ..utils.text_sanitizer import sanitize_filename

logger = logging.getLogger("weibo_saver.storage.file_writer")


class FileWriter:
    """多格式文件写入."""

    def __init__(self, *, save_json: bool = True, save_md: bool = True, save_txt: bool = True):
        self._save_json = save_json
        self._save_md = save_md
        self._save_txt = save_txt

    async def write_post(self, post: Post, post_dir: Path) -> dict[str, Path]:
        """写入博文的所有格式.

        Args:
            post: 博文数据
            post_dir: 目标目录

        Returns:
            格式 -> 文件路径 的映射
        """
        post_dir.mkdir(parents=True, exist_ok=True)
        results: dict[str, Path] = {}

        if self._save_json:
            results["json"] = await self._write_json(post, post_dir)
        if self._save_md:
            results["md"] = await self._write_markdown(post, post_dir)
        if self._save_txt:
            results["txt"] = await self._write_txt(post, post_dir)

        return results

    async def _write_json(self, post: Post, post_dir: Path) -> Path:
        """写入 JSON 格式."""
        path = post_dir / "post.json"
        content = json.dumps(post.to_dict(), ensure_ascii=False, indent=2)
        path.write_text(content, encoding="utf-8")
        logger.debug(f"JSON 已写入: {path}")
        return path

    async def _write_markdown(self, post: Post, post_dir: Path) -> Path:
        """写入 Markdown 格式."""
        path = post_dir / "post.md"
        content = post.to_markdown()
        path.write_text(content, encoding="utf-8")
        logger.debug(f"Markdown 已写入: {path}")
        return path

    async def _write_txt(self, post: Post, post_dir: Path) -> Path:
        """写入纯文本格式."""
        path = post_dir / "post.txt"
        content = post.to_txt()
        path.write_text(content, encoding="utf-8")
        logger.debug(f"TXT 已写入: {path}")
        return path

    async def write_raw_response(self, raw_card: dict, post_dir: Path) -> Path:
        """写入原始 API 响应（调试用）."""
        path = post_dir / "original.json"
        content = json.dumps(raw_card, ensure_ascii=False, indent=2)
        path.write_text(content, encoding="utf-8")
        return path

    async def write_user_profile(self, user_data: dict, user_dir: Path) -> Path:
        """写入用户信息."""
        user_dir.mkdir(parents=True, exist_ok=True)
        path = user_dir / "profile.json"
        content = json.dumps(user_data, ensure_ascii=False, indent=2)
        path.write_text(content, encoding="utf-8")
        return path

    async def write_version(
        self, post: Post, version_num: int, diff_text: str | None, versions_dir: Path
    ) -> Path:
        """写入版本快照.

        Args:
            post: 新版本博文
            version_num: 版本号
            diff_text: 与上一版本的差异
            versions_dir: 版本目录

        Returns:
            版本目录路径
        """
        v_dir = versions_dir / f"v{version_num}"
        v_dir.mkdir(parents=True, exist_ok=True)

        # 保存当前版本
        (v_dir / "post.json").write_text(
            json.dumps(post.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (v_dir / "post.md").write_text(post.to_markdown(), encoding="utf-8")
        (v_dir / "post.txt").write_text(post.to_txt(), encoding="utf-8")

        # 保存差异
        if diff_text:
            (v_dir / f"diff_v{version_num}.patch").write_text(
                diff_text, encoding="utf-8"
            )

        return v_dir

    async def save_media_bytes(self, data: bytes, path: Path) -> None:
        """保存媒体文件的字节数据."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        logger.debug(f"媒体已保存: {path} ({len(data)} bytes)")
