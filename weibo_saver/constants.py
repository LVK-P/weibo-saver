"""全局常量、SQL DDL、默认配置值."""

# ============================================================
# 微博 API 端点
# ============================================================
WEIBO_MOBILE_BASE = "https://m.weibo.cn"
WEIBO_API_CONFIG = "/api/config"
WEIBO_API_TIMELINE = "/api/container/getIndex"
WEIBO_API_POST_DETAIL = "/statuses/extend"
WEIBO_API_LONG_TEXT = "/statuses/extend"
WEIBO_API_USER_INFO = "/api/container/getIndex"

# Cookie 相关域名
WEIBO_COOKIE_DOMAINS = [".weibo.cn", ".weibo.com", ".sinaimg.cn"]

# ============================================================
# 请求头伪装
# ============================================================
MOBILE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/18.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://m.weibo.cn/",
    "Origin": "https://m.weibo.cn",
    "Connection": "keep-alive",
}

# ============================================================
# 速率限制默认值
# ============================================================
RATE_LIMIT_MIN_DELAY = 1.5       # 最小请求间隔（秒）
RATE_LIMIT_MAX_DELAY = 4.5       # 最大请求间隔（秒）
RATE_LIMIT_MAX_PER_MINUTE = 15   # 每分钟最大请求数
RATE_LIMIT_BACKOFF_432 = 45.0    # 432 错误退避时间（秒）

# ============================================================
# 爬取相关
# ============================================================
CARD_TYPE_POST = 9        # 普通博文
CARD_TYPE_REPOST = 1      # 转发博文
CARD_TYPE_VIDEO = 10      # 视频/故事卡片
CARD_TYPE_SYSTEM = 11     # 系统/广告卡片

# ============================================================
# 图片 CDN
# ============================================================
SINAIMG_CDN_HOSTS = [
    "wx1.sinaimg.cn",
    "wx2.sinaimg.cn",
    "wx3.sinaimg.cn",
    "wx4.sinaimg.cn",
]

# ============================================================
# 文件模板
# ============================================================
POST_DIR_TEMPLATE = "{year}/{month}/{bid}"

# ============================================================
# 默认配置
# ============================================================
DEFAULT_CONFIG = {
    "archive_root": "",  # 运行时动态设置
    "browser": "chrome",
    "target_uids": [],
    "monitor": {
        "enabled": True,
        "interval_seconds": 60,
        "quiet_hours_enabled": False,
        "quiet_hours_start": "23:00",
        "quiet_hours_end": "07:00",
    },
    "rate_limit": {
        "min_delay_seconds": 1.5,
        "max_delay_seconds": 4.5,
        "max_requests_per_minute": 15,
    },
    "download": {
        "images": True,
        "videos": True,
        "gifs": True,
        "max_video_size_mb": 500,
        "max_concurrent_downloads": 3,
        "image_quality": "original",
    },
    "output": {
        "save_json": True,
        "save_markdown": True,
        "save_txt": True,
        "save_raw_response": False,
    },
    "retry": {
        "max_retries": 5,
        "backoff_base_seconds": 2.0,
        "backoff_max_seconds": 120.0,
    },
    "proxy": {
        "enabled": False,
        "proxies": [],
        "strategy": "weighted",
        "health_check_interval": 300.0,
        "max_consecutive_fails": 3,
        "min_alive_proxies": 2,
        "auto_fetch_free": False,
        "proxy_sources": [],
    },
    "logging": {
        "level": "INFO",
        "max_log_files": 30,
        "max_log_size_mb": 10,
    },
}

# ============================================================
# SQL DDL
# ============================================================
SQL_CREATE_TABLES = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;
PRAGMA cache_size=-32000;

CREATE TABLE IF NOT EXISTS users (
    uid              TEXT PRIMARY KEY,
    screen_name      TEXT NOT NULL,
    description      TEXT,
    profile_url      TEXT,
    avatar_url       TEXT,
    followers_count  INTEGER DEFAULT 0,
    friends_count    INTEGER DEFAULT 0,
    statuses_count   INTEGER DEFAULT 0,
    raw_json         TEXT,
    first_seen_at    TEXT NOT NULL,
    last_updated_at  TEXT NOT NULL,
    visibility_limit      TEXT,               -- 'none'/'six_months'/'one_year'/'unknown'
    earliest_visible_post_at TEXT,            -- 最早可见博文的时间
    visibility_checked_at TEXT               -- 上次检查时间
);

CREATE TABLE IF NOT EXISTS posts (
    post_id              TEXT NOT NULL,
    bid                   TEXT NOT NULL,
    uid                   TEXT NOT NULL,
    screen_name          TEXT NOT NULL,
    text_content         TEXT NOT NULL,
    text_html            TEXT,
    created_at           TEXT NOT NULL,
    source               TEXT,
    reposts_count        INTEGER NOT NULL DEFAULT 0,
    comments_count       INTEGER NOT NULL DEFAULT 0,
    attitudes_count      INTEGER NOT NULL DEFAULT 0,
    is_pinned            INTEGER NOT NULL DEFAULT 0,
    is_repost            INTEGER NOT NULL DEFAULT 0,
    page_url             TEXT,
    region_name          TEXT,
    current_content_hash TEXT NOT NULL,
    version_count        INTEGER NOT NULL DEFAULT 1,
    first_seen_at        TEXT NOT NULL,
    last_updated_at      TEXT NOT NULL,
    last_checked_at      TEXT NOT NULL,
    is_deleted           INTEGER NOT NULL DEFAULT 0,
    visibility_hidden    INTEGER NOT NULL DEFAULT 0,  -- 因可见时间限制被隐藏
    raw_json             TEXT,
    PRIMARY KEY (post_id, uid)
);

CREATE INDEX IF NOT EXISTS idx_posts_uid_created ON posts(uid, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_posts_bid ON posts(bid);
CREATE INDEX IF NOT EXISTS idx_posts_hash ON posts(uid, current_content_hash);

CREATE TABLE IF NOT EXISTS post_versions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id         TEXT NOT NULL,
    uid             TEXT NOT NULL,
    version_num     INTEGER NOT NULL,
    text_content    TEXT NOT NULL,
    text_html       TEXT,
    content_hash    TEXT NOT NULL,
    diff_from_prev  TEXT,
    captured_at     TEXT NOT NULL,
    FOREIGN KEY (post_id, uid) REFERENCES posts(post_id, uid),
    UNIQUE(post_id, uid, version_num)
);

CREATE INDEX IF NOT EXISTS idx_versions_post ON post_versions(post_id, uid);

CREATE TABLE IF NOT EXISTS media (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id         TEXT NOT NULL,
    uid             TEXT NOT NULL,
    media_type      TEXT NOT NULL CHECK (media_type IN ('image', 'video', 'gif')),
    weibo_pid       TEXT,
    original_url    TEXT NOT NULL,
    local_path      TEXT NOT NULL,
    file_size       INTEGER,
    file_hash       TEXT,
    width           INTEGER,
    height          INTEGER,
    duration        REAL,
    downloaded_at   TEXT NOT NULL,
    download_status TEXT NOT NULL DEFAULT 'complete',
    retry_count     INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (post_id, uid) REFERENCES posts(post_id, uid),
    UNIQUE(post_id, weibo_pid)
);

CREATE INDEX IF NOT EXISTS idx_media_post ON media(post_id, uid);

CREATE TABLE IF NOT EXISTS crawl_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    uid             TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    crawl_type      TEXT NOT NULL CHECK (crawl_type IN ('full', 'incremental', 'monitor_check')),
    pages_crawled   INTEGER NOT NULL DEFAULT 0,
    posts_seen      INTEGER NOT NULL DEFAULT 0,
    new_posts       INTEGER NOT NULL DEFAULT 0,
    updated_posts   INTEGER NOT NULL DEFAULT 0,
    new_media       INTEGER NOT NULL DEFAULT 0,
    errors_count    INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'running',
    error_log       TEXT,
    FOREIGN KEY (uid) REFERENCES users(uid)
);

CREATE TABLE IF NOT EXISTS crawl_state (
    id                      INTEGER PRIMARY KEY CHECK (id = 1),
    uid                     TEXT NOT NULL,
    is_first_crawl_complete INTEGER NOT NULL DEFAULT 0,
    last_page_crawled       INTEGER NOT NULL DEFAULT 0,
    total_posts_crawled     INTEGER NOT NULL DEFAULT 0,
    total_media_downloaded  INTEGER NOT NULL DEFAULT 0,
    last_full_crawl_at      TEXT,
    last_incremental_at     TEXT,
    last_error              TEXT,
    last_error_at           TEXT,
    consecutive_failures    INTEGER NOT NULL DEFAULT 0,
    visibility_limit        TEXT,              -- 当前检测到的可见限制
    earliest_post_date      TEXT,              -- 最早可见博文日期
    hidden_posts_count       INTEGER NOT NULL DEFAULT 0  -- 因限制被隐藏的博文数
);

CREATE TABLE IF NOT EXISTS visibility_change_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    uid             TEXT NOT NULL,
    detected_at     TEXT NOT NULL,
    previous_limit  TEXT,
    new_limit       TEXT,
    earliest_post   TEXT,
    total_visible   INTEGER,
    hidden_count    INTEGER NOT NULL DEFAULT 0,
    notes           TEXT,
    FOREIGN KEY (uid) REFERENCES users(uid)
);

CREATE TABLE IF NOT EXISTS api_structure_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,
    endpoint        TEXT NOT NULL,
    response_keys   TEXT NOT NULL,
    sample_post_id  TEXT,
    hash_changed    INTEGER NOT NULL DEFAULT 0,
    notes           TEXT
);
"""
