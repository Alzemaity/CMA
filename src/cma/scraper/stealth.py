import asyncio
import os
import random
from urllib.parse import urlparse

from playwright.async_api import BrowserContext, Page

from cma.config import settings

def get_random_proxy() -> dict | None:
    """
    Selects a random proxy from proxies.txt (if it exists) or settings.proxy_list.
    Returns a dictionary suitable for Playwright context kwargs:
    {"server": "http://host:port", "username": "...", "password": "..."}
    or None if no proxy is configured.
    """
    proxies = []
    
    # 1. Try to load from proxies.txt in the root directory
    if os.path.exists("proxies.txt"):
        try:
            with open("proxies.txt", "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        proxies.append(line)
        except Exception:
            pass
            
    # 2. Try to load from settings.proxy_list (comma-separated)
    if not proxies and settings.proxy_list:
        proxies = [p.strip() for p in settings.proxy_list.split(",") if p.strip()]
        
    if not proxies:
        # Fallback to single proxy if configured
        if settings.proxy_server:
            server = settings.proxy_server
            if not server.startswith(("http://", "https://", "socks5://")):
                server = f"http://{server}"
            proxy_dict = {"server": server}
            if settings.proxy_username:
                proxy_dict["username"] = settings.proxy_username
            if settings.proxy_password:
                proxy_dict["password"] = settings.proxy_password
            return proxy_dict
        return None
        
    # Pick a random proxy
    proxy_str = random.choice(proxies)
    
    # Parse proxy_str (e.g. http://username:password@host:port)
    if not proxy_str.startswith(("http://", "https://", "socks5://")):
        proxy_str = f"http://{proxy_str}"
        
    try:
        parsed = urlparse(proxy_str)
        server_scheme = parsed.scheme or "http"
        server_netloc = parsed.hostname
        if parsed.port:
            server_netloc = f"{server_netloc}:{parsed.port}"
        
        proxy_dict = {
            "server": f"{server_scheme}://{server_netloc}"
        }
        if parsed.username:
            proxy_dict["username"] = parsed.username
        if parsed.password:
            proxy_dict["password"] = parsed.password
        return proxy_dict
    except Exception:
        return None


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]

VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1280, "height": 720},
]


def random_user_agent() -> str:
    return random.choice(USER_AGENTS)


def random_viewport() -> dict:
    return random.choice(VIEWPORTS)


async def random_delay(min_sec: float = 2.0, max_sec: float = 6.0):
    await asyncio.sleep(random.uniform(min_sec, max_sec))


async def apply_stealth(context: BrowserContext):
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['ar', 'en-US', 'en'] });
        window.chrome = { runtime: {} };
    """)


async def human_scroll(page: Page, scrolls: int = 3):
    for _ in range(scrolls):
        scroll_amount = random.randint(300, 700)
        await page.mouse.wheel(0, scroll_amount)
        await asyncio.sleep(random.uniform(0.5, 1.5))
