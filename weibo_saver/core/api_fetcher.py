"""API 抓取器（主模式）：基于 httpx 直接调用 m.weibo.cn 移动端 API.

这是日常监控的首选模式，轻量、快速。
仅在遇到持续封锁时才会升级到 BrowserFetcher。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from ..config import Config, RetryConfig
from ..constants import (
    MOBILE_HEADERS,
    WEIBO_API_CONFIG,
    WEIBO_API_LONG_TEXT,
    WEIBO_API_TIMELINE,
    WEIBO_MOBILE_BASE,
)
from ..exceptions import (
    APIError,
    CookieExpiredError,
    RateLimitError,
)
from ..models.post import Post
from ..models.user import User
from ..utils.rate_limiter import AdaptiveRateLimiter
from ..utils.retry import async_retry
from .block_detector import BlockDetector
from .proxy_pool import ProxyPool, ProxyState
from .session_manager import SessionManager

logger = logging.getLogger("weibo_saver.core.api_fetcher")


class ApiFetcher:
    """基于 httpx 的微博 API 抓取器（轻量主模式）."""

    def __init__(
        self,
        session: SessionManager,
        rate_limiter: AdaptiveRateLimiter,
        block_detector: BlockDetector,
        retry_config: RetryConfig,
        proxy_pool: ProxyPool | None = None,
    ):
        self._session = session
        self._rate_limiter = rate_limiter
        self._block_detector = block_detector
        self._retry_config = retry_config
        self._proxy_pool = proxy_pool
        self._client: httpx.AsyncClient | None = None
        self._current_proxy: ProxyState | None = None

    async def start(self) -> None:
        """初始化 HTTP 客户端."""
        self._client = httpx.AsyncClient(
            headers=MOBILE_HEADERS,
            cookies=self._session.cookies,
            timeout=30.0,
            follow_redirects=True,
        )
        await self._session.ensure_valid(self._client)
        logger.info("ApiFetcher 已启动")

    async def stop(self) -> None:
        """关闭 HTTP 客户端."""
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("ApiFetcher 已停止")

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("ApiFetcher 未启动，请先调用 start()")
        return self._client

    # ---- 用户相关 ----

    async def get_user_info(self, uid: str) -> User:
        """获取用户信息."""
        await self._rate_limiter.acquire()

        params = {
            "type": "uid",
            "value": uid,
            "containerid": f"100505{uid}",
        }

        response = await self._request("GET", WEIBO_API_TIMELINE, params=params)
        data = response.json()

        self._block_detector.check_response(response.status_code, data, WEIBO_API_TIMELINE)

        if not data.get("ok"):
            raise APIError(
                f"获取用户信息失败: {data.get('msg', 'unknown')}",
                status_code=response.status_code,
                endpoint=WEIBO_API_TIMELINE,
            )

        user = User.from_api_response(data.get("data", {}))
        return user

    # ---- 时间线 ----

    async def get_timeline(
        self,
        uid: str,
        page: int = 1,
        since_id: str | None = None,
    ) -> list[Post]:
        """获取用户时间线.

        Args:
            uid: 用户 UID
            page: 页码（从 1 开始）
            since_id: 只获取比此 ID 更新的博文

        Returns:
            Post 列表（已过滤转发）
        """
        await self._rate_limiter.acquire()

        containerid = f"107603{uid}"
        params: dict[str, str] = {
            "type": "uid",
            "value": uid,
            "containerid": containerid,
            "page": str(page),
        }
        if since_id:
            params["since_id"] = since_id

        response = await self._request("GET", WEIBO_API_TIMELINE, params=params)
        data = response.json()

        # 检测封锁
        status = self._block_detector.check_response(
            response.status_code, data, WEIBO_API_TIMELINE
        )
        if status == "432":
            await self._rate_limiter.report_432()
            raise RateLimitError(
                "触发 432 限流",
                status_code=432,
                endpoint=WEIBO_API_TIMELINE,
                retry_after=self._rate_limiter.penalty * 45,
            )
        elif status == "structure_change":
            # 结构变化，记录日志但继续尝试解析
            logger.api_change(  # type: ignore[attr-defined]
                f"API 响应结构变更 | uid={uid} | page={page} | 将继续尝试解析"
            )

        if not data.get("ok"):
            raise APIError(
                f"获取时间线失败: {data.get('msg', 'unknown')}",
                status_code=response.status_code,
                endpoint=WEIBO_API_TIMELINE,
            )

        # 解析卡片
        cards_data = data.get("data", {}).get("cards", [])
        posts: list[Post] = []
        for card in cards_data:
            # 检查卡片结构
            self._block_detector.check_card_structure(card)

            post = Post.from_api_card(card)
            if post is not None:  # None 表示转发或系统卡片
                posts.append(post)

        # 记录 API 结构
        if cards_data:
            self._log_api_structure(WEIBO_API_TIMELINE, cards_data[0])

        # 成功，通知限流器
        await self._rate_limiter.report_success()
        logger.debug(f"获取时间线 | uid={uid} | page={page} | posts={len(posts)}")

        return posts

    async def get_long_text(self, post_id: str) -> str:
        """获取长文展开内容."""
        await self._rate_limiter.acquire()

        params = {"id": post_id}
        response = await self._request("GET", WEIBO_API_LONG_TEXT, params=params)
        data = response.json()

        if data.get("ok"):
            long_text = data.get("data", {}).get("longTextContent", "")
            return long_text

        logger.warning(f"获取长文失败: {data.get('msg', 'unknown')} | post_id={post_id}")
        return ""

    # ---- 内部方法 ----

    async def _request(
        self, method: str, path: str, **kwargs: Any
    ) -> httpx.Response:
        """发送 HTTP 请求，带 Cookie 验证、代理和重试."""
        url = f"{WEIBO_MOBILE_BASE}{path}"

        # 获取代理
        proxy_url = None
        if self._proxy_pool and self._proxy_pool.is_enabled:
            self._current_proxy = await self._proxy_pool.get_proxy()
            if self._current_proxy:
                proxy_url = self._proxy_pool.get_proxy_url_for_httpx(self._current_proxy)
                logger.debug(f"使用代理: {self._current_proxy.host}:{self._current_proxy.port}")
            if proxy_url:
                kwargs["proxy"] = proxy_url

        # 定期验证 Cookie
        if await self._session.needs_revalidation():
            await self._session.ensure_valid(self.client)

        try:
            import time

            start = time.monotonic()
            response = await self.client.request(method, url, **kwargs)
            latency = time.monotonic() - start

            # 代理上报成功
            if self._current_proxy and self._proxy_pool:
                self._proxy_pool.report_success(self._current_proxy, latency)

            # 检查 Cookie 过期
            if response.status_code in (403, 302):
                await self._session.ensure_valid(self.client)

            return response

        except httpx.TimeoutException as e:
            if self._current_proxy and self._proxy_pool:
                self._proxy_pool.report_failure(self._current_proxy)
            raise APIError(
                f"请求超时: {url}",
                endpoint=path,
                detail={"error": str(e)},
            ) from e
        except httpx.NetworkError as e:
            if self._current_proxy and self._proxy_pool:
                self._proxy_pool.report_failure(self._current_proxy)
            raise APIError(
                f"网络错误: {url}",
                endpoint=path,
                detail={"error": str(e)},
            ) from e

    def _log_api_structure(self, endpoint: str, sample_card: dict) -> None:
        """记录 API 响应结构（供后续分析）."""
        # 提取顶层 key
        mblog = sample_card.get("mblog", sample_card)
        top_keys = sorted(mblog.keys()) if isinstance(mblog, dict) else []
        logger.debug(
            f"API 结构记录 | endpoint={endpoint} | keys={top_keys[:20]}"
        )

    @property
    def escalation_score(self) -> float:
        return self._block_detector.escalation_score

    @property
    def cookies_valid(self) -> bool:
        return self._session.is_loaded
