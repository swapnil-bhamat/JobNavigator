"""RSS/Atom job feed adapter (zero browser/Playwright overhead)."""
import logging
import xml.etree.ElementTree as ET
from html import unescape
import re

from bs4 import BeautifulSoup

from backend.scraper._shared.robots import USER_AGENT, is_allowed_by_robots, respect_rate_limit
from backend.scraper._shared.url_safety import safe_get

logger = logging.getLogger("jobnavigator.rss")

_TAG_RE = re.compile(r"<[^>]+>")


def _clean_html(raw: str) -> str:
    if not raw:
        return ""
    text = BeautifulSoup(unescape(raw), "html.parser").get_text("\n", strip=True)
    return text.strip()


def _text(el, *tags: str) -> str:
    for tag in tags:
        child = el.find(tag)
        if child is not None and child.text:
            return child.text.strip()
    return ""


def parse_feed_xml(xml_text: str, source_name: str = "rss") -> list[dict]:
    """Parse RSS 2.0 or Atom XML into normalized job dicts."""
    root = ET.fromstring(xml_text)
    items: list[dict] = []

    # RSS 2.0: <rss><channel><item>
    channel = root.find("channel")
    if channel is not None:
        for item in channel.findall("item"):
            title = _text(item, "title")
            link = _text(item, "link")
            desc = _clean_html(_text(item, "description", "{http://purl.org/rss/1.0/modules/content/}encoded"))
            pub_date = _text(item, "pubDate")
            company = _text(item, "author", "{http://purl.org/dc/elements/1.1/}creator") or "Unknown"
            if title and link:
                items.append({
                    "title": title,
                    "company": company,
                    "url": link,
                    "description": desc,
                    "published_at": pub_date,
                    "source": source_name,
                })
        return items

    # Atom: <feed xmlns="http://www.w3.org/2005/Atom"><entry>
    ns = "{http://www.w3.org/2005/Atom}"
    for entry in root.findall(f"{ns}entry") + root.findall("entry"):
        title = _text(entry, f"{ns}title", "title")
        link_el = entry.find(f"{ns}link")
        if link_el is None:
            link_el = entry.find("link")
        link = link_el.attrib.get("href", "").strip() if link_el is not None else ""
        desc = _clean_html(_text(entry, f"{ns}content", "content", f"{ns}summary", "summary"))
        pub_date = _text(entry, f"{ns}published", "published", f"{ns}updated", "updated")
        author_el = entry.find(f"{ns}author")
        if author_el is None:
            author_el = entry.find("author")
        company = _text(author_el, f"{ns}name", "name") if author_el is not None else "Unknown"
        if title and link:
            items.append({
                "title": title,
                "company": company or "Unknown",
                "url": link,
                "description": desc,
                "published_at": pub_date,
                "source": source_name,
            })

    return items


async def fetch_rss_feed(url: str, source_name: str = "rss") -> list[dict]:
    """Fetch and parse an RSS/Atom feed respecting SSRF, robots.txt, and rate limits."""
    allowed, delay = await is_allowed_by_robots(url)
    if not allowed:
        logger.info("Skipping RSS feed %s (disallowed by robots.txt)", url)
        return []
    await respect_rate_limit(url, min_delay=delay)
    resp = await safe_get(url, timeout=15.0, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    return parse_feed_xml(resp.text, source_name=source_name)
