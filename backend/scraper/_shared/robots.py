"""robots.txt compliance checker and per-domain rate limiter.

Treats robots.txt strictly as a compliance signal (respecting Disallow and
Crawl-delay), never as affirmative permission to scrape restricted sites.
"""
import asyncio
import logging
import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from backend.scraper._shared.url_safety import UnsafeURLError, safe_get

logger = logging.getLogger("jobnavigator.robots")

USER_AGENT = "JobNavigatorPersonalAgent/1.0"
_CACHE_TTL = 3600  # 1 hour
_ROBOTS_CACHE: dict[str, tuple[float, RobotFileParser | None]] = {}
_DOMAIN_LAST_FETCH: dict[str, float] = {}
_RATE_LOCK = asyncio.Lock()


def parse_robots_txt(robots_url: str, text: str) -> RobotFileParser:
    rp = RobotFileParser()
    rp.set_url(robots_url)
    rp.parse(text.splitlines())
    return rp


async def get_robots_parser(url: str) -> RobotFileParser | None:
    parts = urlsplit(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    now = time.monotonic()
    cached = _ROBOTS_CACHE.get(origin)
    if cached and (now - cached[0]) < _CACHE_TTL:
        return cached[1]

    robots_url = f"{origin}/robots.txt"
    try:
        resp = await safe_get(robots_url, timeout=8.0, headers={"User-Agent": USER_AGENT})
        if resp.status_code == 200:
            rp = parse_robots_txt(robots_url, resp.text)
            _ROBOTS_CACHE[origin] = (now, rp)
            return rp
        _ROBOTS_CACHE[origin] = (now, None)
        return None
    except UnsafeURLError:
        raise
    except Exception as e:
        logger.debug("robots.txt fetch failed for %s: %s", origin, e)
        _ROBOTS_CACHE[origin] = (now, None)
        return None


async def is_allowed_by_robots(url: str, user_agent: str = USER_AGENT) -> tuple[bool, float]:
    """Return (allowed, crawl_delay_seconds) for `url`."""
    rp = await get_robots_parser(url)
    if rp is None:
        return True, 1.0
    allowed = rp.can_fetch(user_agent, url)
    delay = rp.crawl_delay(user_agent)
    return bool(allowed), float(delay) if delay else 1.0


async def respect_rate_limit(url: str, min_delay: float = 1.0) -> None:
    """Enforce per-domain minimum delay between requests."""
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        return
    async with _RATE_LOCK:
        now = time.monotonic()
        last = _DOMAIN_LAST_FETCH.get(host, 0.0)
        wait = (last + min_delay) - now
        if wait > 0:
            await asyncio.sleep(wait)
        _DOMAIN_LAST_FETCH[host] = time.monotonic()
