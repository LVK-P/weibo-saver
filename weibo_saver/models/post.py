"""数据模型：博文."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from .media_item import MediaItem


@dataclass(slots=True)
class Post:
    """标准化博文数据."""

    post_id: str
    bid: str
    uid: str
    screen_name: str
    text_content: str
    text_html: str = ""
    created_at: str = ""  # ISO 8601
    source: str = ""  # 发布设备
    reposts_count: int = 0
    comments_count: int = 0
    attitudes_count: int = 0
    is_pinned: bool = False
    is_repost: bool = False
    retweeted_post: "Post | None" = None  # 转发的原博
    retweeted_text: str = ""            # 转发者附加的文字
    page_url: str = ""
    region_name: str = ""  # 发布位置
    pics: list[MediaItem] = field(default_factory=list)
    video: MediaItem | None = None
    page_info: dict | None = None  # 链接卡片等
    raw_card: dict | None = None
    content_hash: str = ""
    version_count: int = 1
    edited_detected: bool = False  # 来自 HTML 页面的 "已编辑" 标记
    edit_count: int = 0            # API 返回的编辑次数
    is_long_text: bool = False     # 是否需要展开完整内容
    avatar_url: str = ""           # 作者头像 URL（转发原博时用于卡片快照）

    # ---- 工厂方法 ----

    @classmethod
    def from_api_card(cls, card: dict) -> "Post | None":
        """从 m.weibo.cn API card 构建 Post.

        Args:
            card: API 返回的单条微博卡片数据

        Returns:
            Post 对象，系统卡片返回 None，转发博文 is_repost=True
        """
        mblog = card.get("mblog", card)

        # 系统卡片/非博文卡片 → 跳过
        # card_type: 9=原创, 10=视频, 1=转发; 11=系统消息/广告等需过滤
        card_type = card.get("card_type", 0)
        if card_type not in (9, 1, 10):
            # 缺少博文 ID 的铁定不是博文
            if not mblog.get("id"):
                return None
            # 有 ID 但缺少文本和媒体内容的也跳过（如空卡片/异常结构）
            if not mblog.get("text") and not mblog.get("pics") and not mblog.get("page_info"):
                return None

        # 提取基本信息
        post_id = str(mblog.get("id", ""))
        bid = str(mblog.get("bid", ""))
        user = mblog.get("user", {})
        uid = str(user.get("id", ""))
        screen_name = user.get("screen_name", "")

        # 文本内容
        text_html = mblog.get("text", "")
        text_content = cls._clean_html(text_html)

        # 时间
        created_at = mblog.get("created_at", "")

        # 来源
        source = cls._clean_html(mblog.get("source", ""))

        # 图片（mblog + card 级别都检查，转发原博可能在 card 级别）
        pics: list[MediaItem] = []
        pic_infos = mblog.get("pic_infos") or mblog.get("pics") or card.get("pic_infos") or card.get("pics", [])
        if isinstance(pic_infos, dict):
            for pid, info in pic_infos.items():
                url = ""
                if isinstance(info, dict):
                    original = info.get("original", info.get("large", {}))
                    url = original.get("url", info.get("url", ""))
                pics.append(
                    MediaItem(
                        media_type="image",
                        weibo_pid=pid,
                        original_url=url,
                        width=info.get("original", {}).get("width", 0) if isinstance(info, dict) else 0,
                        height=info.get("original", {}).get("height", 0) if isinstance(info, dict) else 0,
                    )
                )
        elif isinstance(pic_infos, list):
            for pic in pic_infos:
                pid = pic.get("pid", "")
                url = pic.get("large", {}).get("url", pic.get("url", ""))
                pics.append(
                    MediaItem(
                        media_type="image",
                        weibo_pid=pid,
                        original_url=url,
                    )
                )

        # 视频（优先用 mblog 的 page_info，fallback 到外层 card）
        video: MediaItem | None = None
        page_info = mblog.get("page_info") or card.get("page_info", {})
        if page_info and page_info.get("type") == "video":
            media_info = page_info.get("media_info", page_info.get("urls", {}))
            stream_url = media_info.get("stream_url", media_info.get("mp4_720p_mp4", ""))
            if not stream_url:
                stream_url = media_info.get("mp4_hd_url", media_info.get("mp4_sd_url", ""))
            if stream_url:
                video = MediaItem(
                    media_type="video",
                    weibo_pid=f"v_{post_id}",
                    original_url=stream_url,
                    duration=float(page_info.get("duration", 0)),
                )

        # 处理转发
        is_repost = False
        retweeted_text = ""
        retweeted_post = None

        retweeted = mblog.get("retweeted_status")
        if retweeted:
            is_repost = True
            retweeted_text = text_content
            # 使用原博文本（如果转发者只写了"转发微博"）
            if not text_content or text_content == "转发微博":
                text_content = cls._clean_html(retweeted.get("text", ""))
                text_html = retweeted.get("text", "")
            retweeted_post = cls._parse_retweeted(retweeted, card)

        # 构建 Post
        post = cls(
            post_id=post_id,
            bid=bid,
            uid=uid,
            screen_name=screen_name,
            text_content=text_content,
            text_html=text_html,
            created_at=created_at,
            source=source,
            reposts_count=int(mblog.get("reposts_count", 0)),
            comments_count=int(mblog.get("comments_count", 0)),
            attitudes_count=int(mblog.get("attitudes_count", 0)),
            is_pinned=bool(mblog.get("isTop", 0)),
            is_repost=is_repost,
            retweeted_post=retweeted_post,
            retweeted_text=retweeted_text,
            edit_count=int(mblog.get("edit_count", 0)),
            # 双重检测：API 标志 + 文本截断标记（`...全文` 或 `…全文`）
            is_long_text=bool(mblog.get("isLongText", False)) or (
                text_content and (
                    text_content.endswith("...全文") or
                    text_content.endswith("…全文") or
                    text_content.rstrip().endswith("全文")
                )
            ),
            edited_detected=bool(mblog.get("edit_at")),
            page_url=f"https://m.weibo.cn/detail/{post_id}",
            region_name=mblog.get("region_name", ""),
            pics=pics,
            video=video,
            page_info=page_info if page_info else None,
            raw_card=card,
        )

        # 计算内容哈希
        post.content_hash = post._compute_hash()
        return post

    @classmethod
    def _parse_retweeted(cls, retweeted: dict, parent_card: dict) -> "Post | None":
        """解析转发的原博为 Post 对象."""
        try:
            ru = retweeted.get("user", {})
            r_pics: list[MediaItem] = []
            r_pic_infos = retweeted.get("pic_infos", retweeted.get("pics", []))
            if isinstance(r_pic_infos, dict):
                for pid, info in r_pic_infos.items():
                    url = ""
                    if isinstance(info, dict):
                        url = info.get("original", info.get("large", {})).get("url", "")
                    r_pics.append(MediaItem(media_type="image", weibo_pid=pid, original_url=url))
            elif isinstance(r_pic_infos, list):
                for pic in r_pic_infos:
                    r_pics.append(MediaItem(
                        media_type="image",
                        weibo_pid=pic.get("pid", ""),
                        original_url=pic.get("large", {}).get("url", pic.get("url", "")),
                    ))

            # 视频（原博的 page_info）
            r_video: MediaItem | None = None
            r_page_info = retweeted.get("page_info", {})
            if r_page_info and r_page_info.get("type") == "video":
                media_info = r_page_info.get("media_info", r_page_info.get("urls", {}))
                stream_url = media_info.get("stream_url", media_info.get("mp4_720p_mp4", ""))
                if not stream_url:
                    stream_url = media_info.get("mp4_hd_url", media_info.get("mp4_sd_url", ""))
                if stream_url:
                    r_video = MediaItem(
                        media_type="video",
                        weibo_pid=f"v_{retweeted.get('id', '')}",
                        original_url=stream_url,
                        duration=float(r_page_info.get("duration", 0)),
                    )

            rp = cls(
                post_id=str(retweeted.get("id", "")),
                bid=str(retweeted.get("bid", "")),
                uid=str(ru.get("id", "")),
                screen_name=ru.get("screen_name", ""),
                text_content=cls._clean_html(retweeted.get("text", "")),
                text_html=retweeted.get("text", ""),
                created_at=retweeted.get("created_at", ""),
                source=cls._clean_html(retweeted.get("source", "")),
                reposts_count=int(retweeted.get("reposts_count", 0)),
                comments_count=int(retweeted.get("comments_count", 0)),
                attitudes_count=int(retweeted.get("attitudes_count", 0)),
                is_repost=False,
                page_url=f"https://m.weibo.cn/detail/{retweeted.get('id', '')}",
                pics=r_pics,
                video=r_video,
                page_info=r_page_info if r_page_info else None,
                raw_card=retweeted,
                avatar_url=ru.get("avatar_hd", ru.get("profile_image_url", "")),
            )
            rp.content_hash = rp._compute_hash()
            return rp
        except Exception:
            return None

    @property
    def post_type_label(self) -> str:
        """返回博文类型标签."""
        if self.is_pinned:
            return "置顶"
        if self.is_repost:
            return "转发"
        return "原创"

    @property
    def dir_name(self) -> str:
        """返回用于文件系统的目录名: {日期_时间}_{类型}（精确到分钟）."""
        date_prefix = "0000-00-00_00-00"
        if self.created_at:
            from ..storage.layout import Layout
            dt = Layout._parse_created_at(self.created_at)
            if dt:
                date_prefix = dt.strftime("%Y-%m-%d_%H-%M")
        return f"{date_prefix}_{self.post_type_label}"

    # ---- 方法 ----

    def _compute_hash(self) -> str:
        """计算内容 SHA-256（文本 + 图片 PID + 视频 PID）."""
        import unicodedata

        text = unicodedata.normalize("NFC", self.text_content.strip())
        # 纳入图片 PID（排序后拼接，确保稳定性）
        pic_ids = "|".join(sorted(p.weibo_pid for p in self.pics if p.weibo_pid))
        vid_id = self.video.weibo_pid if self.video else ""
        combined = f"{text}\n{pic_ids}\n{vid_id}"
        return hashlib.sha256(combined.encode("utf-8")).hexdigest()

    def has_media(self) -> bool:
        """是否有媒体内容."""
        return bool(self.pics) or self.video is not None

    def to_dict(self) -> dict:
        """导出为字典."""
        return {
            "post_id": self.post_id,
            "bid": self.bid,
            "uid": self.uid,
            "screen_name": self.screen_name,
            "text_content": self.text_content,
            "text_html": self.text_html,
            "created_at": self.created_at,
            "source": self.source,
            "reposts_count": self.reposts_count,
            "comments_count": self.comments_count,
            "attitudes_count": self.attitudes_count,
            "is_pinned": self.is_pinned,
            "is_repost": self.is_repost,
            "page_url": self.page_url,
            "region_name": self.region_name,
            "pics": [
                {
                    "pid": p.weibo_pid,
                    "url": p.original_url,
                    "local_path": p.local_path,
                }
                for p in self.pics
            ],
            "video": {
                "vid": self.video.weibo_pid,
                "url": self.video.original_url,
                "local_path": self.video.local_path,
                "duration": self.video.duration,
            }
            if self.video
            else None,
            "content_hash": self.content_hash,
            "version_count": self.version_count,
        }

    def to_markdown(self) -> str:
        """导出为 Markdown."""
        import re

        lines: list[str] = []
        lines.append(f"---")
        lines.append(f'post_id: "{self.post_id}"')
        lines.append(f'bid: "{self.bid}"')
        lines.append(f'author: "{self.screen_name}"')
        lines.append(f'created_at: "{self.created_at}"')
        lines.append(f'source: "{self.source}"')
        lines.append(f"reposts: {self.reposts_count}")
        lines.append(f"comments: {self.comments_count}")
        lines.append(f"attitudes: {self.attitudes_count}")
        if self.region_name:
            lines.append(f'location: "{self.region_name}"')
        lines.append(f'permalink: "{self.page_url}"')
        if self.is_pinned:
            lines.append("pinned: true")
        lines.append(f"---")
        lines.append("")

        # 正文
        # 清理 HTML 标签用于纯文本展示，但保留基本格式
        text = self.text_html
        # 将 <br/> 转为换行
        text = re.sub(r"<br\s*/?>", "\n", text)
        # 移除其他 HTML 标签
        text = re.sub(r"<[^>]+>", "", text)
        lines.append(text)
        lines.append("")

        # 图片
        if self.pics:
            lines.append("## 图片")
            lines.append("")
            for i, pic in enumerate(self.pics, 1):
                alt = f"图片 {i}"
                if pic.local_path:
                    ext = Path(pic.local_path).suffix  # 实际扩展名 (.jpg/.gif/.png...)
                    lines.append(f"![{alt}](./images/{pic.weibo_pid}_original{ext})")
                else:
                    lines.append(f"![{alt}]({pic.original_url})")
            lines.append("")

        # 视频
        if self.video:
            lines.append("## 视频")
            lines.append("")
            if self.video.local_path:
                lines.append(f"[视频](./videos/{self.video.weibo_pid}.mp4)")
            else:
                lines.append(f"[视频链接]({self.video.original_url})")
            lines.append("")

        return "\n".join(lines)

    def to_txt(self) -> str:
        """导出为纯文本."""
        import re

        lines: list[str] = []
        lines.append(f"作者: {self.screen_name}")
        lines.append(f"时间: {self.created_at}")
        lines.append(f"来源: {self.source}")
        lines.append(f"链接: {self.page_url}")
        lines.append(f"转发: {self.reposts_count} | 评论: {self.comments_count} | 点赞: {self.attitudes_count}")
        if self.region_name:
            lines.append(f"位置: {self.region_name}")
        if self.is_pinned:
            lines.append("[置顶]")
        lines.append("-" * 40)

        text = self.text_html
        text = re.sub(r"<br\s*/?>", "\n", text)
        text = re.sub(r"<[^>]+>", "", text)
        lines.append(text)

        if self.pics:
            lines.append("")
            lines.append(f"图片 ({len(self.pics)} 张):")
            for i, pic in enumerate(self.pics, 1):
                lines.append(f"  {i}. {pic.original_url}")

        if self.video:
            lines.append("")
            lines.append(f"视频: {self.video.original_url}")

        return "\n".join(lines)

    # ---- 静态方法 ----

    @staticmethod
    def _clean_html(html: str) -> str:
        """移除 HTML 标签，保留 emoji 表情的 alt 文本."""
        import re

        if not html:
            return ""

        # <br/> → 换行
        text = re.sub(r"<br\s*/?>", "\n", html)
        # <img ... alt="[emoji_text]" ...> → 保留 alt 文本（微博表情包）
        text = re.sub(r'<img[^>]+alt="([^"]*)"[^>]*>', r"\1", text)
        # 移除其他 HTML 标签
        text = re.sub(r"<[^>]+>", "", text)
        # 清理多余空白
        text = re.sub(r"  +", " ", text)
        return text.strip()
