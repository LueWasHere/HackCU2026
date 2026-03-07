import asyncio
import re
from urllib.parse import quote


class HackathonCrawler:
    """All web crawling via Crawl4AI — no paid search APIs."""

    async def _crawl_url(self, url: str, timeout: int = 30) -> dict:
        """Crawl a single URL and return markdown + links."""
        try:
            from crawl4ai import AsyncWebCrawler

            async with AsyncWebCrawler(verbose=False) as crawler:
                result = await crawler.arun(
                    url=url,
                    bypass_cache=True,
                    page_timeout=timeout * 1000,
                    word_count_threshold=5,
                )
                if result.success:
                    content = result.markdown or ""
                    # Extract external links
                    raw_links = result.links or {}
                    external = [
                        l.get("href", "")
                        for l in raw_links.get("external", [])
                        if l.get("href", "").startswith("http")
                    ]
                    internal = [
                        l.get("href", "")
                        for l in raw_links.get("internal", [])
                        if l.get("href", "").startswith("http")
                    ]
                    return {
                        "content": content[:10000],
                        "external_links": external[:30],
                        "internal_links": internal[:20],
                        "url": url,
                        "success": True,
                    }
        except Exception as e:
            return {"content": "", "external_links": [], "internal_links": [], "url": url, "success": False, "error": str(e)}
        return {"content": "", "external_links": [], "internal_links": [], "url": url, "success": False}

    async def _search_duckduckgo(self, query: str, max_results: int = 6) -> list[str]:
        """Use DuckDuckGo HTML search to get result URLs — no API key needed."""
        search_url = f"https://html.duckduckgo.com/html/?q={quote(query)}"
        try:
            result = await self._crawl_url(search_url, timeout=20)
            links = result.get("external_links", [])
            # Filter out DDG tracking/internal links
            filtered = [
                l for l in links
                if "duckduckgo.com" not in l
                and "duck.com" not in l
                and l.startswith("http")
            ]
            return filtered[:max_results]
        except Exception:
            return []

    async def crawl_hackathon_page(self, url: str) -> str:
        """Crawl the main hackathon page. Handles both Devpost and generic pages."""
        result = await self._crawl_url(url, timeout=45)
        content = result.get("content", "")

        # For Devpost, also try the judges tab URL pattern
        if "devpost.com" in url:
            base = url.rstrip("/")
            extra_pages = [
                f"{base}#judges",
                f"{base}/details",
            ]
            tasks = [self._crawl_url(u, timeout=20) for u in extra_pages]
            extras = await asyncio.gather(*tasks, return_exceptions=True)
            for r in extras:
                if isinstance(r, dict) and r.get("content"):
                    content += "\n\n" + r["content"][:3000]

        return content

    async def worker_linkedin(self, name: str, company: str = "", title: str = "") -> dict:
        """Worker 1: Find and crawl LinkedIn profile."""
        results = []
        sources = []

        # Search for LinkedIn profile
        query = f'"{name}" {company} site:linkedin.com/in'
        urls = await self._search_duckduckgo(query, max_results=4)

        # Also try direct name-based URL construction
        slug = re.sub(r"[^a-z0-9]", "-", name.lower()).strip("-")
        direct_urls = [
            f"https://www.linkedin.com/in/{slug}/",
            f"https://www.linkedin.com/in/{slug.replace('-', '')}/",
        ]

        all_urls = list(dict.fromkeys(urls + direct_urls))  # deduplicate, preserve order

        for url in all_urls[:3]:
            if "linkedin.com/in/" in url:
                data = await self._crawl_url(url, timeout=20)
                if data.get("content") and len(data["content"]) > 100:
                    results.append(data["content"][:3000])
                    sources.append(url)

        return {
            "source": "linkedin",
            "content": "\n\n---\n\n".join(results),
            "sources": sources,
        }

    async def worker_github_personal(self, name: str, company: str = "") -> dict:
        """Worker 2: Find GitHub profile and personal website/blog."""
        results = []
        sources = []

        # GitHub search
        gh_query = f'"{name}" github.com developer'
        gh_urls = await self._search_duckduckgo(gh_query, max_results=4)

        for url in gh_urls[:2]:
            if "github.com/" in url and url.count("/") <= 4:
                data = await self._crawl_url(url, timeout=20)
                if data.get("content") and len(data["content"]) > 100:
                    results.append(f"[GitHub]\n{data['content'][:2500]}")
                    sources.append(url)

        # Personal website / blog
        site_query = f'"{name}" {company} personal website portfolio about'
        site_urls = await self._search_duckduckgo(site_query, max_results=5)

        for url in site_urls[:3]:
            skip = any(x in url for x in ["linkedin.com", "github.com", "twitter.com", "facebook.com", "duckduckgo.com"])
            if not skip:
                data = await self._crawl_url(url, timeout=20)
                if data.get("content") and len(data["content"]) > 150:
                    results.append(f"[Personal Site: {url}]\n{data['content'][:2500]}")
                    sources.append(url)
                    break  # One good personal site is enough

        return {
            "source": "github_personal",
            "content": "\n\n---\n\n".join(results),
            "sources": sources,
        }

    async def worker_news_papers_talks(self, name: str, company: str = "") -> dict:
        """Worker 3: Find news articles, papers, talks, and interviews."""
        results = []
        sources = []

        queries = [
            f'"{name}" {company} interview talk keynote podcast',
            f'"{name}" research paper arxiv publication',
            f'"{name}" {company} techcrunch wired forbes medium article',
        ]

        all_urls: list[str] = []
        for query in queries:
            urls = await self._search_duckduckgo(query, max_results=4)
            all_urls.extend(urls)

        # Deduplicate and filter
        seen = set()
        unique_urls = []
        for url in all_urls:
            skip = any(x in url for x in ["linkedin.com", "duckduckgo.com", "twitter.com"])
            if url not in seen and not skip:
                seen.add(url)
                unique_urls.append(url)

        # Crawl top results concurrently
        tasks = [self._crawl_url(u, timeout=18) for u in unique_urls[:6]]
        crawled = await asyncio.gather(*tasks, return_exceptions=True)

        for url, r in zip(unique_urls[:6], crawled):
            if isinstance(r, dict) and r.get("content") and len(r["content"]) > 200:
                results.append(f"[Source: {url}]\n{r['content'][:2000]}")
                sources.append(url)

        return {
            "source": "news_papers_talks",
            "content": "\n\n---\n\n".join(results[:4]),
            "sources": sources[:4],
        }
