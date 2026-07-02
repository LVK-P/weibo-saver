"""代理池：多代理轮转 + 健康检查 + 自动淘汰.

支持的代理格式:
- http://host:port
- http://user:pass@host:port
- https://host:port
- socks5://host:port
- socks5://user:pass@host:port
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import httpx

logger = logging.getLogger("weibo_saver.core.proxy_pool")

# ============================================================
# 预置代理列表（免费代理，仅供测试，建议替换为付费代理）
# 注意：免费代理极不稳定，微博可能直接封禁这些 IP
# 实际生产环境请使用付费住宅代理或机房代理
# ============================================================
STARTER_PROXIES: list[str] = [
    # ---- 国内 HTTP 代理（示例，需自行验证可用性）----
    # 实际使用时请替换为以下来源获取的最新代理：
    # - 快代理: https://www.kuaidaili.com/free/
    # - 站大爷: https://www.zdaye.com/free/
    # - 89免费代理: https://www.89ip.cn/
    # - 齐云代理: https://proxy.ip3366.net/free/
    # - 豌豆代理: https://www.wandouip.com/
    #
    # 以下为格式示例（这些 IP 大概率已失效，仅展示格式）：
    # "http://123.456.78.90:8080",
    # "http://user:pass@111.222.333.444:9999",
    # "socks5://222.333.444.555:1080",
]

# 可以从这些 URL 自动抓取免费代理（需启用）
FREE_PROXY_SOURCES: list[str] = [
    # "https://www.kuaidaili.com/free/inha/",      # 快代理-高匿
    # "https://www.89ip.cn/tqdl.html?api=1&num=20", # 89代理 API
    # "https://proxy.ip3366.net/free/?action=china&page=1",  # 齐云
]


class ProxyStrategy(str, Enum):
    """代理选择策略."""

    ROUND_ROBIN = "round_robin"  # 轮转
    RANDOM = "random"            # 随机
    WEIGHTED = "weighted"        # 加权（成功率高的优先）
    BEST_FIRST = "best_first"    # 延迟最低的优先


@dataclass
class ProxyState:
    """单个代理的状态."""

    url: str
    scheme: str = "http"
    host: str = ""
    port: int = 0
    username: str | None = None
    password: str | None = None

    # 统计
    total_requests: int = 0
    success_count: int = 0
    fail_count: int = 0
    consecutive_fails: int = 0
    last_used_at: float = 0.0
    last_check_at: float = 0.0
    avg_latency: float = 0.0
    is_alive: bool = True
    score: float = 1.0  # 权重分数 (0.0-1.0)

    def record_success(self, latency: float = 0.0) -> None:
        """记录成功."""
        self.total_requests += 1
        self.success_count += 1
        self.consecutive_fails = 0
        self.last_used_at = time.monotonic()
        if latency > 0:
            if self.avg_latency == 0:
                self.avg_latency = latency
            else:
                self.avg_latency = self.avg_latency * 0.7 + latency * 0.3
        self._update_score()

    def record_failure(self) -> None:
        """记录失败."""
        self.total_requests += 1
        self.fail_count += 1
        self.consecutive_fails += 1
        self.last_used_at = time.monotonic()
        if self.consecutive_fails >= 3:
            self.is_alive = False
        self._update_score()

    def mark_alive(self) -> None:
        """标记为存活."""
        self.is_alive = True
        self.consecutive_fails = 0

    def _update_score(self) -> None:
        """更新权重分数."""
        if self.total_requests == 0:
            self.score = 1.0
            return

        # 成功率权重 (50%)
        success_rate = self.success_count / max(self.total_requests, 1)
        # 延迟权重 (30%) - 延迟越低越好
        latency_score = max(0, 1.0 - self.avg_latency / 10.0)  # 10s 为基准
        # 使用频率权重 (20%) - 越久没用权重越高
        time_since_last = time.monotonic() - self.last_used_at
        freshness = min(1.0, time_since_last / 300.0)  # 5 分钟为基准

        self.score = 0.5 * success_rate + 0.3 * latency_score + 0.2 * freshness


class ProxyPool:
    """代理池管理器.

    特性:
    - 多策略轮转 (round_robin / random / weighted)
    - 自动健康检查 (定期 + 失败驱逐)
    - 并发安全
    - 支持 HTTP/HTTPS/SOCKS5
    - 可从 URL 爬取免费代理
    """

    def __init__(
        self,
        proxies: list[str] | None = None,
        strategy: ProxyStrategy = ProxyStrategy.WEIGHTED,
        health_check_interval: float = 300.0,  # 5 分钟
        max_consecutive_fails: int = 3,
        min_alive_proxies: int = 2,
    ):
        """
        Args:
            proxies: 代理 URL 列表
            strategy: 选择策略
            health_check_interval: 健康检查间隔（秒）
            max_consecutive_fails: 连续失败多少次后标记为死亡
            min_alive_proxies: 最少存活代理数（低于此数时不启用代理池）
        """
        self._strategy = strategy
        self._health_check_interval = health_check_interval
        self._max_consecutive_fails = max_consecutive_fails
        self._min_alive_proxies = min_alive_proxies

        self._proxies: list[ProxyState] = []
        self._lock = asyncio.Lock()
        self._rr_index: int = 0
        self._last_health_check: float = 0.0
        self._disabled: bool = False
        self._http_client: httpx.AsyncClient | None = None

        # 解析代理
        if proxies:
            for url in proxies:
                self._add_proxy(url)

        if not self._proxies:
            self._disabled = True
            logger.info("代理池为空，已禁用（使用直连）")

    # ---- 代理管理 ----

    def add_proxy(self, url: str) -> None:
        """添加代理."""
        self._add_proxy(url)

    def add_proxies(self, urls: list[str]) -> None:
        """批量添加代理."""
        for url in urls:
            self._add_proxy(url)

    def remove_proxy(self, url: str) -> None:
        """移除代理."""
        self._proxies = [p for p in self._proxies if p.url != url]

    def _add_proxy(self, url: str) -> None:
        """解析并添加代理."""
        state = self._parse_proxy_url(url)
        if state:
            self._proxies.append(state)
            self._disabled = False
            logger.debug(f"代理已添加: {state.scheme}://{state.host}:{state.port}")

    @staticmethod
    def _parse_proxy_url(url: str) -> ProxyState | None:
        """解析代理 URL."""
        try:
            # 处理格式: scheme://[user:pass@]host:port
            scheme = "http"
            rest = url

            if "://" in url:
                scheme, rest = url.split("://", 1)

            auth = None
            host_port = rest

            if "@" in rest:
                auth, host_port = rest.rsplit("@", 1)

            host, port_str = host_port.rsplit(":", 1)
            port = int(port_str)

            username = None
            password = None
            if auth and ":" in auth:
                username, password = auth.split(":", 1)

            return ProxyState(
                url=url,
                scheme=scheme,
                host=host,
                port=port,
                username=username,
                password=password,
            )

        except Exception as e:
            logger.warning(f"无法解析代理 URL: {url} | {e}")
            return None

    # ---- 代理选择 ----

    async def get_proxy(self) -> ProxyState | None:
        """获取一个可用代理.

        Returns:
            ProxyState 或 None（表示不使用代理/直连）
        """
        if self._disabled:
            return None

        async with self._lock:
            alive = [p for p in self._proxies if p.is_alive]

            if len(alive) < self._min_alive_proxies:
                # 存活代理太少，降级为直连
                logger.warning(
                    f"存活代理不足 ({len(alive)}/{self._min_alive_proxies})，使用直连"
                )
                return None

            if not alive:
                return None

            if self._strategy == ProxyStrategy.ROUND_ROBIN:
                proxy = alive[self._rr_index % len(alive)]
                self._rr_index += 1
            elif self._strategy == ProxyStrategy.RANDOM:
                proxy = random.choice(alive)
            elif self._strategy == ProxyStrategy.WEIGHTED:
                # 按 score 加权随机
                total = sum(p.score for p in alive)
                r = random.uniform(0, total)
                cumulative = 0.0
                proxy = alive[-1]  # fallback
                for p in alive:
                    cumulative += p.score
                    if r <= cumulative:
                        proxy = p
                        break
            elif self._strategy == ProxyStrategy.BEST_FIRST:
                proxy = max(alive, key=lambda p: p.score)
            else:
                proxy = alive[0]

            return proxy

    def report_success(self, proxy: ProxyState, latency: float = 0.0) -> None:
        """上报代理成功."""
        proxy.record_success(latency)

    def report_failure(self, proxy: ProxyState) -> None:
        """上报代理失败."""
        proxy.record_failure()
        if not proxy.is_alive:
            logger.warning(f"代理已标记为死亡: {proxy.host}:{proxy.port}")

    # ---- 健康检查 ----

    async def health_check(self) -> None:
        """对所有代理进行健康检查."""
        now = time.monotonic()
        if now - self._last_health_check < self._health_check_interval:
            return

        if not self._proxies:
            return

        logger.info(f"开始代理健康检查 | 总数={len(self._proxies)}")

        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=10.0)

        tasks = []
        for proxy in self._proxies:
            tasks.append(self._check_single_proxy(proxy))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        alive_count = sum(1 for p in self._proxies if p.is_alive)
        self._last_health_check = time.monotonic()

        logger.info(
            f"健康检查完成 | 存活={alive_count}/{len(self._proxies)}"
        )

        if alive_count < self._min_alive_proxies:
            logger.warning(f"存活代理低于最低要求 ({alive_count} < {self._min_alive_proxies})")

    async def _check_single_proxy(self, proxy: ProxyState) -> None:
        """检查单个代理."""
        if self._http_client is None:
            return

        proxy_url = self._build_httpx_proxy_url(proxy)
        if not proxy_url:
            return

        try:
            start = time.monotonic()
            response = await self._http_client.get(
                "https://m.weibo.cn/api/config",
                proxy=proxy_url,
                timeout=8.0,
            )
            latency = time.monotonic() - start

            if response.status_code == 200:
                proxy.mark_alive()
                proxy.record_success(latency)
                logger.debug(f"代理健康: {proxy.host}:{proxy.port} | 延迟={latency:.2f}s")
            else:
                proxy.record_failure()
                logger.debug(f"代理异常响应: {proxy.host}:{proxy.port} | HTTP {response.status_code}")

        except Exception:
            proxy.record_failure()

    # ---- 工具 ----

    @staticmethod
    def _build_httpx_proxy_url(proxy: ProxyState) -> str | None:
        """构建 httpx 可用的代理 URL."""
        if not proxy.host or not proxy.port:
            return None

        auth_part = ""
        if proxy.username:
            auth_part = f"{proxy.username}:{proxy.password}@" if proxy.password else f"{proxy.username}@"

        return f"{proxy.scheme}://{auth_part}{proxy.host}:{proxy.port}"

    def get_proxy_url_for_httpx(self, proxy: ProxyState | None) -> str | None:
        """获取 httpx 兼容的代理 URL."""
        if proxy is None:
            return None
        return self._build_httpx_proxy_url(proxy)

    def get_proxy_dict_for_playwright(self, proxy: ProxyState | None) -> dict | None:
        """获取 Playwright 兼容的代理配置."""
        if proxy is None:
            return None

        config: dict[str, Any] = {
            "server": f"{proxy.scheme}://{proxy.host}:{proxy.port}",
        }
        if proxy.username:
            config["username"] = proxy.username
            config["password"] = proxy.password or ""

        return config

    # ---- 属性 ----

    @property
    def is_enabled(self) -> bool:
        return not self._disabled and len(self._proxies) > 0

    @property
    def alive_count(self) -> int:
        return sum(1 for p in self._proxies if p.is_alive)

    @property
    def total_count(self) -> int:
        return len(self._proxies)

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "enabled": self.is_enabled,
            "total": self.total_count,
            "alive": self.alive_count,
            "strategy": self._strategy.value,
            "proxies": [
                {
                    "host": f"{p.host}:{p.port}",
                    "alive": p.is_alive,
                    "score": round(p.score, 3),
                    "success_rate": round(p.success_count / max(p.total_requests, 1), 3),
                    "avg_latency": round(p.avg_latency, 2),
                    "requests": p.total_requests,
                }
                for p in self._proxies
            ],
        }

    async def close(self) -> None:
        """关闭 HTTP 客户端."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    # ---- 从 URL 获取免费代理 ----

    async def fetch_from_sources(self, sources: list[str] | None = None) -> int:
        """从免费代理网站抓取代理列表.

        Args:
            sources: 代理来源 URL 列表

        Returns:
            新增代理数量
        """
        if sources is None:
            sources = FREE_PROXY_SOURCES

        if not sources:
            return 0

        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=15.0)

        added = 0
        for source in sources:
            try:
                response = await self._http_client.get(source)
                if response.status_code == 200:
                    # 简单正则提取 IP:PORT
                    import re
                    pattern = re.compile(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):(\d{2,5})')
                    matches = pattern.findall(response.text)
                    for ip, port in matches:
                        url = f"http://{ip}:{port}"
                        if not any(p.url == url for p in self._proxies):
                            self._add_proxy(url)
                            added += 1
                    logger.info(f"从 {source} 提取了 {len(matches)} 个代理")
            except Exception as e:
                logger.warning(f"从 {source} 抓取代理失败: {e}")

        if added > 0:
            self._disabled = False
            logger.info(f"共新增 {added} 个代理")
            # 立即健康检查
            await self.health_check()

        return added
