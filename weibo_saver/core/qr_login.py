"""微博扫码登录模块 — 基于 httpx + passport.weibo.com API.

无需浏览器（Playwright），更快更稳定。
流程:
  1. 预热 session → 获取 cookie
  2. 请求 passport QR code API → 返回二维码图片 + 服务端设置 token cookie
  3. 轮询检测扫码状态
  4. 扫描后 follow redirect → 提取完整 cookie
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Callable
from urllib.parse import urlencode

import httpx

logger = logging.getLogger("weibo_saver.core.qr_login")

PASSPORT_BASE = "https://passport.weibo.com"
WEIBO_BASE = "https://weibo.com"

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/18.0 Mobile/15E148 Safari/604.1"
)


class QrLoginResult:
    """扫码登录结果."""
    def __init__(self, success: bool = False, cookie_string: str = "",
                 message: str = ""):
        self.success = success
        self.cookie_string = cookie_string
        self.message = message


async def qr_login(
    qr_callback: Callable[[bytes], None] | None = None,
    status_callback: Callable[[str], None] | None = None,
    cancel_event: asyncio.Event | None = None,
    timeout: int = 120,
) -> QrLoginResult:
    """扫码登录微博.

    Args:
        qr_callback: 二维码图片回调（bytes）
        status_callback: 状态文本回调
        cancel_event: 取消信号
        timeout: 超时秒数

    Returns:
        QrLoginResult
    """
    cancel = cancel_event or asyncio.Event()
    client = httpx.AsyncClient(
        headers={
            "User-Agent": MOBILE_UA,
            "Accept": "text/html,application/json,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": f"{WEIBO_BASE}/login",
        },
        timeout=15,
        follow_redirects=False,
    )

    try:
        # ---- 1. 预热 session（获取初始 cookie） ----
        if status_callback:
            status_callback("正在初始化...")
        try:
            resp = await client.get(f"{WEIBO_BASE}/login", timeout=10, follow_redirects=True)
            resp.raise_for_status()
        except Exception as e:
            return QrLoginResult(success=False, message=f"连接微博失败: {str(e)[:50]}")

        if cancel.is_set():
            return QrLoginResult(success=False, message="已取消")

        # ---- 2. 获取二维码 ----
        if status_callback:
            status_callback("正在获取二维码...")

        ts = int(time.time() * 1000)
        qr_url = f"{PASSPORT_BASE}/sso/qrcode/image?entry=miniblog&size=180×180&_={ts}"

        try:
            qr_resp = await client.get(qr_url, timeout=10)
            qr_resp.raise_for_status()
        except Exception as e:
            return QrLoginResult(success=False, message=f"获取二维码失败: {str(e)[:50]}")

        # 检测返回类型：可能是图片或 JSON
        content_type = qr_resp.headers.get("content-type", "")
        if "image" in content_type:
            # 直接返回图片
            img_data = qr_resp.content
        else:
            # JSON 响应：尝试提取图片地址
            try:
                data = qr_resp.json()
                img_url = data.get("data", {}).get("image", "")
                if not img_url:
                    img_url = re.search(r'https?://[^"\']+\.(png|jpg)', qr_resp.text)
                    img_url = img_url.group(0) if img_url else ""
                if img_url:
                    img_resp = await client.get(img_url, timeout=10)
                    img_data = img_resp.content
                else:
                    return QrLoginResult(success=False, message="无法解析二维码图片")
            except Exception:
                return QrLoginResult(success=False, message="无法解析二维码响应")

        if qr_callback:
            qr_callback(img_data)

        if cancel.is_set():
            return QrLoginResult(success=False, message="已取消")

        if status_callback:
            status_callback("请使用手机微博扫码登录")

        # ---- 3. 轮询扫码状态 ----
        # passport 通过 cookie 中的 alt / qrcode_from 识别会话
        poll_url = f"{PASSPORT_BASE}/sso/qrcode/check"
        poll_interval = 1.5
        scanned = False
        ticket_url = ""

        for _ in range(int(timeout / poll_interval)):
            if cancel.is_set():
                return QrLoginResult(success=False, message="已取消")

            await asyncio.sleep(poll_interval)
            try:
                ts2 = int(time.time() * 1000)
                check_resp = await client.get(
                    f"{poll_url}?entry=miniblog&_={ts2}",
                    timeout=8,
                )
                text = check_resp.text.strip()

                # 返回码说明:
                # 50114001 = 未扫描, 50114002 = 已扫描待确认,
                # 20000000 = 已确认/成功
                if "20000000" in text or "crossDomainUrl" in text:
                    scanned = True
                    # 提取跳转 URL
                    try:
                        data = check_resp.json()
                        ticket_url = data.get("data", {}).get("url", "")
                    except Exception:
                        urls = re.findall(r'https?://[^\s"\'<>]+', text)
                        if urls:
                            ticket_url = urls[0]
                    if ticket_url:
                        break
                    scanned = False

                elif "50114002" in text or "50114001" in text:
                    pass  # 继续轮询

            except Exception:
                continue

        if not scanned or not ticket_url:
            return QrLoginResult(success=False, message="登录超时或未扫描")

        if status_callback:
            status_callback("登录成功，正在获取Cookie...")

        # ---- 4. follow redirect 完成登录 ----
        try:
            resp = await client.get(ticket_url, timeout=10)
            # 可能有多重跳转，跟随直到稳定
            for _ in range(5):
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("location", "")
                    if location:
                        resp = await client.get(
                            location if location.startswith("http") else f"{PASSPORT_BASE}{location}",
                            timeout=10,
                        )
                    else:
                        break
                else:
                    break
        except Exception:
            pass

        await asyncio.sleep(1)

        # ---- 5. 提取所有 cookie ----
        cookies = dict(client.cookies)
        if not cookies:
            return QrLoginResult(success=False, message="未获取到Cookie")

        # 构建 Cookie 字符串
        core_keys = {"SUB", "SUBP", "XSRF-TOKEN", "ALF", "MLOGIN",
                     "_T_WM", "WEIBOCN_FROM", "M_WEIBOCN_PARAMS",
                     "gdxidpyhxdE"}
        cookie_parts = []
        seen = set()
        for name, value in cookies.items():
            if name in core_keys and name not in seen:
                cookie_parts.append(f"{name}={value}")
                seen.add(name)

        if "SUB" not in {c.split("=")[0] for c in cookie_parts if "=" in c}:
            # 全量导出
            cookie_parts = [f"{k}={v}" for k, v in cookies.items()]

        cookie_string = "; ".join(cookie_parts)

        if status_callback:
            status_callback("登录成功！")

        return QrLoginResult(
            success=True,
            cookie_string=cookie_string,
            message="登录成功",
        )

    except Exception as e:
        logger.error(f"扫码登录失败: {e}", exc_info=True)
        return QrLoginResult(success=False, message=f"登录失败: {str(e)[:80]}")
    finally:
        await client.aclose()
