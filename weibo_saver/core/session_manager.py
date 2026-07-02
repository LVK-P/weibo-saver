"""会话管理：Cookie 提取、验证、刷新."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import httpx

from ..constants import MOBILE_HEADERS, WEIBO_API_CONFIG, WEIBO_MOBILE_BASE
from ..exceptions import CookieError, CookieExpiredError

logger = logging.getLogger("weibo_saver.core.session_manager")


class SessionManager:
    """管理微博 API 会话.

    支持两种 Cookie 来源:
    1. 从浏览器自动提取 (browser_cookie3)
    2. 手动提供的 Cookie 字符串
    """

    # 微博关键 Cookie 字段
    REQUIRED_COOKIES = ("SUB",)

    def __init__(self, browser: str = "chrome"):
        self._browser = browser
        self._cookies: dict[str, str] = {}
        self._validated_at: float = 0.0
        self._validation_ttl: float = 600.0  # 10 分钟重新验证

    # ---- Cookie 提取 ----

    def extract_from_browser(self) -> dict[str, str]:
        """从浏览器提取 Cookie.

        Returns:
            Cookie 字典

        Raises:
            CookieError: 提取失败
        """
        try:
            import browser_cookie3
        except ImportError:
            raise CookieError(
                "请安装 browser_cookie3: pip install browser_cookie3",
                browser=self._browser,
            )

        browser_map = {
            "chrome": browser_cookie3.chrome,
            "edge": browser_cookie3.edge,
        }

        load_fn = browser_map.get(self._browser)
        if load_fn is None:
            raise CookieError(
                f"不支持的浏览器: {self._browser}",
                browser=self._browser,
            )

        try:
            jar = load_fn(domain_name="weibo.cn")
            cookies: dict[str, str] = {}
            for cookie in jar:
                name = cookie.name if hasattr(cookie, "name") else cookie[0]
                value = cookie.value if hasattr(cookie, "value") else cookie[1]
                cookies[name] = value

            # 同时尝试 weibo.com 的 Cookie
            try:
                jar_com = browser_cookie3.chrome(domain_name="weibo.com")
                for cookie in jar_com:
                    name = cookie.name if hasattr(cookie, "name") else cookie[0]
                    if name not in cookies:
                        value = cookie.value if hasattr(cookie, "value") else cookie[1]
                        cookies[name] = value
            except Exception:
                pass

            if not cookies:
                raise CookieError(
                    "未找到微博 Cookie，请先在浏览器中登录微博",
                    browser=self._browser,
                )

            self._cookies = cookies
            logger.info(
                f"从 {self._browser} 提取了 {len(cookies)} 个 Cookie | "
                f"关键字段: {[k for k in self.REQUIRED_COOKIES if k in cookies]}"
            )
            return cookies

        except CookieError:
            raise
        except Exception as e:
            raise CookieError(
                f"Cookie 提取失败: {e}",
                browser=self._browser,
                detail={"error": str(e)},
            ) from e

    def load_cookies_from_string(self, cookie_str: str) -> dict[str, str]:
        """从 Cookie 字符串加载.

        Args:
            cookie_str: 格式 "name1=value1; name2=value2"

        Returns:
            Cookie 字典
        """
        cookies: dict[str, str] = {}
        for item in cookie_str.split(";"):
            item = item.strip()
            if "=" in item:
                key, _, value = item.partition("=")
                cookies[key.strip()] = value.strip()

        if not cookies:
            raise CookieError("Cookie 字符串为空或格式不正确")

        self._cookies = cookies
        logger.info(f"从字符串加载了 {len(cookies)} 个 Cookie")
        return cookies

    def load_cookies_from_file(self, path: str | Path) -> dict[str, str]:
        """从 JSON 文件加载 Cookie."""
        import json

        path = Path(path)
        if not path.exists():
            raise CookieError(f"Cookie 文件不存在: {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if isinstance(data, list):
            # Playwright storage state 格式
            cookies = {
                c["name"]: c["value"]
                for c in data
                if isinstance(c, dict) and "name" in c and "value" in c
            }
        elif isinstance(data, dict):
            cookies = data
        else:
            raise CookieError("Cookie 文件格式无法识别")

        self._cookies = cookies
        return cookies

    # ---- 验证 ----

    async def validate(self, client: httpx.AsyncClient | None = None) -> bool:
        """验证 Cookie 是否有效.

        通过向 m.weibo.cn 发送轻量请求来检查登录状态.
        """
        if not self._cookies:
            return False

        should_close = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=10.0)

        try:
            response = await client.get(
                f"{WEIBO_MOBILE_BASE}{WEIBO_API_CONFIG}",
                headers=MOBILE_HEADERS,
                cookies=self._cookies,
            )
            data = response.json()

            # 检查登录状态：m.weibo.cn 返回 data.login (bool)
            if isinstance(data, dict):
                login_data = data.get("data", {})
                # m.weibo.cn API 返回 "login" 字段，camelCase
                is_logged_in = login_data.get("login", False)
                # 也检查是否有 loginUrl（未登录时会返回）
                has_login_url = bool(login_data.get("loginUrl") or login_data.get("login_url"))

                if not is_logged_in:
                    logger.warning(f"Cookie 已过期 (login={is_logged_in}, has_login_url={has_login_url})")
                    self._validated_at = 0
                    return False

                if response.status_code == 200:
                    self._validated_at = asyncio.get_event_loop().time()
                    logger.info("Cookie 验证通过")
                    return True

            return False

        except Exception as e:
            logger.warning(f"Cookie 验证请求失败: {e}")
            return False
        finally:
            if should_close:
                await client.aclose()

    async def ensure_valid(self, client: httpx.AsyncClient) -> None:
        """确保 Cookie 有效，否则抛出异常."""
        loop = asyncio.get_event_loop()
        now = loop.time()

        # 在 TTL 范围内使用缓存结果
        if self._validated_at and (now - self._validated_at) < self._validation_ttl:
            return

        if not await self.validate(client):
            raise CookieExpiredError(
                "Cookie 已失效，请重新登录微博",
                browser=self._browser,
                status_code=401,
                endpoint="/api/config",
            )

    async def needs_revalidation(self) -> bool:
        """是否需要重新验证."""
        if not self._validated_at:
            return True
        loop = asyncio.get_event_loop()
        return (loop.time() - self._validated_at) > self._validation_ttl

    # ---- 属性 ----

    @property
    def cookies(self) -> dict[str, str]:
        return self._cookies

    @property
    def cookie_string(self) -> str:
        """获取 Cookie 字符串格式."""
        return "; ".join(f"{k}={v}" for k, v in self._cookies.items())

    @property
    def is_loaded(self) -> bool:
        return bool(self._cookies)

    @property
    def has_sub(self) -> bool:
        """是否有关键的 SUB Cookie."""
        return "SUB" in self._cookies
