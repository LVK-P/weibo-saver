"""全量抓取脚本：自动处理 XSRF-TOKEN 更新."""
import asyncio, httpx, json, sys, time, random, os
from pathlib import Path
from datetime import datetime, timezone

# Windows 控制台 UTF-8 编码（解决 emoji 等字符的 UnicodeEncodeError）
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).parent))

# === 配置 ===
# Cookie 必须通过环境变量 WEIBO_COOKIE_STRING 提供
COOKIE_STRING = os.environ.get("WEIBO_COOKIE_STRING", "")

TARGET_UID = os.environ.get("WEIBO_TARGET_UID", "")
ARCHIVE_ROOT = Path(os.environ.get("WEIBO_ARCHIVE_ROOT",
    str(Path.home() / "Documents" / "WeiboArchive")))
MIN_DELAY = 2.0
MAX_DELAY = 5.0
MAX_CARDS = int(os.environ.get("WEIBO_MAX_CARDS", "5"))

# Phase 3: 内容类型过滤
CONTENT_TEXT = os.environ.get("WEIBO_CONTENT_TEXT", "1") == "1"
CONTENT_IMAGES = os.environ.get("WEIBO_CONTENT_IMAGES", "1") == "1"
CONTENT_VIDEOS = os.environ.get("WEIBO_CONTENT_VIDEOS", "1") == "1"

# Phase 8: 补采模式
WEIBO_BACKFILL = os.environ.get("WEIBO_BACKFILL", "0") == "1"
WEIBO_BACKFILL_TYPES = os.environ.get("WEIBO_BACKFILL_TYPES", "")
WEIBO_BACKFILL_MAX_POSTS = int(os.environ.get("WEIBO_BACKFILL_MAX_POSTS", "50"))

# 速率限制（登录用户: 1000次/小时）
HOURLY_LIMIT = 1000
SAFE_LIMIT = 800   # 提前停止，留余量
_request_count = 0
_request_start_time = time.monotonic()
_last_request_time = 0.0        # 上次请求时间戳
_rate_limit_hit = False          # 本次抓取是否触发了限流等待
MIN_REQ_GAP = 1.2               # 请求间最小间隔（秒），防突发检测
MAX_REQ_GAP = 2.5               # 请求间最大间隔（秒）

async def _check_rate() -> bool:
    """检查速率限制 + 请求间隔.

    1. 确保请求间有随机间隔（防突发检测触发反爬）
    2. 确保每小时不超过 SAFE_LIMIT 次
    """
    global _request_count, _request_start_time, _last_request_time, _rate_limit_hit

    # ---- 请求间间隔 ----
    gap = time.monotonic() - _last_request_time
    if 0 < gap < MIN_REQ_GAP:
        await asyncio.sleep(random.uniform(MIN_REQ_GAP - gap, MAX_REQ_GAP - gap))

    # ---- 每小时上限 ----
    while True:
        elapsed = time.monotonic() - _request_start_time
        if elapsed > 3600:
            _request_count = 0
            _request_start_time = time.monotonic()
            return True
        if _request_count >= SAFE_LIMIT:
            _rate_limit_hit = True
            wait_sec = 3600 - elapsed + random.uniform(5, 15)
            wait_min = wait_sec / 60
            log(f"  [限流] 已达到 {SAFE_LIMIT}/{HOURLY_LIMIT} 次，等待 {wait_min:.0f} 分钟...")
            await asyncio.sleep(wait_sec)
            _request_count = 0
            _request_start_time = time.monotonic()
            return True
        return True

def _count_request():
    global _request_count, _last_request_time
    _request_count += 1
    _last_request_time = time.monotonic()
    if _request_count % 50 == 0:
        elapsed = time.monotonic() - _request_start_time
        remaining = HOURLY_LIMIT - _request_count
        log(f"  [计数] 已用 {_request_count}/{HOURLY_LIMIT} 次 | 剩余 {remaining} | 已过 {elapsed/60:.0f}分钟")

class CookieExpiredMidCrawl(Exception):
    """Cookie 在爬取中途过期."""
    pass

async def _safe_get(client, url, **kwargs):
    """带 Cookie 过期检测的 HTTP GET."""
    r = await client.get(url, **kwargs)
    if r.status_code in (403, 302):
        # 尝试验证 Cookie 是否仍有效
        try:
            v = await client.get("https://m.weibo.cn/api/config")
            if not v.json().get("data", {}).get("login", False):
                raise CookieExpiredMidCrawl(
                    f"Cookie 已失效 (HTTP {r.status_code} at {url})")
        except CookieExpiredMidCrawl:
            raise
        except Exception:
            raise CookieExpiredMidCrawl(
                f"Cookie 疑似失效 (HTTP {r.status_code} at {url})")
    return r

PROGRESS_FILE = ARCHIVE_ROOT / "progress.txt"

def log(msg: str):
    t = datetime.now().strftime("%H:%M:%S")
    line = f"[{t}] {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("ascii", errors="replace").decode("ascii"), flush=True)
    except OSError as e:
        # stdout 不可用（极罕见）
        pass
    try:
        ARCHIVE_ROOT.mkdir(parents=True, exist_ok=True)
        with open(PROGRESS_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as e:
        # 磁盘满或权限不足
        print(f"[DISK_ERR] 无法写入日志: {e}", flush=True)

stats = {"pages": 0, "posts_saved": 0, "posts_skipped": 0, "reposts_skipped": 0,
         "images": 0, "videos": 0, "errors": [], "start_time": 0, "edited": 0,
         "content_expanded": 0, "edit_histories_fetched": 0}


async def _fetch_edit_history_api(client, post_id: str) -> list[dict]:
    """通过 API 获取编辑历史页面并解析版本.

    编辑历史页: https://m.weibo.cn/detail/{post_id}/edit/history
    返回 HTML 页面，其中包含各版本的卡片。
    """
    versions: list[dict] = []
    try:
        r = await client.get(
            f"https://m.weibo.cn/detail/{post_id}/edit/history",
            headers={"Accept": "text/html",
                     "X-Requested-With": "XMLHttpRequest",
                     "Referer": f"https://m.weibo.cn/detail/{post_id}"}
        )
        if r.status_code != 200:
            return versions

        html = r.text
        if "编辑" not in html:
            return versions

        import re

        # 从编辑历史页面提取各版本
        # 页面中的版本通常以时间标记分隔
        time_pattern = re.compile(
            r'(\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{1,2}(?::\d{1,2})?)',
            re.IGNORECASE
        )
        times = time_pattern.findall(html)

        # 提取段落文本
        text_blocks = re.findall(
            r'<p[^>]*class="[^"]*txt[^"]*"[^>]*>(.*?)</p>',
            html, re.DOTALL
        )
        if not text_blocks:
            text_blocks = re.findall(r'<p[^>]*>(.*?)</p>', html, re.DOTALL)

        def strip_html(s):
            return re.sub(r'<[^>]+>', '', s).replace('&nbsp;', ' ').strip()

        cleaned = [strip_html(b) for b in text_blocks if len(strip_html(b)) > 5]

        for i, text in enumerate(cleaned):
            t = times[i] if i < len(times) else ""
            versions.append({"text": text, "time": t, "version": i + 1})

    except Exception:
        pass

    return versions


def _detect_image_changes(old_pids: set, new_pics: list) -> dict:
    """对比新旧图片列表，返回变化详情."""
    new_pids = {p.weibo_pid for p in new_pics}
    return {
        "added": list(new_pids - old_pids),
        "removed": list(old_pids - new_pids),
        "old_count": len(old_pids),
        "new_count": len(new_pids),
        "changed": old_pids != new_pids,
    }


async def _fetch_full_text(client, post_id: str) -> str:
    """通过详情页API获取博文完整内容（突破卡片文字限制）."""
    try:
        r = await client.get(
            "https://m.weibo.cn/statuses/extend",
            params={"id": post_id},
            headers={"Referer": f"https://m.weibo.cn/detail/{post_id}",
                     "X-Requested-With": "XMLHttpRequest",
                     "Accept": "application/json"}
        )
        if r.status_code == 200:
            data = r.json()
            if data.get("ok") and data.get("data"):
                long_text = data["data"].get("longTextContent", "")
                if long_text:
                    return long_text
    except Exception:
        pass
    return ""


async def _fetch_edit_history(client, post_id: str) -> list[dict]:
    """抓取编辑记录页面，返回所有历史版本.

    流程: 详情页 → 检测已编辑 → 编辑记录页 → 解析各版本
    """
    versions: list[dict] = []
    try:
        # Step 1: 获取详情页HTML，检测是否有"已编辑"标记
        r = await client.get(
            f"https://m.weibo.cn/detail/{post_id}",
            headers={"Accept": "text/html",
                     "Referer": f"https://m.weibo.cn/u/0"}
        )
        if "已编辑" not in r.text:
            return versions  # 未编辑过

        # Step 2: 获取编辑记录页
        edit_url = f"https://m.weibo.cn/p/231440_-_{post_id}"
        r2 = await client.get(
            edit_url,
            params={"title": "编辑记录"},
            headers={"Accept": "text/html",
                     "X-Requested-With": "XMLHttpRequest",
                     "Referer": f"https://m.weibo.cn/detail/{post_id}"}
        )
        if r2.status_code != 200:
            return versions

        html = r2.text

        # Step 3: 从页面提取各版本的文本和时间
        import re
        # 编辑记录页面的卡片通常包含时间和文本
        # 匹配时间模式: YYYY-MM-DD HH:MM:SS 或 微博格式
        time_pattern = re.compile(
            r'(?:编辑于|发布于)?\s*(\d{4}[-/]\d{1,2}[-/]\d{1,2}\s*\d{1,2}:\d{1,2}(?::\d{1,2})?)',
            re.IGNORECASE
        )
        # 提取文本内容块（去除HTML标签后的纯文本）
        text_blocks = re.findall(r'<p[^>]*>(.*?)</p>', html, re.DOTALL)
        # 也尝试 weibo-text 类
        text_blocks += re.findall(r'class="[^"]*weibo-text[^"]*"[^>]*>(.*?)</(?:p|div)>', html, re.DOTALL)

        # 简单去HTML标签
        def strip_html(s):
            return re.sub(r'<[^>]+>', '', s).strip()

        cleaned_blocks = [strip_html(b) for b in text_blocks if len(strip_html(b)) > 5]
        times = time_pattern.findall(html)

        for i, text in enumerate(cleaned_blocks):
            t = times[i] if i < len(times) else ""
            versions.append({"text": text, "time": t, "version": i + 1})

    except Exception as e:
        log(f"    [编辑历史] 抓取异常: {e}")

    return versions


def _ym(created_at: str) -> str:
    """从 created_at 提取 YYYY/MM 字符串."""
    from weibo_saver.storage.layout import Layout
    dt = Layout._parse_created_at(created_at)
    return dt.strftime("%Y/%m") if dt else "unknown"


async def _save_post_to_dir(db, fw, md, renderer, post, dest_dir, uid, sn, prefix="", client=None):
    """将博文保存到指定目录（用于 resource 等子目录）——含长文展开."""
    # ---- 长文先展开 ----
    need_expand = (
        post.is_long_text
        or post.text_content.rstrip().endswith("全文")
        or (post.text_html and ("...全文" in post.text_html or "…全文" in post.text_html))
    )
    if need_expand and client and post.post_id:
        try:
            await _check_rate()
            r = await client.get(
                "https://m.weibo.cn/statuses/extend",
                params={"id": post.post_id},
                headers={"Referer": f"https://m.weibo.cn/detail/{post.post_id}",
                         "X-Requested-With": "XMLHttpRequest"}
            )
            _count_request()
            if r.status_code == 200:
                data = r.json()
                if data.get("ok"):
                    lt = data["data"].get("longTextContent", "")
                    if lt and len(lt) > len(post.text_content):
                        added = len(lt) - len(post.text_content)
                        post.text_content = lt
                        post.text_html = lt
                        post.content_hash = post._compute_hash()
                        log(f"{prefix}    [展开] bid={post.bid} +{added}字")
        except Exception as e:
            pass

    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / "images").mkdir(exist_ok=True)
    (dest_dir / "videos").mkdir(exist_ok=True)
    (dest_dir / "versions").mkdir(exist_ok=True)

    # Phase 3: 内容类型过滤
    if not CONTENT_IMAGES:
        post.pics = []
    if not CONTENT_VIDEOS:
        post.video = None

    try:
        await md.download_all(post, dest_dir)
        if post.pics and CONTENT_IMAGES:
            stats["images"] += len(post.pics)
        if post.video and CONTENT_VIDEOS and post.video.local_path:
            stats["videos"] += 1
    except Exception as e:
        pass

    await fw.write_post(post, dest_dir)

    # 下载原博主头像（若与目标用户不同，用于转发原博卡片）
    avatar_override = ""
    av_url = getattr(post, 'avatar_url', '')
    if av_url and str(uid) != str(TARGET_UID):
        try:
            r_av = await client.get(av_url, timeout=10.0,
                headers={"Referer": "https://m.weibo.cn/"})
            if r_av.status_code == 200 and len(r_av.content) > 100:
                av_file = dest_dir / "avatar.jpg"
                av_file.write_bytes(r_av.content)
                avatar_override = str(av_file)
        except Exception:
            pass

    # 生成微博卡片快照（使用原作者头像）
    try:
        card_path = dest_dir / "post_card.jpg"
        renderer.render(post, dest_dir, card_path, avatar_override=avatar_override)
    except Exception:
        pass

    await db.upsert_post({
        "post_id": post.post_id, "bid": post.bid, "uid": uid,
        "screen_name": sn, "text_content": post.text_content,
        "text_html": post.text_html, "created_at": post.created_at,
        "source": post.source, "reposts_count": post.reposts_count,
        "comments_count": post.comments_count,
        "attitudes_count": post.attitudes_count,
        "is_pinned": post.is_pinned, "is_repost": post.is_repost,
        "page_url": post.page_url, "region_name": post.region_name,
        "current_content_hash": post.content_hash, "version_count": 1,
        "raw_json": json.dumps(post.raw_card, ensure_ascii=False) if post.raw_card else "{}",
    })

    # 记录媒体到 DB
    for pic in post.pics:
        await db.upsert_media({
            "post_id": post.post_id, "uid": uid,
            "media_type": pic.media_type, "weibo_pid": pic.weibo_pid,
            "original_url": pic.original_url, "local_path": pic.local_path,
            "file_size": pic.file_size, "file_hash": pic.file_hash,
            "width": pic.width, "height": pic.height,
        })
    if post.video and post.video.local_path:
        await db.upsert_media({
            "post_id": post.post_id, "uid": uid,
            "media_type": "video", "weibo_pid": post.video.weibo_pid,
            "original_url": post.video.original_url, "local_path": post.video.local_path,
            "file_size": post.video.file_size, "file_hash": post.video.file_hash,
            "duration": post.video.duration,
        })

    preview = post.text_content[:40].replace('\n', ' ')
    log(f"{prefix}    [{post.post_type_label}] {post.bid} | {preview}...")


def _is_retweet_unavailable(post, retweeted_post) -> bool:
    """判断转发的原博是否不可见.

    两种情况会返回 True:
    1. 原博设置了可见时间范围，已不可见
    2. 原博作者设置了查看权限
    特征: 转发内容仅显示"转发微博"，无实质内容，且原博数据缺失关键字段
    """
    # 转发者只写了"转发微博"（无附加评论）
    if post.retweeted_text and post.retweeted_text.strip() != "转发微博":
        return False  # 转发者加了评论，值得记录

    # 原博不存在或关键信息缺失
    if retweeted_post is None:
        return True

    # 原博无文本内容且无 bid → 不可见
    if not retweeted_post.bid or not retweeted_post.text_content:
        return True

    # 原博文本包含特定提示
    unavailable_markers = [
        "此微博已不可见",
        "暂时没有这条微博的查看权限",
        "已被作者删除",
        "由于作者设置",
    ]
    for marker in unavailable_markers:
        if marker in retweeted_post.text_content:
            return True

    return False


async def _enrich_post(db, fw, layout, renderer, post, uid, sn, client):
    """后处理：完整内容展开 + 编辑检测（文本+图片对比）."""
    if not client or not post.post_id:
        return

    # 1) 展开长文
    if post.is_long_text:
        full_text = await _fetch_full_text(client, post.post_id)
        if full_text and len(full_text) > len(post.text_content):
            post.text_content = full_text
            post.content_hash = post._compute_hash()
            stats["content_expanded"] += 1
            post_dir = _find_post_dir(layout, sn, uid, post)
            if post_dir:
                await fw.write_post(post, post_dir)
                # 长文展开后更新卡片快照
                try:
                    renderer.render(post, post_dir, post_dir / "post_card.jpg")
                except Exception:
                    pass
                await db.conn.execute(
                    "UPDATE posts SET text_content=?, current_content_hash=? WHERE post_id=? AND uid=?",
                    (post.text_content, post.content_hash, post.post_id, uid))
                await db.conn.commit()

    # 2) 显示编辑标记
    if post.edit_count > 0 or post.edited_detected:
        log(f"    [已编辑] edit_count={post.edit_count} | bid={post.bid}")


def _find_post_dir(layout, sn, uid, post):
    """查找博文的磁盘目录（处理序号后缀）."""
    base = layout.posts_root(sn, uid) / _ym(post.created_at)
    dir_name = post.dir_name
    p = base / dir_name
    if p.exists():
        return p
    for c in range(2, 20):
        p = base / f"{dir_name}_{c}"
        if p.exists():
            return p
    return None


async def _save_new_post(db, fw, md, layout, renderer, post, uid, sn, client=None):
    """保存一篇新博文（原创或转发）——先展开全文再保存，避免伪编辑."""
    # ---- 判断是否需要展开全文 ----
    # 三重检测：API 标志 + 清洗后文本 + 原始 HTML
    need_expand = post.is_long_text
    if not need_expand:
        raw_html = post.text_html or ""
        cleaned = post.text_content or ""
        need_expand = (
            cleaned.endswith("...全文") or
            cleaned.endswith("…全文") or
            cleaned.rstrip().endswith("全文") or
            raw_html.endswith("...全文") or
            "全文</" in raw_html or       # 截断标记常见格式: ...<span>全文</span>
            "...全文" in raw_html or
            "…全文" in raw_html
        )
    # ---- 长文先展开（消除保存后再更新造成的伪版本变更） ----
    if need_expand and client and post.post_id:
        log(f"    [展开尝试] bid={post.bid} isLongText={post.is_long_text} (检测到截断，请求全文...)")
        try:
            await _check_rate()
            r = await client.get(
                "https://m.weibo.cn/statuses/extend",
                params={"id": post.post_id},
                headers={"Referer": f"https://m.weibo.cn/detail/{post.post_id}",
                         "X-Requested-With": "XMLHttpRequest"}
            )
            _count_request()
            if r.status_code == 200:
                data = r.json()
                if data.get("ok"):
                    long_text = data["data"].get("longTextContent", "")
                    if long_text and len(long_text) > len(post.text_content):
                        added = len(long_text) - len(post.text_content)
                        post.text_content = long_text
                        post.text_html = long_text  # to_markdown/to_txt 用的是 text_html!
                        post.content_hash = post._compute_hash()
                        log(f"    [展开] bid={post.bid} +{added}字 (保存前)")
                    else:
                        raw_len = len(data.get("data", {}).get("longTextContent", "")) if data.get("data") else 0
                        log(f"    [展开跳过] bid={post.bid} longTextContent={raw_len}字 vs 当前{len(post.text_content)}字 (可能API未返回全文)")
                else:
                    log(f"    [展开失败] bid={post.bid} API ok=0 | msg={data.get('msg','?')}")
            else:
                log(f"    [展开失败] bid={post.bid} HTTP {r.status_code}")
        except Exception as e:
            log(f"    [展开异常] bid={post.bid}: {e}")
    elif need_expand and not client:
        log(f"    [展开跳过] bid={post.bid} client 不可用")

    # ---- 目录 + 媒体 + 写入 ----
    dir_name = post.dir_name
    dt = layout._parse_created_at(post.created_at)
    ym = dt.strftime("%Y/%m") if dt else "unknown"
    base_dir = layout.posts_root(sn, uid) / ym
    post_dir = base_dir / dir_name

    # 冲突处理：同一分钟多条博文时加序号
    counter = 1
    while post_dir.exists():
        counter += 1
        post_dir = base_dir / f"{dir_name}_{counter}"

    layout.ensure_dirs(post_dir, post_dir / "images", post_dir / "videos", post_dir / "versions")

    # Phase 3: 内容类型过滤 — 根据开关控制下载
    if not CONTENT_IMAGES:
        post.pics = []
    if not CONTENT_VIDEOS:
        post.video = None

    try:
        n = await md.download_all(post, post_dir)
        if n:
            img_count = len(post.pics) if CONTENT_IMAGES else 0
            vid_count = 1 if (CONTENT_VIDEOS and post.video and post.video.local_path) else 0
            stats["images"] += img_count
            stats["videos"] += vid_count
    except Exception as e:
        log(f"    [下载异常] bid={post.bid}: {e}")

    await fw.write_post(post, post_dir)

    save_data = {
        "post_id": post.post_id, "bid": post.bid, "uid": uid,
        "screen_name": sn, "text_content": post.text_content,
        "text_html": post.text_html, "created_at": post.created_at,
        "source": post.source, "reposts_count": post.reposts_count,
        "comments_count": post.comments_count,
        "attitudes_count": post.attitudes_count,
        "is_pinned": post.is_pinned, "is_repost": post.is_repost,
        "page_url": post.page_url, "region_name": post.region_name,
        "current_content_hash": post.content_hash, "version_count": 1,
        "raw_json": json.dumps(post.raw_card, ensure_ascii=False) if post.raw_card else "{}",
    }
    await db.upsert_post(save_data)

    # 记录媒体到 DB（与 engine.py _process_new_post 保持一致）
    for pic in post.pics:
        await db.upsert_media({
            "post_id": post.post_id,
            "uid": uid,
            "media_type": pic.media_type,
            "weibo_pid": pic.weibo_pid,
            "original_url": pic.original_url,
            "local_path": pic.local_path,
            "file_size": pic.file_size,
            "file_hash": pic.file_hash,
            "width": pic.width,
            "height": pic.height,
        })
    if post.video and post.video.local_path:
        await db.upsert_media({
            "post_id": post.post_id,
            "uid": uid,
            "media_type": "video",
            "weibo_pid": post.video.weibo_pid,
            "original_url": post.video.original_url,
            "local_path": post.video.local_path,
            "file_size": post.video.file_size,
            "file_hash": post.video.file_hash,
            "duration": post.video.duration,
        })

    label = post.post_type_label
    stats["posts_saved"] += 1
    preview = post.text_content[:50].replace('\n', ' ')

    expand_mark = " [全文]" if post.is_long_text else ""
    log(f"    [{label}] bid={post.bid} | pics={len(post.pics)}{expand_mark} | {preview}...")



async def _save_edited_version(db, fw, renderer, post, uid, sn, existing, layout, client=None, md=None):
    """保存博文的编辑版本——先展开全文再对比."""
    # 检测到的编辑若是长文，先展开再对比（否则 diff 会包含截断标记）
    need_expand = post.is_long_text or post.text_content.rstrip().endswith("全文")
    if need_expand and client and post.post_id:
        try:
            await _check_rate()
            r = await client.get(
                "https://m.weibo.cn/statuses/extend",
                params={"id": post.post_id},
                headers={"Referer": f"https://m.weibo.cn/detail/{post.post_id}",
                         "X-Requested-With": "XMLHttpRequest"}
            )
            _count_request()
            if r.status_code == 200:
                data = r.json()
                if data.get("ok"):
                    long_text = data["data"].get("longTextContent", "")
                    if long_text and len(long_text) > len(post.text_content):
                        added = len(long_text) - len(post.text_content)
                        post.text_content = long_text
                        post.text_html = long_text
                        post.content_hash = post._compute_hash()
                        log(f"    [编辑展开] bid={post.bid} +{added}字")
        except Exception:
            pass

    from weibo_saver.versioning.differ import Differ
    differ = Differ()

    old_text = existing.get("text_content", "")
    diff_text = differ.unified_diff(old_text, post.text_content, "v1", f"v{existing.get('version_count', 1) + 1}")

    # 图片变化检测
    old_media = await db.get_post_media(post.post_id, uid)
    old_pids = {m["weibo_pid"] for m in old_media if m["media_type"] == "image"}
    img_change = _detect_image_changes(old_pids, post.pics)

    has_text_change = bool(diff_text) and differ.has_substantive_change(old_text, post.text_content)
    has_img_change = img_change["changed"]

    if not has_text_change and not has_img_change:
        return False  # 无实质性变更

    new_version = existing.get("version_count", 1) + 1
    diff_summary = differ.diff_summary(diff_text) if diff_text else "仅图片变化"
    if has_img_change:
        diff_summary += f" | 图片: +{len(img_change['added'])} -{len(img_change['removed'])}"

    # 保存版本到数据库
    await db.conn.execute(
        """INSERT INTO post_versions (post_id, uid, version_num, text_content,
           text_html, content_hash, diff_from_prev, captured_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
        (post.post_id, uid, new_version, post.text_content,
         post.text_html, post.content_hash, diff_text),
    )

    # 更新主博文元数据（不覆盖 v1 原始内容，仅更新版本号和哈希）
    await db.conn.execute(
        """UPDATE posts SET
           current_content_hash=?, version_count=?,
           last_updated_at=datetime('now'), last_checked_at=datetime('now')
           WHERE post_id=? AND uid=?""",
        (post.content_hash, new_version, post.post_id, uid),
    )
    await db.conn.commit()

    # 保存新版本到 versions/ 子目录（独立完整的快照）
    dir_name = post.dir_name
    dt = layout._parse_created_at(post.created_at)
    ym = dt.strftime("%Y/%m") if dt else "unknown"
    post_dir = layout.posts_root(sn, uid) / ym / dir_name
    versions_dir = post_dir / "versions"
    v_dir = versions_dir / f"v{new_version}"
    v_dir.mkdir(parents=True, exist_ok=True)

    import json, shutil

    # ---- 图片：直接下载到 v{N}/images/（与初始抓取一致，不复制） ----
    if post.pics and md:
        v_images = v_dir / "images"
        v_images.mkdir(exist_ok=True)
        try:
            await md.download_all_images(post, v_dir)
        except Exception:
            pass

    # ---- 视频：直接下载到 v{N}/videos/ ----
    if post.video and md:
        v_videos = v_dir / "videos"
        v_videos.mkdir(exist_ok=True)
        try:
            await md.download_post_video(post, v_dir)
        except Exception:
            pass

    # ---- 保存文本文件 ----
    (v_dir / "post.json").write_text(json.dumps(post.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    (v_dir / "post.md").write_text(post.to_markdown(), encoding="utf-8")
    (v_dir / "post.txt").write_text(post.to_txt(), encoding="utf-8")
    (v_dir / f"diff_v{new_version}.patch").write_text(diff_text, encoding="utf-8")

    # 生成版本卡片快照
    try:
        renderer.render(post, v_dir, v_dir / "post_card.jpg", is_version=True, version_num=new_version)
    except Exception:
        pass

    # 不覆盖主博文文件（保持 v1 原始内容不变）
    preview = post.text_content[:40].replace('\n', ' ')
    log(f"    [编辑] bid={post.bid} v{existing.get('version_count',1)}→v{new_version} | {diff_summary} | {preview}...")
    stats["edited"] += 1
    return True


async def _restore_if_deleted(db, layout, post, uid, sn):
    """博文重新出现在时间线上：恢复 DB 标记 + 目录名，返回现有记录."""
    existing = await db.get_post(post.post_id, uid)
    if not existing:
        return existing
    if existing.get("is_deleted") or existing.get("visibility_hidden"):
        await db.conn.execute(
            "UPDATE posts SET is_deleted=0, visibility_hidden=0 WHERE post_id=? AND uid=?",
            (post.post_id, uid))
        await db.conn.commit()
        # 恢复目录名（使用 post.dir_name 匹配 _save_new_post 创建的目录命名）
        created_at = existing.get("created_at", "")
        dt = layout._parse_created_at(created_at)
        ym = dt.strftime("%Y/%m") if dt else "unknown"
        post_dir = layout.posts_root(sn, uid) / ym / post.dir_name
        renamed_dir = post_dir.parent / (post_dir.name + "已删除或隐藏")
        if renamed_dir.exists():
            try:
                renamed_dir.rename(post_dir)
            except Exception:
                pass
            for f in post_dir.glob("对象在*已将此博文删除或隐藏.txt"):
                try: f.unlink()
                except Exception: pass
        log(f"    [恢复] bid={post.bid} 已从删除/隐藏状态恢复")
    return existing


async def _detect_deletions(db, layout, uid, sn, seen_bids, vis_result) -> int:
    """检测对象已删除的博文.

    排除因可见时间限制而隐藏的博文（半年/一年），只标记真正被删的.
    """
    deleted = 0
    all_bids = await db.get_all_bids(uid)
    missing = all_bids - seen_bids
    if not missing:
        return 0

    # 从可见性检测结果获取「可见范围内最早日期」
    earliest_visible = vis_result.get("earliest_post_date", "")
    visibility_limit = vis_result.get("limit", "none")

    now_str = datetime.now().strftime("%Y_%m_%d_%H_%M")
    for bid in missing:
        post = await db.get_post_by_bid(bid, uid)
        if not post or post.get("is_deleted") or post.get("visibility_hidden"):
            continue

        created_at = post.get("created_at", "")
        # 排除因可见时间限制而隐藏的博文
        if visibility_limit in ("six_months", "one_year", "custom") and earliest_visible:
            # 如果博文发布时间早于最早可见日期 → 是被隐藏的，不标记删除
            from weibo_saver.storage.layout import Layout
            dt = Layout._parse_created_at(created_at)
            ev_dt = None
            try:
                ev_dt = datetime.strptime(earliest_visible[:10], "%Y-%m-%d")
            except Exception:
                pass
            if dt and ev_dt and dt < ev_dt:
                # 标记为隐藏而非删除
                await db.conn.execute(
                    "UPDATE posts SET visibility_hidden=1 WHERE post_id=? AND uid=?",
                    (post["post_id"], uid))
                await db.conn.commit()
                continue

        # 确认删除
        await db.conn.execute(
            "UPDATE posts SET is_deleted=1, last_checked_at=datetime('now') WHERE post_id=? AND uid=?",
            (post["post_id"], uid))
        await db.conn.commit()
        # 重命名目录 + 创建时间戳文件
        # 目录命名有两种可能：BID 或 {日期}_{类型}，逐个尝试
        actual_dir = None
        candidates = [
            layout.post_dir(sn, uid, created_at, bid),  # BID 命名
        ]
        # 也尝试 {日期}_{类型} 命名
        try:
            from weibo_saver.models.post import Post
            dt = layout._parse_created_at(created_at)
            if dt:
                date_prefix = dt.strftime("%Y-%m-%d_%H-%M")
                type_label = "转发" if post.get("is_repost") else ("置顶" if post.get("is_pinned") else "原创")
                ym = dt.strftime("%Y/%m")
                alt_dir = layout.posts_root(sn, uid) / ym / f"{date_prefix}_{type_label}"
                candidates.append(alt_dir)
                # 也可能有后缀（冲突序号）
                for c in range(2, 20):
                    candidates.append(layout.posts_root(sn, uid) / ym / f"{date_prefix}_{type_label}_{c}")
        except Exception:
            pass
        for d in candidates:
            if d.exists():
                actual_dir = d
                break
        if actual_dir:
            marker = actual_dir / f"对象在{now_str}已将此博文删除或隐藏.txt"
            marker.write_text(
                f"监测器于 {now_str.replace('_', '-')} 检测到此博文已被删除或隐藏。\n"
                f"post_id: {post['post_id']}\nbid: {bid}\n",
                encoding="utf-8")
            new_dir = actual_dir.parent / (actual_dir.name + "已删除或隐藏")
            if not new_dir.exists():
                try:
                    actual_dir.rename(new_dir)
                except Exception:
                    pass
        deleted += 1
        log(f"    [已删除] bid={bid} | 标记于 {now_str}")
    return deleted


async def main():
    stats["start_time"] = time.monotonic()

    log("=" * 50)
    log(f"Weibo Saver 全量抓取 - UID={TARGET_UID}")
    log("=" * 50)

    # 初始化组件
    from weibo_saver.models.post import Post
    from weibo_saver.storage.layout import Layout
    from weibo_saver.storage.file_writer import FileWriter
    from weibo_saver.storage.database import Database
    from weibo_saver.core.media_downloader import MediaDownloader
    from weibo_saver.core.card_renderer import CardRenderer
    from weibo_saver.config import DownloadConfig

    db = Database(ARCHIVE_ROOT / "db" / "weibo_saver.db")
    await db.init()
    layout = Layout(ARCHIVE_ROOT)
    fw = FileWriter(save_json=True, save_md=True, save_txt=True)

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
        # 设置基础 Cookie
        for item in COOKIE_STRING.split(";"):
            item = item.strip()
            if "=" in item:
                k, v = item.split("=", 1)
                client.cookies.set(k.strip(), v.strip(), domain=".weibo.cn")

        client.headers.update({
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15",
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
        })

        # ====== Step 1: 获取 XSRF-TOKEN + 验证登录 ======
        log("[1] 验证登录 + 获取 XSRF-TOKEN...")
        await _check_rate()
        r = await client.get("https://m.weibo.cn/api/config")
        _count_request()
        data = r.json()
        if not data.get("data", {}).get("login", False):
            log("[FAIL] Cookie 已过期! SUB 可能失效，请重新提供")
            return 1
        st = data.get("data", {}).get("st", "")
        client.headers["X-XSRF-TOKEN"] = st
        log(f"  [OK] 登录有效 | st={st}")

        # ====== Step 2: 用户信息（先尝试专用API，失败则从时间线提取）======
        log("[2] 获取用户信息...")
        ui = {}
        sn = f"user_{TARGET_UID}"
        for attempt in range(2):
            await _check_rate()
            r = await client.get(
                "https://m.weibo.cn/api/container/getIndex",
                params={"type": "uid", "value": TARGET_UID, "containerid": f"100505{TARGET_UID}"},
                headers={"Referer": f"https://m.weibo.cn/u/{TARGET_UID}"}
            )
            _count_request()
            data = r.json()
            ui = data.get("data", {}).get("userInfo", {})
            if ui:
                break
            if attempt == 0:
                await asyncio.sleep(2)

        if not ui:
            # 降级：从时间线第一页提取用户名
            log("    用户API失败，从时间线提取用户名...")
            await _check_rate()
            r = await client.get(
                "https://m.weibo.cn/api/container/getIndex",
                params={"type": "uid", "value": TARGET_UID, "containerid": f"107603{TARGET_UID}", "page": "1"},
                headers={"Referer": f"https://m.weibo.cn/u/{TARGET_UID}"}
            )
            _count_request()
            data = r.json()
            cards = data.get("data", {}).get("cards", [])
            for card in cards:
                mblog = card.get("mblog", card)
                user_info = mblog.get("user", {})
                if user_info.get("screen_name"):
                    sn = user_info.get("screen_name", sn)
                    ui = {"screen_name": sn, "id": TARGET_UID}
                    break
            # 用户API和时间线都取不到 → 账号可能已注销
            if not ui or not ui.get("screen_name"):
                log(f"  [DEACTIVATED] 账号 {TARGET_UID} 疑似已注销或不可访问")
                return 1
        else:
            sn = ui.get("screen_name", sn)

        sn = ui.get("screen_name", "unknown")
        log(f"  [OK] {sn} | 粉丝:{ui.get('followers_count','?')} | 微博:{ui.get('statuses_count','?')}")

        await fw.write_user_profile(ui, layout.user_dir(sn, TARGET_UID))
        await db.upsert_user(TARGET_UID, sn, description=ui.get("description",""),
            followers_count=0, statuses_count=0, raw_json=json.dumps(ui, ensure_ascii=False))

        # ---- 下载头像（sinaimg CDN 无需 Cookie）----
        avatar_path = ""
        user_dir = layout.user_dir(sn, TARGET_UID)
        avatar_url = ui.get("avatar_hd", ui.get("avatar_large", ui.get("profile_image_url", "")))
        if avatar_url:
            try:
                # sinaimg.cn 是 CDN，不需要验证，但 httpx 可能不带 cookie 而被拒
                r_av = await client.get(avatar_url, timeout=15.0,
                    headers={"Referer": "https://m.weibo.cn/"})
                if r_av.status_code == 200 and len(r_av.content) > 100:
                    avatar_file = user_dir / "avatar.jpg"
                    avatar_file.write_bytes(r_av.content)
                    avatar_path = str(avatar_file)
                    log(f"    头像已下载: {len(r_av.content)//1024}KB")
                else:
                    log(f"    头像下载失败: HTTP {r_av.status_code} size={len(r_av.content)}")
            except Exception as e:
                log(f"    头像下载异常: {e}")

        # ====== Step 3: 全量爬取 ======
        log("[3] 开始逐页抓取...")
        md = MediaDownloader(DownloadConfig(max_concurrent_downloads=2), client)
        renderer = CardRenderer(http_client=client, avatar_path=avatar_path, target_uid=TARGET_UID)
        log("    卡片渲染器已就绪")
        page = 1
        consecutive_empty = 0
        consecutive_no_new = 0
        known_bids = set()
        seen_bids = set()    # 本轮时间线出现的所有 BID（用于删除检测）

        cards_processed = 0
        api_anomalies = 0  # #7: API 结构异常计数
        while True:
            # 达到卡片上限
            # MAX_CARDS=0 表示不设上限，抓取全部可见博文
            if MAX_CARDS > 0 and cards_processed >= MAX_CARDS:
                log(f"  [OK] 已达{MAX_CARDS}张卡片上限，抓取完成")
                break
            # 连续5页无新博文则停止
            if consecutive_no_new >= 5:
                log(f"  [OK] 连续{consecutive_no_new}页无新博文，抓取完成")
                break
            # 页间延迟 + 速率限制
            delay = random.uniform(MIN_DELAY, MAX_DELAY)
            await asyncio.sleep(delay)
            await _check_rate()

            try:
                r = await _safe_get(client,
                    "https://m.weibo.cn/api/container/getIndex",
                    params={"type": "uid", "value": TARGET_UID, "containerid": f"107603{TARGET_UID}", "page": str(page)},
                    headers={"Referer": f"https://m.weibo.cn/u/{TARGET_UID}"}
                )
            except CookieExpiredMidCrawl:
                log(f"  [COOKIE_EXPIRED] Cookie 在爬取中途失效，中止")
                break
            _count_request()

            if r.status_code == 432:
                log(f"  [WARN] 第{page}页 432限流，等待45秒...")
                await asyncio.sleep(45)
                # 刷新 XSRF-TOKEN
                await _check_rate()
                r2 = await client.get("https://m.weibo.cn/api/config")
                _count_request()
                new_st = r2.json().get("data", {}).get("st", "")
                if new_st:
                    client.headers["X-XSRF-TOKEN"] = new_st
                continue

            if r.status_code != 200:
                log(f"  [WARN] 第{page}页 HTTP {r.status_code}，等待10秒...")
                await asyncio.sleep(10)
                continue

            try:
                data = r.json()
            except Exception:
                log(f"  [WARN] 第{page}页非JSON响应，等待30秒...")
                await asyncio.sleep(30)
                continue

            cards = data.get("data", {}).get("cards", [])
            stats["pages"] += 1

            if not cards:
                consecutive_empty += 1
                # 第一页就为空 + 用户信息存在 → 可能是私密账号
                if page == 1 and consecutive_empty == 1 and sn and sn != f"user_{TARGET_UID}":
                    log(f"  [PRIVATE] 用户 {sn}({TARGET_UID}) 首页为空，可能为私密账号或全部博文已隐藏")
                if consecutive_empty >= 3:
                    log(f"  [OK] 连续{consecutive_empty}页为空，完成")
                    break
                page += 1
                continue

            consecutive_empty = 0
            page_new = 0
            page_anomalies = 0  # 本页空卡片数（仅本页有新帖时才计入 api_anomalies）

            for card in cards:
                post = Post.from_api_card(card)
                if post is None:
                    stats["reposts_skipped"] += 1
                    continue

                # 计数空内容卡片（可能 API 结构变更；有 page_info 的话题/文章卡不算空）
                pi = getattr(post, 'page_info', None) or {}
                if not post.text_content and not post.pics and not post.video and not pi:
                    page_anomalies += 1

                if post.bid:
                    seen_bids.add(post.bid)

                if not post.bid or not post.post_id:
                    continue

                # 处理转发（原博存入 resource/ 子目录）
                if post.is_repost:
                    retweeted = post.retweeted_post
                    if _is_retweet_unavailable(post, retweeted):
                        stats["reposts_skipped"] += 1
                        continue

                    # 有效转发博文计入上限（原创算一条，转发也算一条）
                    cards_processed += 1
                    if MAX_CARDS > 0 and cards_processed > MAX_CARDS:
                        break

                    # 展开长文再对比（防截断vs完整误判）
                    if (post.is_long_text or post.text_content.rstrip().endswith("全文")) and client:
                        try:
                            await _check_rate()
                            r = await client.get(
                                "https://m.weibo.cn/statuses/extend",
                                params={"id": post.post_id},
                                headers={"Referer": f"https://m.weibo.cn/detail/{post.post_id}",
                                         "X-Requested-With": "XMLHttpRequest"}
                            )
                            _count_request()
                            if r.status_code == 200:
                                data = r.json()
                                if data.get("ok"):
                                    lt = data["data"].get("longTextContent", "")
                                    if lt and len(lt) > len(post.text_content):
                                        post.text_content = lt
                                        post.text_html = lt
                                        post.content_hash = post._compute_hash()
                        except Exception:
                            pass

                    # 检查转发是否已存（含恢复逻辑）
                    existing = await _restore_if_deleted(db, layout, post, TARGET_UID, sn)
                    if existing:
                        stored_hash = existing.get("current_content_hash", "")
                        if stored_hash and stored_hash != post.content_hash:
                            if await _save_edited_version(db, fw, renderer, post, TARGET_UID, sn, existing, layout, client, md):
                                stats["posts_saved"] += 1
                                page_new += 1
                        else:
                            stats["posts_skipped"] += 1
                        continue

                    await _save_new_post(db, fw, md, layout, renderer, post, TARGET_UID, sn, client)
                    await _enrich_post(db, fw, layout, renderer, post, TARGET_UID, sn, client)
                    page_new += 1

                    # 原博存入转发目录下的 resource/ 子目录
                    if retweeted and retweeted.bid:
                        repost_dir = layout.posts_root(sn, TARGET_UID) / _ym(post.created_at) / post.dir_name

                        # 如果原博是目标用户自己发的，也保存为独立原创博文（避免遗漏自转发）
                        if str(retweeted.uid) == str(TARGET_UID):
                            existing_orig = await db.get_post(retweeted.post_id, TARGET_UID)
                            if not existing_orig:
                                await _save_new_post(db, fw, md, layout, renderer, retweeted, TARGET_UID,
                                                     retweeted.screen_name or sn, client)
                                # 自转独立原创卡片快照
                                try:
                                    rt_standalone_dir = layout.posts_root(
                                        retweeted.screen_name or sn, TARGET_UID) / _ym(retweeted.created_at) / retweeted.dir_name
                                    renderer.render(retweeted, rt_standalone_dir,
                                                    rt_standalone_dir / "post_card.jpg")
                                except Exception:
                                    pass
                                log(f"  [自转] 原博 {retweeted.bid} 同时保存为独立原创")

                        # 下载原博媒体到 resource/ + 保存（此时 retweeted.pics 的 local_path 指向 resource/）
                        res_dir = repost_dir / "resource"
                        await _save_post_to_dir(db, fw, md, renderer, retweeted, res_dir, retweeted.uid or TARGET_UID,
                                               retweeted.screen_name or sn, prefix="  [原博]", client=client)

                        # 渲染转发卡片快照（原博媒体已就位，缩略图可见）
                        try:
                            renderer.render(post, repost_dir, repost_dir / "post_card.jpg")
                        except Exception:
                            pass
                    known_bids.add(post.bid)
                    continue

                if post.bid in known_bids:
                    continue
                known_bids.add(post.bid)

                # 有效原创博文计入上限（去重之后再计，避免重复消耗配额）
                cards_processed += 1
                if MAX_CARDS > 0 and cards_processed > MAX_CARDS:
                    break

                # 展开长文再对比哈希（否则截断文本 ≠ 数据库中已保存的全文，每轮监测都会误判为编辑）
                if (post.is_long_text or post.text_content.rstrip().endswith("全文")) and client:
                    try:
                        await _check_rate()
                        r = await client.get(
                            "https://m.weibo.cn/statuses/extend",
                            params={"id": post.post_id},
                            headers={"Referer": f"https://m.weibo.cn/detail/{post.post_id}",
                                     "X-Requested-With": "XMLHttpRequest"}
                        )
                        _count_request()
                        if r.status_code == 200:
                            data = r.json()
                            if data.get("ok"):
                                lt = data["data"].get("longTextContent", "")
                                if lt and len(lt) > len(post.text_content):
                                    post.text_content = lt
                                    post.text_html = lt
                                    post.content_hash = post._compute_hash()
                    except Exception:
                        pass

                # 检查是否已存在（含恢复逻辑）
                existing = await _restore_if_deleted(db, layout, post, TARGET_UID, sn)

                if existing:
                    stored_hash = existing.get("current_content_hash", "")
                    if stored_hash and stored_hash != post.content_hash:
                        if await _save_edited_version(db, fw, renderer, post, TARGET_UID, sn, existing, layout, client, md):
                            stats["posts_saved"] += 1
                            page_new += 1
                    else:
                        stats["posts_skipped"] += 1
                    continue

                # 新博文（_save_new_post 内部已 stats["posts_saved"] += 1）
                await _save_new_post(db, fw, md, layout, renderer, post, TARGET_UID, sn, client)
                await _enrich_post(db, fw, layout, renderer, post, TARGET_UID, sn, client)
                page_new += 1

                # 渲染原创博文卡片快照
                try:
                    orig_dir = layout.posts_root(sn, TARGET_UID) / _ym(post.created_at) / post.dir_name
                    renderer.render(post, orig_dir, orig_dir / "post_card.jpg")
                except Exception:
                    pass

            # 进度
            elapsed = time.monotonic() - stats["start_time"]
            eta = ""
            if stats["posts_saved"] > 0:
                rate = stats["posts_saved"] / (elapsed / 60) if elapsed > 60 else 0
                eta = f" | 速度:{rate:.0f}条/分钟"
            # 跟踪连续无新增
            if page_new == 0:
                consecutive_no_new += 1
            else:
                consecutive_no_new = 0

            # 仅本页有新帖时，空卡片才算 API 异常（无新增时空白卡片属正常）
            if page_new > 0:
                api_anomalies += page_anomalies

            log(f"  第{page:3d}页 | 卡片{len(cards):2d} +{page_new}新"
                f" | 累计{stats['posts_saved']:4d}条 | {elapsed:.0f}s{eta}")

            page += 1

        # ====== Phase 8: 补采（补充下载之前缺失的媒体类型）======
        if WEIBO_BACKFILL:
            missing_types = [t.strip() for t in WEIBO_BACKFILL_TYPES.split(",") if t.strip()]
            if missing_types:
                log(f"\n[3b] 补采模式: 补充 {missing_types} (最多 {WEIBO_BACKFILL_MAX_POSTS} 条)...")
                backfilled = await _backfill_media(db, fw, md, layout, renderer, TARGET_UID, sn, client, missing_types)
                log(f"  补采完成: 补充下载 {backfilled} 个媒体文件")
                stats["images"] += backfilled

        # ====== 完成 + 可见性检测 ======
        elapsed = time.monotonic() - stats["start_time"]
        await db.update_crawl_state(
            uid=TARGET_UID, is_first_crawl_complete=1,
            total_posts_crawled=stats["posts_saved"],
            last_page_crawled=page - 1,
            last_full_crawl_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        )

        # 可见性检测（在删除检测之前，用于排除隐藏博文）
        log(f"\n[4] 可见性检测...")
        from weibo_saver.core.visibility_detector import VisibilityDetector
        vis = VisibilityDetector(db)
        vis_result = await vis.detect_after_full_crawl(
            TARGET_UID, stats["posts_saved"], ""
        )
        limit_label = VisibilityDetector.format_limit(vis_result["limit"], vis_result.get("days_visible", 0))
        earliest = vis_result.get("earliest_post_date", "?")[:10]
        days = vis_result.get("days_visible", 0)
        log(f"  检测结果: {limit_label}")
        log(f"  最早博文: {earliest} (距今 {days} 天)")

        # ====== 删除检测（排除可见范围外的隐藏博文）======
        log(f"\n[5] 删除检测...")
        deleted_count = await _detect_deletions(db, layout, TARGET_UID, sn, seen_bids, vis_result)
        if deleted_count:
            log(f"  检测到 {deleted_count} 条已删除博文")

        log(f"\n[6] 爬取完成!")
        log(f"  {'─' * 40}")
        log(f"  页数: {stats['pages']} | 新增: {stats['posts_saved'] - stats.get('edited', 0)} | 编辑: {stats.get('edited', 0)} | 跳过: {stats['posts_skipped']}")
        log(f"  转发过滤: {stats['reposts_skipped']} | 媒体下载: {stats['images']}图 {stats['videos']}视频")
        # 仅在本页有新增时才报告 API 异常（无新增时空白卡片属正常）
        if api_anomalies >= 1 and stats["posts_saved"] > 0:
            log(f"  [WARN] API 结构异常: {api_anomalies} 张卡片内容为空（可能 API 变更）")
        if _rate_limit_hit:
            log(f"  [限流] 本次抓取触发了 API 频率限制，已自动等待恢复")
        log(f"  内容展开: {stats.get('content_expanded', 0)} | 编辑检测: {stats.get('edited', 0)}")
        log(f"  错误: {len(stats['errors'])} | 耗时: {elapsed/60:.1f}分钟")
        for e in stats["errors"][:5]:
            log(f"    - {e}")

        await db.close()

    # 统计文件（排除 versions/ 和 resource/ 子目录，只计正文）
    user_dir = layout.user_dir(sn, TARGET_UID)
    post_count = sum(
        1 for p in user_dir.rglob("post.json")
        if "/versions/" not in str(p) and "\\versions\\" not in str(p)
        and "/resource/" not in str(p) and "\\resource\\" not in str(p)
    )
    total_size = sum(_.stat().st_size for _ in user_dir.rglob("*") if _.is_file())
    log(f"\n[7] 存档: {post_count}篇博文 | {total_size/1024/1024:.1f}MB")
    log(f"  路径: {user_dir}")
    log(f"  [计数] 最终: 已用 {_request_count}/{HOURLY_LIMIT} 次")
    log(f"{'=' * 50}")
    log("全量抓取完成!")
    return 0


async def _backfill_media(db, fw, md, layout, renderer, uid, sn, client, missing_types: list[str]) -> int:
    """补采：对已有博文补充下载之前缺失的媒体类型（含 resource/ 中的转发原博）.

    Args:
        missing_types: 需要补充的类型列表，如 ["images", "videos"]

    Returns:
        成功补充下载的媒体文件数
    """
    # 查询所有与该用户相关的博文（含 resource/ 子目录中的转发原博，它们可能有不同 UID）
    posts = await db.conn.execute_fetchall(
        "SELECT post_id, bid, uid, screen_name, created_at, raw_json FROM posts "
        "WHERE is_deleted=0 AND (uid=? OR post_id IN "
        "  (SELECT DISTINCT post_id FROM posts WHERE uid=? AND is_repost=1)) "
        "ORDER BY created_at DESC LIMIT ?",
        (uid, uid, WEIBO_BACKFILL_MAX_POSTS),
    )
    if not posts:
        # 降级：仅查当前 UID
        posts = await db.conn.execute_fetchall(
            "SELECT post_id, bid, uid, screen_name, created_at, raw_json FROM posts "
            "WHERE uid=? AND is_deleted=0 ORDER BY created_at DESC LIMIT ?",
            (uid, WEIBO_BACKFILL_MAX_POSTS),
        )
    downloaded = 0
    for row in posts:
        post_id, bid, row_uid, row_sn, created_at, raw_json_str = row
        # 使用博文自己的 UID 查询媒体
        existing_media = await db.get_post_media(post_id, row_uid)
        existing_types = {m["media_type"] for m in existing_media}

        need_download = [t for t in missing_types
                         if t not in existing_types and t != "text"]
        if not need_download:
            continue

        # 从 raw_json 重建 post 以获取媒体 URL
        if not raw_json_str:
            # 若无 raw_json，尝试从 API 重新获取
            try:
                await _check_rate()
                r = await client.get(
                    "https://m.weibo.cn/statuses/extend",
                    params={"id": post_id},
                    headers={"Referer": f"https://m.weibo.cn/detail/{post_id}",
                             "X-Requested-With": "XMLHttpRequest"}
                )
                _count_request()
                if r.status_code != 200:
                    continue
                card_data = r.json()
                if not card_data.get("ok"):
                    continue
                raw_json_str = json.dumps(card_data.get("data", {}), ensure_ascii=False)
            except Exception:
                continue

        try:
            card = json.loads(raw_json_str) if isinstance(raw_json_str, str) else raw_json_str
        except (json.JSONDecodeError, TypeError):
            continue

        from weibo_saver.models.post import Post
        post_obj = Post.from_api_card(card if isinstance(card, dict) else {"mblog": card})
        if post_obj is None:
            continue

        # 只下载缺失的类型（保留旧媒体引用，下载后合并以渲染完整卡片）
        saved_pics = list(post_obj.pics)
        saved_video = post_obj.video
        if "images" not in need_download:
            post_obj.pics = []
        if "videos" not in need_download:
            post_obj.video = None

        # 找现有 post 目录（含 resource/ 子目录中的转发原博）
        dt = layout._parse_created_at(created_at)
        ym = dt.strftime("%Y/%m") if dt else "unknown"
        base_dir = layout.posts_root(sn, uid) / ym
        post_dir = base_dir / post_obj.dir_name
        if not post_dir.exists():
            # 尝试加序号
            found = False
            for c in range(2, 20):
                alt = base_dir / f"{post_obj.dir_name}_{c}"
                if alt.exists():
                    post_dir = alt
                    found = True
                    break
            # 尝试 resource/ 子目录（转发原博存储位置）
            if not found:
                for sub in base_dir.iterdir():
                    res_candidate = sub / "resource"
                    if res_candidate.exists() and res_candidate.is_dir():
                        # 检查 resource/ 中是否有此 post_id 的文件
                        if (res_candidate / "post.json").exists():
                            try:
                                j = json.loads((res_candidate / "post.json").read_text(encoding="utf-8"))
                                if j.get("bid") == bid or j.get("post_id") == post_id:
                                    post_dir = res_candidate
                                    found = True
                                    break
                            except Exception:
                                pass
            if not found:
                continue

        layout.ensure_dirs(post_dir, post_dir / "images", post_dir / "videos")
        try:
            n = await md.download_all(post_obj, post_dir)
            downloaded += n
            if n > 0:
                log(f"    [补采] bid={bid} +{n}媒体 {need_download}")
                # 合并新旧媒体（download_all 已原地更新 MediaItem，saved_pics 已有完整 local_path）
                post_obj.pics = saved_pics
                post_obj.video = post_obj.video or saved_video
                try:
                    renderer.render(post_obj, post_dir, post_dir / "post_card.jpg")
                except Exception:
                    pass
        except Exception as e:
            log(f"    [补采失败] bid={bid}: {e}")

        # 防止过于频繁
        await asyncio.sleep(random.uniform(0.5, 1.5))

    return downloaded


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
