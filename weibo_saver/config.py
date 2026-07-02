"""配置管理 — 支持每用户独立设置."""
from __future__ import annotations
import argparse, json, os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from .constants import DEFAULT_CONFIG
from .exceptions import ConfigError


@dataclass
class MonitorConfig:
    enabled: bool = True
    interval_seconds: int = 60
    quiet_hours_enabled: bool = False
    quiet_hours_start: str = "23:00"
    quiet_hours_end: str = "07:00"

@dataclass
class RateLimitConfig:
    min_delay_seconds: float = 1.5
    max_delay_seconds: float = 4.5
    max_requests_per_minute: int = 15

@dataclass
class DownloadConfig:
    images: bool = True
    videos: bool = True
    gifs: bool = True
    max_video_size_mb: int = 0  # 0=不限制
    max_concurrent_downloads: int = 3
    image_quality: str = "original"

@dataclass
class OutputConfig:
    save_json: bool = True
    save_markdown: bool = True
    save_txt: bool = True
    save_raw_response: bool = False

@dataclass
class RetryConfig:
    max_retries: int = 5
    backoff_base_seconds: float = 2.0
    backoff_max_seconds: float = 120.0

@dataclass
class ProxyConfig:
    enabled: bool = False
    proxies: list[str] = field(default_factory=list)
    strategy: str = "weighted"
    health_check_interval: float = 300.0
    max_consecutive_fails: int = 3
    min_alive_proxies: int = 2
    auto_fetch_free: bool = False
    proxy_sources: list[str] = field(default_factory=list)

@dataclass
class LoggingConfig:
    level: str = "INFO"
    max_log_files: int = 30
    max_log_size_mb: int = 10

@dataclass
class UserConfig:
    """单个目标用户的独立配置."""
    uid: str
    screen_name: str = ""
    monitoring: bool = True
    custom_limit_enabled: bool = False
    max_cards: int = 0             # 0 = 使用全局默认
    custom_path_enabled: bool = False
    archive_path: str = ""         # 空 = 使用全局默认
    content_text: bool = True
    content_images: bool = True
    content_videos: bool = True
    post_count: int = 0
    visibility: str = "未知"

    def to_dict(self) -> dict:
        return {
            "uid": self.uid, "screen_name": self.screen_name,
            "monitoring": self.monitoring,
            "custom_limit_enabled": self.custom_limit_enabled,
            "max_cards": self.max_cards,
            "custom_path_enabled": self.custom_path_enabled,
            "archive_path": self.archive_path,
            "content_text": self.content_text,
            "content_images": self.content_images,
            "content_videos": self.content_videos,
            "post_count": self.post_count, "visibility": self.visibility,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "UserConfig":
        return cls(
            uid=d.get("uid",""), screen_name=d.get("screen_name",""),
            monitoring=d.get("monitoring",True),
            custom_limit_enabled=d.get("custom_limit_enabled",False),
            max_cards=d.get("max_cards",0),
            custom_path_enabled=d.get("custom_path_enabled",False),
            archive_path=d.get("archive_path",""),
            content_text=d.get("content_text",True),
            content_images=d.get("content_images",True),
            content_videos=d.get("content_videos",True),
            post_count=d.get("post_count",0),
            visibility=d.get("visibility","未知"),
        )


@dataclass
class Config:
    archive_root: Path
    browser: str = "chrome"
    target_uids: list[str] = field(default_factory=list)
    users: list[UserConfig] = field(default_factory=list)
    monitor: MonitorConfig = field(default_factory=MonitorConfig)
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    download: DownloadConfig = field(default_factory=DownloadConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    retry: RetryConfig = field(default_factory=RetryConfig)
    proxy: ProxyConfig = field(default_factory=ProxyConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    def get_user(self, uid: str) -> UserConfig | None:
        for u in self.users:
            if u.uid == uid: return u
        return None

    @classmethod
    def from_file(cls, path: str | Path) -> "Config":
        path = Path(path)
        if not path.exists(): raise ConfigError(f"配置文件不存在: {path}")
        with open(path, "r", encoding="utf-8") as f: data = json.load(f)
        return cls._from_dict(data)

    @classmethod
    def from_cli(cls, args: argparse.Namespace | None = None) -> "Config":
        if args is None: args = cls._parse_args()
        config = cls.from_file(args.config) if args.config else cls.default()
        if args.archive_root: config.archive_root = Path(args.archive_root)
        if args.browser: config.browser = args.browser
        if args.uid: config.target_uids = args.uid
        if args.once: config.monitor.enabled = False
        if args.interval: config.monitor.interval_seconds = args.interval
        if args.verbose: config.logging.level = "DEBUG"
        return config

    @classmethod
    def default(cls) -> "Config":
        return cls._from_dict(DEFAULT_CONFIG)

    @classmethod
    def _from_dict(cls, data: dict) -> "Config":
        ar = Path(data.get("archive_root","") or os.path.join(os.path.expanduser("~"),"Documents","WeiboArchive"))
        users = [UserConfig.from_dict(u) for u in data.get("users",[])]
        return cls(
            archive_root=ar, browser=data.get("browser","chrome"),
            target_uids=data.get("target_uids",[]), users=users,
            monitor=MonitorConfig(**data.get("monitor",{})),
            rate_limit=RateLimitConfig(**data.get("rate_limit",{})),
            download=DownloadConfig(**data.get("download",{})),
            output=OutputConfig(**data.get("output",{})),
            retry=RetryConfig(**data.get("retry",{})),
            proxy=ProxyConfig(**data.get("proxy",{})),
            logging=LoggingConfig(**data.get("logging",{})),
        )

    def to_dict(self) -> dict:
        return {
            "archive_root": str(self.archive_root), "browser": self.browser,
            "target_uids": self.target_uids,
            "users": [u.to_dict() for u in self.users],
            "monitor": {"enabled":self.monitor.enabled,"interval_seconds":self.monitor.interval_seconds,"quiet_hours_enabled":self.monitor.quiet_hours_enabled,"quiet_hours_start":self.monitor.quiet_hours_start,"quiet_hours_end":self.monitor.quiet_hours_end},
            "rate_limit": {"min_delay_seconds":self.rate_limit.min_delay_seconds,"max_delay_seconds":self.rate_limit.max_delay_seconds,"max_requests_per_minute":self.rate_limit.max_requests_per_minute},
            "download": {"images":self.download.images,"videos":self.download.videos,"gifs":self.download.gifs,"max_video_size_mb":self.download.max_video_size_mb,"max_concurrent_downloads":self.download.max_concurrent_downloads,"image_quality":self.download.image_quality},
            "output": {"save_json":self.output.save_json,"save_markdown":self.output.save_markdown,"save_txt":self.output.save_txt,"save_raw_response":self.output.save_raw_response},
            "retry": {"max_retries":self.retry.max_retries,"backoff_base_seconds":self.retry.backoff_base_seconds,"backoff_max_seconds":self.retry.backoff_max_seconds},
            "proxy": {"enabled":self.proxy.enabled,"proxies":self.proxy.proxies,"strategy":self.proxy.strategy,"health_check_interval":self.proxy.health_check_interval,"max_consecutive_fails":self.proxy.max_consecutive_fails,"min_alive_proxies":self.proxy.min_alive_proxies,"auto_fetch_free":self.proxy.auto_fetch_free,"proxy_sources":self.proxy.proxy_sources},
            "logging": {"level":self.logging.level,"max_log_files":self.logging.max_log_files,"max_log_size_mb":self.logging.max_log_size_mb},
        }

    def save(self, path): (p:=Path(path)).parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(self.to_dict(),ensure_ascii=False,indent=2),encoding="utf-8")

    def validate(self) -> list[str]:
        issues = []
        if not self.target_uids and not self.users: issues.append("target_uids 和 users 均为空")
        return issues

    @staticmethod
    def _parse_args():
        p = argparse.ArgumentParser(description="Weibo Saver")
        p.add_argument("--config",type=Path); p.add_argument("--archive-root",type=str)
        p.add_argument("--browser",choices=["chrome","edge"],default="chrome")
        p.add_argument("--uid",action="append"); p.add_argument("--once",action="store_true")
        p.add_argument("--interval",type=int,default=60); p.add_argument("--verbose","-v",action="store_true")
        p.add_argument("--list-cookies",action="store_true"); p.add_argument("--export-config",type=Path)
        return p.parse_args()
