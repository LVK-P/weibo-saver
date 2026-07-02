"""Weibo Saver 异常层次结构."""


class WeiboSaverError(Exception):
    """所有异常的基类."""

    def __init__(self, message: str, *, detail: dict | None = None):
        super().__init__(message)
        self.detail = detail or {}


class ConfigError(WeiboSaverError):
    """配置相关错误."""


class CookieError(WeiboSaverError):
    """Cookie 提取或验证失败."""

    def __init__(self, message: str, *, browser: str | None = None, detail: dict | None = None):
        super().__init__(message, detail=detail)
        self.browser = browser


class APIError(WeiboSaverError):
    """微博 API 请求错误."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        endpoint: str | None = None,
        detail: dict | None = None,
    ):
        super().__init__(message, detail=detail)
        self.status_code = status_code
        self.endpoint = endpoint


class RateLimitError(APIError):
    """触发限流 (HTTP 432 或频率过高)."""

    def __init__(self, message: str, *, retry_after: float = 60.0, **kwargs):
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class CookieExpiredError(APIError):
    """Cookie 已过期."""


class BlockDetectedError(APIError):
    """检测到反爬封锁，需升级到浏览器模式."""


class CrawlError(WeiboSaverError):
    """爬取过程错误."""


class MediaDownloadError(WeiboSaverError):
    """媒体下载失败."""

    def __init__(
        self,
        message: str,
        *,
        media_url: str | None = None,
        media_type: str | None = None,
        detail: dict | None = None,
    ):
        super().__init__(message, detail=detail)
        self.media_url = media_url
        self.media_type = media_type


class DatabaseError(WeiboSaverError):
    """数据库操作错误."""


class APIStructureChangeError(APIError):
    """API 返回数据结构发生变化（用于日志告警）."""

    def __init__(
        self,
        message: str,
        *,
        endpoint: str | None = None,
        expected_fields: list[str] | None = None,
        actual_keys: list[str] | None = None,
        **kwargs,
    ):
        super().__init__(message, endpoint=endpoint, **kwargs)
        self.expected_fields = expected_fields or []
        self.actual_keys = actual_keys or []
