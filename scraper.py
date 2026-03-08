"""
scraper.py – async Crawl4AI scraping pipeline.

• scrape_hackathon(url)   → raw content + links + images
• research_judge(judge, log_fn) → deep web research on a judge

Uses a shared browser session for efficiency.
"""

import asyncio
import re
from urllib.parse import quote_plus

# ── Crawl4AI import with version compat ─────────────────────────────────────
_crawler_instance = None
_crawler_lock = asyncio.Lock()

try:
    from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, CacheMode

    _BROWSER_CFG = BrowserConfig(headless=True, verbose=False)

    async def _get_crawler():
        """Get or create a shared crawler instance (reuses browser)."""
        global _crawler_instance
        if _crawler_instance is None:
            _crawler_instance = AsyncWebCrawler(config=_BROWSER_CFG)
            await _crawler_instance.__aenter__()
        return _crawler_instance

    async def _fetch(url: str, timeout: int = 30) -> dict:
        cfg = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            page_timeout=timeout * 1000,
            wait_until="domcontentloaded",
            remove_overlay_elements=True,
        )
        crawler = await _get_crawler()
        r = await crawler.arun(url=url, config=cfg)
        return {
            "success": r.success,
            "markdown": (r.markdown or "")[:60_000],
            "links": (r.links or {}),
            "media": (r.media or {}),
        }

except ImportError:
    # Older crawl4ai API
    from crawl4ai import AsyncWebCrawler  # type: ignore

    async def _get_crawler():
        global _crawler_instance
        if _crawler_instance is None:
            _crawler_instance = AsyncWebCrawler(verbose=False)
            await _crawler_instance.__aenter__()
        return _crawler_instance

    async def _fetch(url: str, timeout: int = 30) -> dict:
        crawler = await _get_crawler()
        r = await crawler.arun(url=url)
        return {
            "success": bool(r.markdown),
            "markdown": (r.markdown or "")[:60_000],
            "links": getattr(r, "links", {}),
            "media": getattr(r, "media", {}),
        }


async def cleanup_crawler():
    """Close the shared browser session."""
    global _crawler_instance
    if _crawler_instance is not None:
        try:
            await _crawler_instance.__aexit__(None, None, None)
        except Exception:
            pass
        _crawler_instance = None


# ── Public helpers ────────────────────────────────────────────────────────

async def scrape_url(url: str, timeout: int = 30) -> str:
    """Return markdown text for a URL, empty string on failure."""
    try:
        result = await _fetch(url, timeout=timeout)
        return result["markdown"]
    except Exception as e:
        print(f"[scraper] error fetching {url}: {e}")
        return ""


async def scrape_hackathon(url: str) -> dict:
    """Crawl the hackathon landing page and return a rich raw dict."""
    try:
        result = await _fetch(url, timeout=40)
    except Exception as e:
        print(f"[scraper] hackathon fetch error: {e}")
        return {"url": url, "raw_content": "", "links": [], "images": [], "linkedin_links": []}

    links_raw = result.get("links", {})
    all_links = links_raw.get("internal", []) + links_raw.get("external", [])
    links = [{"href": l.get("href", ""), "text": l.get("text", "")} for l in all_links[:200]]
    linkedin_links = [l for l in links if "linkedin.com/in/" in l.get("href", "")]

    images_raw = result.get("media", {}).get("images", [])[:30]
    images = [{"src": i.get("src", ""), "alt": i.get("alt", "")} for i in images_raw if i.get("src")]

    return {
        "url": url,
        "raw_content": result["markdown"],
        "links": links,
        "images": images,
        "linkedin_links": linkedin_links,
        # Structured fields populated by analyzer later
        "name": "",
        "description": "",
        "prizes": [],
        "tracks": [],
        "timeline": [],
        "judges": [],
        "requirements": [],
        "themes": [],
        "judging_criteria": [],
        "technologies": [],
        "sponsors": [],
    }


async def _duckduckgo_search_urls(query: str, num: int = 5) -> list[str]:
    """Scrape DuckDuckGo HTML search results as a fallback."""
    search_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    try:
        result = await _fetch(search_url, timeout=20)
        markdown = result.get("markdown", "")
        # Extract URLs from markdown links
        urls = re.findall(r'https?://[^\s\)\]"\'<>]+', markdown)
        filtered = []
        for u in urls:
            u = u.rstrip("/.,;:!?)")
            if (
                "duckduckgo.com" not in u
                and "duck.co" not in u
                and len(u) > 20
                and u not in filtered
            ):
                filtered.append(u)
        return filtered[:num]
    except Exception as e:
        print(f"[scraper] duckduckgo search error for '{query}': {e}")
        return []


async def google_search_urls(query: str, num: int = 5) -> list[str]:
    """Scrape Google search results page and extract result URLs.
    Falls back to DuckDuckGo if Google fails."""
    search_url = f"https://www.google.com/search?q={quote_plus(query)}&num={num}"
    try:
        result = await _fetch(search_url, timeout=20)
        links_raw = result.get("links", {})
        all_links = links_raw.get("external", []) + links_raw.get("internal", [])
        urls = []
        for l in all_links:
            href = l.get("href", "")
            if (
                href.startswith("http")
                and "google.com" not in href
                and "gstatic.com" not in href
                and "googleapis.com" not in href
            ):
                urls.append(href)
        urls = list(dict.fromkeys(urls))[:num]  # dedupe
        if urls:
            return urls
    except Exception as e:
        print(f"[scraper] google search error for '{query}': {e}")

    # Fallback to DuckDuckGo
    print(f"[scraper] falling back to DuckDuckGo for '{query}'")
    return await _duckduckgo_search_urls(query, num)


_SOCIAL = {"linkedin.com", "twitter.com", "x.com", "facebook.com",
           "instagram.com", "youtube.com", "tiktok.com"}


async def research_judge(judge: dict, log_fn=None) -> dict:
    """
    Deep research a judge:
      1. LinkedIn (direct URL or searched)
      2. Personal website / blog
      3. Talks & papers
      4. News / interviews
    """

    def _log(msg):
        if log_fn:
            log_fn(msg)
        print(f"[judge] {msg}")

    name = judge.get("name", "").strip()
    title = judge.get("title", "").strip()
    company = judge.get("company", "").strip()

    research = {
        "linkedin_url": judge.get("linkedin_url", ""),
        "linkedin_content": "",
        "website_url": "",
        "website_content": "",
        "papers_talks": [],
        "news": [],
    }

    if not name:
        return research

    # ── 1. LinkedIn ────────────────────────────────────────────────────────
    li_url = judge.get("linkedin_url", "")
    if not li_url:
        _log(f"  🔎 Searching LinkedIn for {name}…")
        urls = await google_search_urls(f"{name} {company} site:linkedin.com/in", 4)
        li_urls = [u for u in urls if "linkedin.com/in/" in u]
        if li_urls:
            li_url = li_urls[0]
            research["linkedin_url"] = li_url

    if li_url:
        _log(f"  🔗 Scraping LinkedIn…")
        content = await scrape_url(li_url, timeout=25)
        research["linkedin_content"] = content[:6_000]

    # ── 2. Personal website / blog ─────────────────────────────────────────
    _log(f"  🌐 Searching for {name}'s website…")
    web_urls = await google_search_urls(f'"{name}" {title} personal site OR blog', 6)
    personal = [u for u in web_urls if not any(s in u for s in _SOCIAL)]
    if personal:
        site_url = personal[0]
        research["website_url"] = site_url
        _log(f"  🌐 Scraping {site_url[:60]}…")
        content = await scrape_url(site_url, timeout=20)
        research["website_content"] = content[:6_000]

    # ── 3. Talks / papers ─────────────────────────────────────────────────
    _log(f"  📚 Searching talks & papers for {name}…")
    talk_urls = await google_search_urls(
        f"{name} {company} talk presentation paper research keynote", 5
    )
    for u in talk_urls[:3]:
        c = await scrape_url(u, timeout=15)
        if c:
            research["papers_talks"].append({"url": u, "content": c[:2_500]})

    # ── 4. News / interviews ───────────────────────────────────────────────
    _log(f"  📰 Searching news for {name}…")
    news_urls = await google_search_urls(f"{name} {company} interview news feature", 4)
    for u in news_urls[:2]:
        c = await scrape_url(u, timeout=15)
        if c:
            research["news"].append({"url": u, "content": c[:2_500]})

    return research
