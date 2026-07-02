"""微博卡片快照渲染器 — 用 Pillow 绘制微博风格卡片，保存为 JPG.

覆盖：原创、转发、转发源(resource/)、历史编辑版本(versions/v{N}/)
"""
from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path
from datetime import datetime

import httpx
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("weibo_saver.core.card_renderer")

# ═══════════════════════════════════════════
# 布局常量
# ═══════════════════════════════════════════
CARD_W = 800
PAD = 18
AVATAR_S = 44
HEADER_H = AVATAR_S + PAD + 2
STATS_H = 28
THUMB_SIZE = 220              # 缩略图正方形尺寸 (px)
THUMB_GAP = 10                # 缩略图间距
THUMB_COLS = 3                # 固定 3 列
RETWEET_INDENT = 14
LINE_H = 22
LINE_SPACING = 5

# 莫兰迪 + 微博色系
CLR_BG = (255, 255, 255)
CLR_RETWEET_BG = (247, 248, 250)
CLR_TEXT = (51, 51, 51)
CLR_SEC = (140, 140, 150)
CLR_ACCENT = (235, 115, 60)
CLR_ACCENT_BLUE = (80, 125, 210)
CLR_DIVIDER = (235, 235, 240)
CLR_STATS = (150, 150, 158)
CLR_VERSION = (235, 115, 60)
CLR_AVATAR_BG = (220, 225, 235)
CLR_PLAY = (235, 115, 60)


class CardRenderer:
    """微博卡片快照渲染器."""

    def __init__(self, http_client: httpx.AsyncClient | None = None, avatar_path: str = "", target_uid: str = ""):
        self._fonts: dict[int, ImageFont.FreeTypeFont] = {}
        self._client = http_client
        self._avatar_path = avatar_path
        self._target_uid = str(target_uid)
        self._avatar_img: Image.Image | None = None
        if avatar_path and Path(avatar_path).exists():
            try:
                self._avatar_img = Image.open(avatar_path).resize((AVATAR_S, AVATAR_S), Image.LANCZOS)
            except Exception:
                pass
        self._init_fonts()

    def _init_fonts(self):
        font_dirs = [Path("C:/Windows/Fonts")]
        yahei_candidates = ["msyh.ttc", "msyhbd.ttc", "simhei.ttf", "simsun.ttc", "STSONG.TTF"]
        font_path = None
        for d in font_dirs:
            if not d.exists():
                continue
            for name in yahei_candidates:
                p = d / name
                if p.exists():
                    font_path = str(p); break
            if not font_path:
                for f in sorted(d.glob("*.ttc")):
                    font_path = str(f); break
            if not font_path:
                for f in sorted(d.glob("*.ttf")):
                    font_path = str(f); break
            if font_path:
                break
        if font_path:
            try:
                for sz in [11, 12, 14, 16, 18, 22, 26, 30]:
                    self._fonts[sz] = ImageFont.truetype(font_path, sz)
                logger.info(f"卡片字体: {font_path}")
            except Exception as e:
                logger.warning(f"字体加载失败: {e}")
        if not self._fonts:
            logger.warning("未找到中文字体，使用默认字体")

    # ═══════════════════════════════════════════
    # 主入口
    # ═══════════════════════════════════════════

    def render(self, post, post_dir: Path, output_path: Path,
               *, is_version: bool = False, version_num: int = 0,
               avatar_override: str = "") -> Path | None:
        try:
            img = self._draw_card(post, post_dir, is_version, version_num, avatar_override)
            if img is None:
                return None
            output_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(str(output_path), "JPEG", quality=88)
            return output_path
        except Exception as e:
            logger.warning(f"卡片渲染失败 | {getattr(post, 'bid', '?')} | {e}")
            return None

    def _use_avatar_for(self, post, avatar_override: str = "") -> Image.Image | None:
        if avatar_override and Path(avatar_override).exists():
            try:
                return Image.open(avatar_override).resize((AVATAR_S, AVATAR_S), Image.LANCZOS)
            except Exception:
                pass
        if self._avatar_img and str(getattr(post, 'uid', '')) == self._target_uid:
            return self._avatar_img
        return None

    # ═══════════════════════════════════════════
    # 卡片绘制
    # ═══════════════════════════════════════════

    def _draw_card(self, post, post_dir, is_version, version_num, avatar_override=""):
        f = self._font; body_w = CARD_W - PAD * 2
        sections: list[tuple[str, int]] = []

        if is_version and version_num:
            sections.append(("version", 24))
        sections.append(("header", HEADER_H))

        clean_text = self._clean_html(getattr(post, 'text_html', '') or post.text_content)
        text_lines = self._wrap(clean_text, body_w, f(16))
        body_h = len(text_lines) * (LINE_H + LINE_SPACING) + 8
        sections.append(("text", body_h))

        if post.is_long_text:
            sections.append(("longtext", 20))

        media_h = self._calc_media_height(post, post_dir, body_w)
        if media_h > 0:
            sections.append(("media", media_h))
        sections.append(("stats", STATS_H + 4))

        if post.is_repost and post.retweeted_post:
            rt = post.retweeted_post
            rt_h = self._calc_retweet_height(rt, post_dir, body_w)
            sections.append(("retweet", rt_h + 8))

        pi = getattr(post, 'page_info', None) or {}
        if pi and pi.get("type") not in ("video", None, ""):
            sections.append(("article", 40))

        total_h = sum(h for _, h in sections) + PAD
        img = Image.new("RGB", (CARD_W, max(total_h, 120)), CLR_BG)
        draw = ImageDraw.Draw(img); y = PAD

        for stype, sh in sections:
            if stype == "version":
                y = self._draw_version(draw, y, sh, version_num)
            elif stype == "header":
                y = self._draw_header(draw, img, y, sh, post, body_w, avatar_override)
            elif stype == "text":
                y = self._draw_text(draw, y, text_lines)
            elif stype == "longtext":
                draw.text((PAD, y), "... 展开全文", fill=CLR_ACCENT, font=f(14)); y += sh
            elif stype == "media":
                y = self._draw_media(draw, img, post, post_dir, PAD, y, body_w)
            elif stype == "stats":
                y = self._draw_stats(draw, y, sh, post, body_w)
            elif stype == "retweet":
                y = self._draw_retweet(draw, img, post.retweeted_post, post_dir, PAD, y, body_w)
            elif stype == "article":
                y = self._draw_article_link(draw, y, sh, pi, body_w)

        return img

    # ═══════════════════════════════════════════
    # 各区块绘制
    # ═══════════════════════════════════════════

    def _draw_version(self, draw, y, h, vn):
        badge_w, badge_h = 56, 20
        x = CARD_W - PAD - badge_w
        draw.rounded_rectangle([x, y, x + badge_w, y + badge_h], radius=4, fill=CLR_VERSION)
        t = f"v{vn}"; b = draw.textbbox((0, 0), t, font=self._font(12))
        draw.text((x + (badge_w - (b[2] - b[0])) // 2, y + (badge_h - (b[3] - b[1])) // 2 - 1),
                  t, fill=(255, 255, 255), font=self._font(12))
        return y + h

    def _draw_header(self, draw, canvas, y, h, post, body_w, avatar_override=""):
        f = self._font; ax, ay = PAD, y + 4
        avatar_img = self._use_avatar_for(post, avatar_override)
        if avatar_img:
            mask = Image.new("L", (AVATAR_S, AVATAR_S), 0)
            ImageDraw.Draw(mask).ellipse([0, 0, AVATAR_S, AVATAR_S], fill=255)
            canvas.paste(avatar_img, (ax, ay), mask)
        else:
            initial = post.screen_name[0] if post.screen_name else "?"
            # 确定性色相（hashlib 替代 Python hash，避免 PYTHONHASHSEED 随机化）
            import hashlib
            hue = int(hashlib.md5(str(post.uid).encode()).hexdigest()[:8], 16) % 360 if post.uid else 210
            rgb = self._hsl_to_rgb(hue, 0.5, 0.7)
            draw.ellipse([ax, ay, ax + AVATAR_S, ay + AVATAR_S], fill=rgb)
            b = draw.textbbox((0, 0), initial, font=f(22))
            draw.text((ax + (AVATAR_S - (b[2] - b[0])) // 2, ay + (AVATAR_S - (b[3] - b[1])) // 2 - 1),
                      initial, fill=(255, 255, 255), font=f(22))
        nx = ax + AVATAR_S + 12
        draw.text((nx, y + 2), post.screen_name, fill=CLR_TEXT, font=f(18))
        sub = self._fmt_date(post.created_at)
        if post.source: sub += f"  ·  {post.source}"
        draw.text((nx, y + 26), sub, fill=CLR_SEC, font=f(12))
        # 类型标识（纯文字，深灰，无背景）
        label = post.post_type_label
        b = draw.textbbox((0, 0), label, font=f(16))
        lx = CARD_W - PAD - (b[2] - b[0])
        draw.text((lx, y + 6), label, fill=CLR_TEXT, font=f(16))
        y2 = y + h - 6
        draw.line([(PAD, y2), (CARD_W - PAD, y2)], fill=CLR_DIVIDER, width=1)
        return y + h

    def _draw_text(self, draw, y, lines):
        f = self._font(16)
        for line in lines:
            self._draw_safe(draw, (PAD, y), line, f, CLR_TEXT)
            y += LINE_H + LINE_SPACING
        return y + 4

    def _draw_media(self, draw, canvas, post, post_dir, x, y, body_w):
        pics = post.pics if post.pics else []
        has_video = post.video is not None
        if not pics and not has_video:
            return y
        if has_video and not pics:
            vh = self._draw_video_cover(draw, canvas, post, post_dir, x, y, body_w)
            return y + vh + 6
        return self._draw_image_grid(draw, canvas, pics, post_dir, x, y, body_w)

    def _draw_image_grid(self, draw, canvas, pics, post_dir, x, y, body_w):
        n = min(len(pics), 9)
        if n == 0: return y
        cols = min(n, THUMB_COLS); size = THUMB_SIZE
        for i in range(n):
            col, row = i % cols, i // cols
            tx = x + col * (size + THUMB_GAP); ty = y + row * (size + THUMB_GAP)
            loaded = False
            local = pics[i].local_path if i < len(pics) else ""
            if local and Path(local).exists():
                try:
                    thumb = Image.open(local)
                    thumb = self._crop_square(thumb, size)
                    canvas.paste(thumb, (tx, ty)); loaded = True
                except Exception:
                    pass
            if not loaded:
                draw.rectangle([tx, ty, tx + size, ty + size], fill=(235, 237, 242), outline=CLR_DIVIDER, width=1)
        rows = (n + cols - 1) // cols
        return y + rows * (size + THUMB_GAP)

    def _crop_square(self, img, target_size):
        w, h = img.size; side = min(w, h)
        left = (w - side) // 2; top = (h - side) // 2
        cropped = img.crop((left, top, left + side, top + side))
        if side != target_size:
            cropped = cropped.resize((target_size, target_size), Image.LANCZOS)
        return cropped

    def _draw_video_cover(self, draw, canvas, post, post_dir, x, y, body_w):
        max_w = min(body_w, 540); max_h = 340
        pi = getattr(post, 'page_info', None) or {}
        # 视频封面（占位：深色背景+播放按钮；封面异步下载在事件循环中不可用）
        draw.rectangle([x, y, x + max_w, y + max_h], fill=(40, 40, 50), outline=(60, 60, 70), width=1)
        cx, cy = x + max_w // 2, y + max_h // 2; r = 28
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=CLR_PLAY)
        tri = [(cx - 8, cy - 14), (cx - 8, cy + 14), (cx + 12, cy)]; draw.polygon(tri, fill=(255, 255, 255))
        duration = pi.get("duration", post.video.duration if post.video else 0)
        if duration:
            dur_str = f"{int(duration // 60):d}:{int(duration % 60):02d}"
            b = draw.textbbox((0, 0), dur_str, font=self._font(12))
            dw, dh = b[2] - b[0] + 10, b[3] - b[1] + 6
            draw.rounded_rectangle([x + max_w - dw - 8, y + max_h - dh - 8, x + max_w - 8, y + max_h - 8], radius=3, fill=(60, 60, 65))
            draw.text((x + max_w - dw - 3, y + max_h - dh - 5), dur_str, fill=(255, 255, 255), font=self._font(12))
        return max_h

    def _draw_stats(self, draw, y, h, post, body_w):
        y0 = y
        draw.line([(PAD, y), (CARD_W - PAD, y)], fill=CLR_DIVIDER, width=1); y += 6
        f = self._font(14); icon_s = 14; cx = PAD
        self._draw_repost_icon(draw, cx, y + 2, icon_s); cx += icon_s + 4
        draw.text((cx, y), str(post.reposts_count), fill=CLR_STATS, font=f)
        cx += self._font(14).getbbox(str(post.reposts_count))[2] - self._font(14).getbbox(str(post.reposts_count))[0] + 16
        self._draw_comment_icon(draw, cx, y + 2, icon_s); cx += icon_s + 4
        draw.text((cx, y), str(post.comments_count), fill=CLR_STATS, font=f)
        cx += self._font(14).getbbox(str(post.comments_count))[2] - self._font(14).getbbox(str(post.comments_count))[0] + 16
        self._draw_like_icon(draw, cx, y + 2, icon_s); cx += icon_s + 4
        draw.text((cx, y), str(post.attitudes_count), fill=CLR_STATS, font=f)
        return y0 + h

    def _draw_repost_icon(self, draw, x, y, s):
        c = CLR_STATS; r = s // 2; cx, cy = x + r, y + r
        draw.arc([x, y, x + s, y + s], 30, 330, fill=c, width=2)
        import math; angle = math.radians(30)
        ax = cx + (r - 1) * math.cos(angle); ay = cy - (r - 1) * math.sin(angle)
        draw.polygon([(ax, ay), (ax - 4, ay - 3), (ax + 3, ay - 4)], fill=c)

    def _draw_comment_icon(self, draw, x, y, s):
        c = CLR_STATS
        draw.rounded_rectangle([x, y, x + s, y + s - 3], radius=3, outline=c, width=2)
        draw.polygon([(x + 4, y + s - 3), (x + 2, y + s + 2), (x + 8, y + s - 3)], fill=c)

    def _draw_like_icon(self, draw, x, y, s):
        c = CLR_STATS; cx, cy = x + s // 2, y + s // 2 + 1
        r = s // 4
        draw.arc([cx - s//2, cy - s//3, cx, cy + s//3], 180, 360, fill=c, width=2)
        draw.arc([cx, cy - s//3, cx + s//2, cy + s//3], 180, 360, fill=c, width=2)
        draw.polygon([(cx - s//2 + 2, cy + 1), (cx, cy + s//2 + 1), (cx + s//2 - 2, cy + 1)], fill=c)

    def _draw_retweet(self, draw, canvas, rt, post_dir, x, y, body_w):
        f = self._font; inner_x = x + RETWEET_INDENT; inner_w = body_w - RETWEET_INDENT * 2; inner_pad = 10
        rt_text = self._clean_html(rt.text_content) if rt.text_content else ""
        rt_lines = self._wrap(rt_text, inner_w - inner_pad * 2, f(14))
        rt_text_h = len(rt_lines) * (19 + 4) + 8
        rt_media_h = 0; rt_pics = rt.pics if rt.pics else []; rt_has_video = rt.video is not None
        if rt_pics: rt_media_h = self._calc_thumb_grid_h(len(rt_pics), inner_w - inner_pad * 2)
        elif rt_has_video: rt_media_h = 180
        rt_total_h = inner_pad + 20 + rt_text_h + rt_media_h + inner_pad
        rt_y = y
        draw.rounded_rectangle([inner_x, rt_y, inner_x + inner_w, rt_y + rt_total_h], radius=8, fill=CLR_RETWEET_BG)
        iy = rt_y + inner_pad
        author = f"@{rt.screen_name}" if rt.screen_name else "@原作者"
        draw.text((inner_x + inner_pad, iy), author, fill=CLR_ACCENT_BLUE, font=f(14)); iy += 20
        for line in rt_lines[:15]:
            self._draw_safe(draw, (inner_x + inner_pad, iy), line, f(14), CLR_TEXT); iy += 19 + 4
        iy += 4
        if rt_pics:
            iy = self._draw_image_grid(draw, canvas, rt_pics,
                post_dir / "resource" if (post_dir / "resource").exists() else post_dir,
                inner_x + inner_pad, iy, inner_w - inner_pad * 2)
        elif rt_has_video:
            vw = min(inner_w - inner_pad * 2, 320); vh = 180
            draw.rectangle([inner_x + inner_pad, iy, inner_x + inner_pad + vw, iy + vh], fill=(40, 40, 50), outline=(60, 60, 70), width=1)
            cx, cy = inner_x + inner_pad + vw // 2, iy + vh // 2; r = 22
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=CLR_PLAY)
            tri = [(cx - 6, cy - 10), (cx - 6, cy + 10), (cx + 9, cy)]; draw.polygon(tri, fill=(255, 255, 255))
            iy += vh
        return y + rt_total_h + 6

    def _draw_article_link(self, draw, y, h, pi, body_w):
        title = pi.get("page_title", "") or pi.get("content2", "") or "网页链接"
        f = self._font(13)
        draw.rounded_rectangle([PAD, y, CARD_W - PAD, y + h - 4], radius=6, fill=(242, 244, 248))
        b = draw.textbbox((0, 0), title, font=f); tw = b[2] - b[0]
        while tw > body_w - 60 and len(title) > 5: title = title[:-2] + "…"; b = draw.textbbox((0, 0), title, font=f); tw = b[2] - b[0]
        draw.text((PAD + 12, y + 8), title, fill=CLR_ACCENT_BLUE, font=f)
        return y + h

    # ═══════════════════════════════════════════
    # 高度计算
    # ═══════════════════════════════════════════

    def _calc_media_height(self, post, post_dir, body_w):
        pics = post.pics if post.pics else []; has_video = post.video is not None
        if not pics and not has_video: return 0
        if has_video and not pics: return 346
        return self._calc_thumb_grid_h(len(pics), body_w) + 6

    def _calc_thumb_grid_h(self, n, body_w):
        n = min(n, 9)
        if n == 0: return 0
        cols = min(n, THUMB_COLS); rows = (n + cols - 1) // cols
        return rows * THUMB_SIZE + (rows - 1) * THUMB_GAP

    def _calc_retweet_height(self, rt, post_dir, body_w):
        inner_w = body_w - RETWEET_INDENT * 2; inner_pad = 10
        rt_text = self._clean_html(rt.text_content) if rt.text_content else ""
        rt_lines = self._wrap(rt_text, inner_w - inner_pad * 2, self._font(14))
        rt_text_h = len(rt_lines) * 23 + 8
        rt_media_h = 0
        if rt.pics: rt_media_h = self._calc_thumb_grid_h(len(rt.pics), inner_w - inner_pad * 2)
        elif rt.video: rt_media_h = 186
        return inner_pad + 20 + rt_text_h + rt_media_h + inner_pad + 4

    # ═══════════════════════════════════════════
    # 文本排版
    # ═══════════════════════════════════════════

    @staticmethod
    def _clean_html(text):
        """清理 HTML：提取 img alt 文本，剥离原生 emoji，保留纯文本."""
        if not text: return ""
        t = re.sub(r"<br\s*/?>", "\n", text)
        t = re.sub(r'<img[^>]+alt="([^"]*)"[^>]*>', r"\1", t)
        t = re.sub(r"<[^>]+>", "", t)
        t = re.sub(r"  +", " ", t)
        result = []
        for ch in t:
            cp = ord(ch)
            if cp > 0xFFFF or (0x1F600 <= cp <= 0x1F9FF) or (0x2600 <= cp <= 0x27BF): continue
            if unicodedata.category(ch) == 'So': continue
            result.append(ch)
        return "".join(result).strip()

    def _wrap(self, text, max_w, font):
        if not text: return []
        lines = []; cur = ""
        for ch in text:
            if ch == "\n": lines.append(cur); cur = ""; continue
            test = cur + ch
            try:
                b = font.getbbox(test) if hasattr(font, 'getbbox') else (0, 0, len(test) * 14, 20)
                w = b[2] - b[0]
            except Exception: w = len(test) * 14
            if w > max_w and cur: lines.append(cur); cur = ch
            else: cur = test
        if cur: lines.append(cur)
        return lines

    def _draw_safe(self, draw, pos, text, font, fill):
        try:
            draw.text(pos, text, fill=fill, font=font)
        except Exception:
            x, y = pos
            for ch in text:
                try:
                    b = draw.textbbox((x, y), ch, font=font)
                    draw.text((x, y), ch, fill=fill, font=font); x = b[2]
                except Exception: x += 12

    def _font(self, size: int):
        return self._fonts.get(size, ImageFont.load_default())

    # ═══════════════════════════════════════════
    # 工具
    # ═══════════════════════════════════════════

    @staticmethod
    def _fmt_date(s):
        if not s: return ""
        try: return datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y").strftime("%Y-%m-%d %H:%M")
        except ValueError: return s[:16] if len(s) >= 16 else s

    @staticmethod
    def _hsl_to_rgb(h, s, l):
        h = h % 360; c = (1 - abs(2 * l - 1)) * s; x = c * (1 - abs((h / 60) % 2 - 1)); m = l - c / 2
        if h < 60: r, g, b = c, x, 0
        elif h < 120: r, g, b = x, c, 0
        elif h < 180: r, g, b = 0, c, x
        elif h < 240: r, g, b = 0, x, c
        elif h < 300: r, g, b = x, 0, c
        else: r, g, b = c, 0, x
        return (int((r + m) * 255), int((g + m) * 255), int((b + m) * 255))
