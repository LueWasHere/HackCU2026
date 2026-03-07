import asyncio
import re
import logging
from urllib.parse import parse_qs, quote, unquote, urlparse, urlunparse

logger = logging.getLogger(__name__)

# Max concurrent Playwright pages sharing the single browser
_CRAWL_SEMAPHORE = asyncio.Semaphore(12)

_EMPTY_NAMES = {"unknown", "n/a", "none", "", "tbd", "tba"}
_TRAILING_URL_PUNCT = ".,;:!?)]}\"'`"


def _clean(val: str) -> str:
    """Return empty string if val looks like a placeholder."""
    return "" if (val or "").strip().lower() in _EMPTY_NAMES else val.strip()


def _name_tokens(name: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", (name or "").lower()) if len(t) > 1]


def _slug_tokens_from_linkedin(url: str) -> list[str]:
    try:
        parsed = urlparse(url or "")
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2 and parts[0] in {"in", "pub"}:
            return _name_tokens(parts[1].replace("%20", "-"))
    except Exception:
        return []
    return []


def _normalize_candidate_url(url: str) -> str:
    if not url:
        return ""
    cleaned = (url or "").strip()
    while cleaned and cleaned[-1] in _TRAILING_URL_PUNCT:
        cleaned = cleaned[:-1]
    if not cleaned:
        return ""
    try:
        parsed = urlparse(cleaned)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ""
        netloc = (parsed.netloc or "").rstrip(_TRAILING_URL_PUNCT)
        if not netloc:
            return ""
        rebuilt = parsed._replace(netloc=netloc)
        final = urlunparse(rebuilt).strip()
        while final and final[-1] in _TRAILING_URL_PUNCT:
            final = final[:-1]
        return final
    except Exception:
        return ""


def _is_linkedin_profile_url(url: str) -> bool:
    parsed = urlparse(url or "")
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").lower()
    return "linkedin.com" in host and (path.startswith("/in/") or path.startswith("/pub/"))


def _normalize_linkedin_url(url: str) -> str:
    if not _is_linkedin_profile_url(url):
        return ""
    parsed = urlparse(url.strip())
    clean = parsed._replace(query="", fragment="")
    normalized = urlunparse(clean).rstrip("/")
    return normalized + "/"


def _extract_linkedin_photo(markdown: str) -> str:
    if not markdown:
        return ""
    photo_match = re.search(
        r'!\[.*?\]\((https://media\.licdn\.com/dms/image/[^)]+)\)',
        markdown,
    )
    if photo_match:
        return photo_match.group(1)
    photo_match = re.search(
        r'(https://media\.licdn\.com/dms/image/[^\s"\'<>]+)',
        markdown,
    )
    if photo_match:
        return photo_match.group(1)
    return ""


def _decode_ddg_redirect(url: str) -> str:
    """Decode DuckDuckGo redirect URLs into their target destination."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        host = (parsed.netloc or "").lower()
        if "duckduckgo.com" in host or "duck.com" in host:
            q = parse_qs(parsed.query)
            uddg = q.get("uddg", [])
            if uddg and uddg[0]:
                return unquote(uddg[0]).strip()
    except Exception:
        return ""
    return ""


def _looks_like_search_result_url(url: str) -> bool:
    if not url:
        return False
    try:
        p = urlparse(url)
    except Exception:
        return False
    host = (p.netloc or "").lower()
    if p.scheme not in {"http", "https"}:
        return False
    blocked = ("duckduckgo.com", "duck.com", "google.com", "bing.com", "yahoo.com")
    return not any(b in host for b in blocked)


def _collect_search_targets(crawl_result: dict, max_results: int) -> list[str]:
    raw = []
    raw.extend(crawl_result.get("external_links", []))
    raw.extend(crawl_result.get("internal_links", []))
    markdown = crawl_result.get("content", "")
    raw.extend(re.findall(r'https?://[^\s)\]"\']+', markdown))

    out = []
    seen = set()
    for link in raw:
        direct = _decode_ddg_redirect(link) or link
        normalized = _normalize_candidate_url(direct)
        if not normalized:
            continue
        if not _looks_like_search_result_url(normalized):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
        if len(out) >= max_results:
            break
    return out


def _score_linkedin_candidate(url: str, content: str, name: str, company: str, title: str) -> int:
    score = 0
    url_l = (url or "").lower()
    content_l = (content or "").lower()

    if "/in/" in url_l:
        score += 25
    elif "/pub/" in url_l:
        score += 12
    else:
        score -= 40

    name_toks = _name_tokens(name)
    slug_toks = _slug_tokens_from_linkedin(url_l)
    for tok in name_toks:
        if tok in slug_toks:
            score += 20
        elif tok in url_l:
            score += 12
        if tok in content_l:
            score += 4

    if len(name_toks) >= 2 and all(tok in slug_toks for tok in name_toks[:2]):
        score += 20

    # Penalize ambiguous slugs (same first name, wrong/no last name match).
    if len(name_toks) >= 2 and name_toks[-1] not in slug_toks:
        score -= 18

    company_hits = 0
    title_hits = 0
    for tok in _name_tokens(company)[:4]:
        if tok in content_l:
            company_hits += 1
            score += 5

    for tok in _name_tokens(title)[:4]:
        if tok in content_l:
            title_hits += 1
            score += 4

    if company and company_hits == 0:
        score -= 16
    if title and title_hits == 0:
        score -= 8

    # Typical marker of heavily blocked/empty LI pages.
    if "linkedin member" in content_l and len(content_l) < 500:
        score -= 12

    if len(content_l) < 150:
        score -= 8

    return score


def _is_linkedin_candidate_valid(url: str, content: str, name: str, company: str, title: str, score: int) -> bool:
    if score < 45:
        return False
    name_toks = _name_tokens(name)
    slug_toks = _slug_tokens_from_linkedin(url)
    content_l = (content or "").lower()

    if len(name_toks) >= 2:
        # Require both first+last in slug, or explicit full-name evidence in page content.
        full_name = " ".join(name_toks[:2])
        has_full_name_evidence = full_name in content_l
        has_slug_name_match = all(tok in slug_toks for tok in name_toks[:2])
        if not (has_full_name_evidence or has_slug_name_match):
            return False

    company_toks = _name_tokens(company)[:4]
    title_toks = _name_tokens(title)[:4]
    has_company = any(tok in content_l for tok in company_toks) if company_toks else True
    has_title = any(tok in content_l for tok in title_toks) if title_toks else True

    # If we know both company and title, insist at least one is visible in content.
    if company_toks and title_toks and not (has_company or has_title):
        return False
    return True


class HackathonCrawler:
    """All web crawling via Crawl4AI — reuses a single browser instance."""

    def __init__(self):
        self._crawler = None
        self._lock = asyncio.Lock()

    async def _get_crawler(self):
        """Lazy-init a single AsyncWebCrawler (one Playwright browser)."""
        if self._crawler is None:
            async with self._lock:
                if self._crawler is None:
                    from crawl4ai import AsyncWebCrawler
                    self._crawler = AsyncWebCrawler(verbose=False)
                    await self._crawler.__aenter__()
        return self._crawler

    async def close(self):
        if self._crawler is not None:
            try:
                await self._crawler.__aexit__(None, None, None)
            except Exception:
                pass
            self._crawler = None

    async def _crawl_url(self, url: str, timeout: int = 30, wait_for: str = None,
                         js_code: str = None, content_limit: int = 10000) -> dict:
        """Crawl a single URL using the shared browser, gated by semaphore."""
        async with _CRAWL_SEMAPHORE:
            try:
                crawler = await self._get_crawler()
                kwargs = dict(
                    url=url,
                    bypass_cache=True,
                    page_timeout=timeout * 1000,
                    word_count_threshold=5,
                )
                if wait_for:
                    kwargs["wait_for"] = wait_for
                if js_code:
                    kwargs["js_code"] = js_code

                result = await crawler.arun(**kwargs)
                if result.success:
                    content = result.markdown or ""
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
                        "content": content[:content_limit],
                        "external_links": external[:30],
                        "internal_links": internal[:20],
                        "url": url,
                        "success": True,
                    }
            except Exception as e:
                return {"content": "", "external_links": [], "internal_links": [],
                        "url": url, "success": False, "error": str(e)}
        return {"content": "", "external_links": [], "internal_links": [],
                "url": url, "success": False}

    async def _search_duckduckgo(self, query: str, max_results: int = 6) -> list[str]:
        """Use DuckDuckGo HTML search to get result URLs — no API key needed."""
        search_url = f"https://html.duckduckgo.com/html/?q={quote(query)}"
        mirror_url = f"https://r.jina.ai/http://html.duckduckgo.com/html/?q={quote(query)}"
        try:
            result = await self._crawl_url(search_url, timeout=20)
            out = _collect_search_targets(result, max_results)
            used_fallback = False

            # DDG often returns anti-bot pages to direct crawlers. Use r.jina mirror fallback.
            if not out:
                used_fallback = True
                mirrored = await self._crawl_url(mirror_url, timeout=30, content_limit=20000)
                out = _collect_search_targets(mirrored, max_results)

            logger.info(f"[ddg] query='{query[:80]}' results={len(out)} (fallback={'yes' if used_fallback else 'no'})")
            return out
        except Exception:
            return []

    async def crawl_hackathon_page(self, url: str) -> str:
        """Crawl the main hackathon page. Handles both Devpost and generic pages."""
        if "devpost.com" in url:
            js_scroll = """
            await new Promise(r => {
                let total = 0;
                const step = () => {
                    window.scrollBy(0, 600);
                    total += 600;
                    if (total < document.body.scrollHeight) {
                        setTimeout(step, 150);
                    } else {
                        setTimeout(r, 1500);
                    }
                };
                step();
            });
            """
            main_task = self._crawl_url(
                url, timeout=60, js_code=js_scroll, content_limit=30000,
            )
            base = url.rstrip("/")
            details_task = self._crawl_url(
                f"{base}/details", timeout=20, content_limit=8000,
            )
            main_result, details_result = await asyncio.gather(
                main_task, details_task, return_exceptions=True,
            )

            content = ""
            if isinstance(main_result, dict):
                content = main_result.get("content", "")
            if isinstance(details_result, dict) and details_result.get("content"):
                content += "\n\n" + details_result["content"]
        else:
            result = await self._crawl_url(url, timeout=45, content_limit=20000)
            content = result.get("content", "")

        return content

    # ── Worker 1: LinkedIn ────────────────────────────────────────────────

    async def worker_linkedin(self, name: str, company: str = "", title: str = "") -> dict:
        """Find and crawl LinkedIn profile."""
        name, company, title = _clean(name), _clean(company), _clean(title)
        if not name:
            return {"source": "linkedin", "content": "", "sources": [], "linkedin_url": "", "photo_url": ""}
        results = []
        candidates = []

        query = f'"{name}" {title} {company} site:linkedin.com/in'
        urls = await self._search_duckduckgo(query, max_results=4)

        slug = re.sub(r"[^a-z0-9]", "-", name.lower()).strip("-")
        direct_urls = [
            f"https://www.linkedin.com/in/{slug}/",
            f"https://www.linkedin.com/in/{slug.replace('-', '')}/",
        ]

        all_urls = list(dict.fromkeys(urls + direct_urls))
        linkedin_urls = [u for u in all_urls[:5] if _is_linkedin_profile_url(u)]
        tasks = [self._crawl_url(u, timeout=20, content_limit=5000) for u in linkedin_urls]
        crawled = await asyncio.gather(*tasks, return_exceptions=True)

        for u, data in zip(linkedin_urls, crawled):
            if not isinstance(data, dict):
                continue
            content = data.get("content", "")
            if len(content) < 100:
                continue
            normalized_url = _normalize_linkedin_url(u)
            if not normalized_url:
                continue
            photo_url = _extract_linkedin_photo(content)
            score = _score_linkedin_candidate(normalized_url, content, name, company, title)
            if not _is_linkedin_candidate_valid(normalized_url, content, name, company, title, score):
                logger.info(f"[linkedin] rejected candidate for '{name}': {normalized_url} score={score}")
                continue
            candidates.append({
                "url": normalized_url,
                "content": content[:5000],
                "photo_url": photo_url,
                "score": score,
            })

        candidates.sort(key=lambda x: x["score"], reverse=True)
        top = candidates[0] if candidates else None
        sources = [c["url"] for c in candidates[:3]]
        for c in candidates[:2]:
            results.append(c["content"])

        return {
            "source": "linkedin",
            "content": "\n\n---\n\n".join(results),
            "sources": sources,
            "linkedin_url": top["url"] if top else "",
            "photo_url": top["photo_url"] if top else "",
        }

    # ── Worker 2: GitHub + personal site ──────────────────────────────────

    async def worker_github_personal(self, name: str, company: str = "") -> dict:
        """Find GitHub profile, repos, and personal website/blog."""
        name, company = _clean(name), _clean(company)
        if not name:
            return {"source": "github_personal", "content": "", "sources": []}
        results = []
        sources = []

        gh_query = f'"{name}" site:github.com'
        site_query = f'"{name}" {company} personal website portfolio about'
        gh_urls_task = self._search_duckduckgo(gh_query, max_results=5)
        site_urls_task = self._search_duckduckgo(site_query, max_results=5)
        gh_urls, site_urls = await asyncio.gather(gh_urls_task, site_urls_task)

        crawl_targets = []

        # GitHub profile pages
        for url in gh_urls[:3]:
            if "github.com/" in url and url.count("/") <= 4:
                crawl_targets.append(("github_profile", url))

        # GitHub repos (deeper links — shows what they actually build)
        for url in gh_urls:
            if "github.com/" in url and url.count("/") == 4:
                crawl_targets.append(("github_repo", url))
                if len([t for t in crawl_targets if t[0] == "github_repo"]) >= 2:
                    break

        # Personal sites
        skip_domains = ["linkedin.com", "github.com", "twitter.com", "facebook.com",
                        "duckduckgo.com", "instagram.com", "youtube.com"]
        for url in site_urls[:4]:
            if not any(x in url for x in skip_domains):
                crawl_targets.append(("site", url))
                if len([t for t in crawl_targets if t[0] == "site"]) >= 2:
                    break

        tasks = [self._crawl_url(u, timeout=20, content_limit=4000) for _, u in crawl_targets]
        crawled = await asyncio.gather(*tasks, return_exceptions=True)

        for (kind, url), data in zip(crawl_targets, crawled):
            if isinstance(data, dict) and data.get("content") and len(data["content"]) > 100:
                label = {"github_profile": "GitHub Profile",
                         "github_repo": "GitHub Repo",
                         "site": f"Personal Site"}[kind]
                results.append(f"[{label}: {url}]\n{data['content'][:4000]}")
                sources.append(url)

        return {
            "source": "github_personal",
            "content": "\n\n---\n\n".join(results),
            "sources": sources,
        }

    # ── Worker 3: News, interviews, podcasts ──────────────────────────────

    async def worker_news_interviews(self, name: str, company: str = "") -> dict:
        """Find news articles, interviews, podcasts, and press mentions."""
        name, company = _clean(name), _clean(company)
        if not name:
            return {"source": "news_interviews", "content": "", "sources": []}
        results = []
        sources = []

        queries = [
            f'"{name}" {company} interview podcast',
            f'"{name}" {company} techcrunch wired forbes verge article',
            f'"{name}" keynote talk conference speaker',
        ]

        search_tasks = [self._search_duckduckgo(q, max_results=5) for q in queries]
        search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

        all_urls: list[str] = []
        for r in search_results:
            if isinstance(r, list):
                all_urls.extend(r)

        seen = set()
        unique_urls = []
        skip_domains = ["linkedin.com", "duckduckgo.com", "twitter.com", "x.com",
                        "facebook.com", "instagram.com"]
        for url in all_urls:
            if url not in seen and not any(x in url for x in skip_domains):
                seen.add(url)
                unique_urls.append(url)

        tasks = [self._crawl_url(u, timeout=18, content_limit=4000) for u in unique_urls[:8]]
        crawled = await asyncio.gather(*tasks, return_exceptions=True)

        for url, r in zip(unique_urls[:8], crawled):
            if isinstance(r, dict) and r.get("content") and len(r["content"]) > 200:
                results.append(f"[News/Interview: {url}]\n{r['content'][:4000]}")
                sources.append(url)

        return {
            "source": "news_interviews",
            "content": "\n\n---\n\n".join(results[:5]),
            "sources": sources[:5],
        }

    # ── Worker 4: Twitter/X + social media ────────────────────────────────

    async def worker_social_media(self, name: str, company: str = "") -> dict:
        """Find Twitter/X, Mastodon, Bluesky, and other social profiles."""
        name, company = _clean(name), _clean(company)
        if not name:
            return {"source": "social_media", "content": "", "sources": []}
        results = []
        sources = []

        queries = [
            f'"{name}" {company} site:twitter.com OR site:x.com',
            f'"{name}" {company} site:medium.com OR site:substack.com OR site:dev.to',
            f'"{name}" {company} site:mastodon.social OR site:bsky.app',
        ]

        search_tasks = [self._search_duckduckgo(q, max_results=4) for q in queries]
        search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

        all_urls: list[str] = []
        for r in search_results:
            if isinstance(r, list):
                all_urls.extend(r)

        seen = set()
        unique_urls = []
        for url in all_urls:
            if url not in seen and "duckduckgo.com" not in url:
                seen.add(url)
                unique_urls.append(url)

        tasks = [self._crawl_url(u, timeout=20, content_limit=5000) for u in unique_urls[:6]]
        crawled = await asyncio.gather(*tasks, return_exceptions=True)

        for url, r in zip(unique_urls[:6], crawled):
            if isinstance(r, dict) and r.get("content") and len(r["content"]) > 100:
                platform = "Twitter/X" if ("twitter.com" in url or "x.com" in url) else \
                           "Medium" if "medium.com" in url else \
                           "Substack" if "substack.com" in url else \
                           "Dev.to" if "dev.to" in url else \
                           "Social"
                results.append(f"[{platform}: {url}]\n{r['content'][:5000]}")
                sources.append(url)

        return {
            "source": "social_media",
            "content": "\n\n---\n\n".join(results[:4]),
            "sources": sources[:4],
        }

    # ── Worker 5: Academic papers + Google Scholar ─────────────────────────

    async def worker_academic(self, name: str, company: str = "") -> dict:
        """Find research papers, Google Scholar, patents, and academic work."""
        name, company = _clean(name), _clean(company)
        if not name:
            return {"source": "academic", "content": "", "sources": []}
        results = []
        sources = []

        queries = [
            f'"{name}" site:scholar.google.com',
            f'"{name}" research paper arxiv publication proceedings',
            f'"{name}" {company} patent invention',
            f'"{name}" site:researchgate.net OR site:semanticscholar.org',
        ]

        search_tasks = [self._search_duckduckgo(q, max_results=4) for q in queries]
        search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

        all_urls: list[str] = []
        for r in search_results:
            if isinstance(r, list):
                all_urls.extend(r)

        seen = set()
        unique_urls = []
        skip_domains = ["duckduckgo.com", "linkedin.com", "twitter.com"]
        for url in all_urls:
            if url not in seen and not any(x in url for x in skip_domains):
                seen.add(url)
                unique_urls.append(url)

        tasks = [self._crawl_url(u, timeout=20, content_limit=4000) for u in unique_urls[:6]]
        crawled = await asyncio.gather(*tasks, return_exceptions=True)

        for url, r in zip(unique_urls[:6], crawled):
            if isinstance(r, dict) and r.get("content") and len(r["content"]) > 150:
                label = "Google Scholar" if "scholar.google" in url else \
                        "ArXiv" if "arxiv.org" in url else \
                        "ResearchGate" if "researchgate" in url else \
                        "Semantic Scholar" if "semanticscholar" in url else \
                        "Academic"
                results.append(f"[{label}: {url}]\n{r['content'][:4000]}")
                sources.append(url)

        return {
            "source": "academic",
            "content": "\n\n---\n\n".join(results[:4]),
            "sources": sources[:4],
        }

    # ── Worker 6: Company / startup / investment info ─────────────────────

    async def worker_company(self, name: str, company: str = "", title: str = "") -> dict:
        """Research their company — Crunchbase, company website, funding, products."""
        name, company, title = _clean(name), _clean(company), _clean(title)
        results = []
        sources = []

        if not company:
            return {"source": "company", "content": "", "sources": []}

        queries = [
            f'"{company}" about company products',
            f'"{company}" site:crunchbase.com OR site:pitchbook.com',
            f'"{company}" funding raised investors startup',
            f'"{name}" "{company}" founded co-founder CEO CTO',
        ]

        search_tasks = [self._search_duckduckgo(q, max_results=4) for q in queries]
        search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

        all_urls: list[str] = []
        for r in search_results:
            if isinstance(r, list):
                all_urls.extend(r)

        seen = set()
        unique_urls = []
        skip_domains = ["duckduckgo.com", "linkedin.com", "twitter.com", "facebook.com"]
        for url in all_urls:
            if url not in seen and not any(x in url for x in skip_domains):
                seen.add(url)
                unique_urls.append(url)

        tasks = [self._crawl_url(u, timeout=20, content_limit=4000) for u in unique_urls[:6]]
        crawled = await asyncio.gather(*tasks, return_exceptions=True)

        for url, r in zip(unique_urls[:6], crawled):
            if isinstance(r, dict) and r.get("content") and len(r["content"]) > 150:
                label = "Crunchbase" if "crunchbase.com" in url else \
                        "PitchBook" if "pitchbook.com" in url else \
                        "Company Info"
                results.append(f"[{label}: {url}]\n{r['content'][:4000]}")
                sources.append(url)

        return {
            "source": "company",
            "content": "\n\n---\n\n".join(results[:4]),
            "sources": sources[:4],
        }

    # ── Worker 7: YouTube / conference talks / slides ─────────────────────

    async def worker_talks_videos(self, name: str, company: str = "") -> dict:
        """Find YouTube talks, conference presentations, SlideShare decks."""
        name, company = _clean(name), _clean(company)
        if not name:
            return {"source": "talks_videos", "content": "", "sources": []}
        results = []
        sources = []

        queries = [
            f'"{name}" site:youtube.com talk OR keynote OR presentation',
            f'"{name}" {company} site:slideshare.net OR site:speakerdeck.com',
            f'"{name}" conference speaker presentation demo',
        ]

        search_tasks = [self._search_duckduckgo(q, max_results=4) for q in queries]
        search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

        all_urls: list[str] = []
        for r in search_results:
            if isinstance(r, list):
                all_urls.extend(r)

        seen = set()
        unique_urls = []
        skip_domains = ["duckduckgo.com", "linkedin.com"]
        for url in all_urls:
            if url not in seen and not any(x in url for x in skip_domains):
                seen.add(url)
                unique_urls.append(url)

        tasks = [self._crawl_url(u, timeout=20, content_limit=4000) for u in unique_urls[:6]]
        crawled = await asyncio.gather(*tasks, return_exceptions=True)

        for url, r in zip(unique_urls[:6], crawled):
            if isinstance(r, dict) and r.get("content") and len(r["content"]) > 100:
                label = "YouTube" if "youtube.com" in url or "youtu.be" in url else \
                        "SlideShare" if "slideshare" in url else \
                        "SpeakerDeck" if "speakerdeck" in url else \
                        "Talk/Presentation"
                results.append(f"[{label}: {url}]\n{r['content'][:4000]}")
                sources.append(url)

        return {
            "source": "talks_videos",
            "content": "\n\n---\n\n".join(results[:4]),
            "sources": sources[:4],
        }

    # ── Worker 8: YouTube transcripts ─────────────────────────────────────

    async def worker_youtube_transcripts(self, name: str, company: str = "") -> dict:
        """Find YouTube videos featuring this person and pull transcripts."""
        name, company = _clean(name), _clean(company)
        if not name:
            return {"source": "youtube_transcripts", "content": "", "sources": []}
        results = []
        sources = []

        # Search for videos they appear in (interviews, panels, talks)
        queries = [
            f'"{name}" site:youtube.com',
            f'"{name}" {company} youtube interview OR panel OR talk OR podcast',
        ]

        search_tasks = [self._search_duckduckgo(q, max_results=5) for q in queries]
        search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

        all_urls: list[str] = []
        for r in search_results:
            if isinstance(r, list):
                all_urls.extend(r)

        # Deduplicate, keep only youtube URLs
        seen = set()
        yt_urls = []
        for url in all_urls:
            # Normalize youtube URLs to get video IDs
            vid_id = self._extract_yt_id(url)
            if vid_id and vid_id not in seen:
                seen.add(vid_id)
                yt_urls.append((vid_id, url))

        # For each video, try to get the transcript via a free transcript service
        for vid_id, original_url in yt_urls[:4]:
            transcript = await self._get_youtube_transcript(vid_id)
            if transcript and len(transcript) > 100:
                # Also crawl the video page for title/description
                page_data = await self._crawl_url(original_url, timeout=15, content_limit=2000)
                page_info = page_data.get("content", "")[:500] if isinstance(page_data, dict) else ""

                results.append(
                    f"[YouTube Video: {original_url}]\n"
                    f"Page Info: {page_info}\n\n"
                    f"TRANSCRIPT (first 5000 chars):\n{transcript[:5000]}"
                )
                sources.append(original_url)
                logger.info(f"[yt_transcript] {name}: got transcript for {vid_id} ({len(transcript)} chars)")
            else:
                logger.info(f"[yt_transcript] {name}: no transcript for {vid_id}")

        return {
            "source": "youtube_transcripts",
            "content": "\n\n---\n\n".join(results[:3]),
            "sources": sources[:3],
        }

    def _extract_yt_id(self, url: str) -> str:
        """Extract YouTube video ID from various URL formats."""
        if not url:
            return ""
        # youtube.com/watch?v=ID
        match = re.search(r"[?&]v=([a-zA-Z0-9_-]{11})", url)
        if match:
            return match.group(1)
        # youtu.be/ID
        match = re.search(r"youtu\.be/([a-zA-Z0-9_-]{11})", url)
        if match:
            return match.group(1)
        # youtube.com/embed/ID
        match = re.search(r"youtube\.com/embed/([a-zA-Z0-9_-]{11})", url)
        if match:
            return match.group(1)
        return ""

    async def _get_youtube_transcript(self, video_id: str) -> str:
        """Try multiple free methods to get a YouTube transcript."""
        # Method 1: Use a free transcript proxy site
        transcript_urls = [
            f"https://youtubetranscript.com/?v={video_id}",
            f"https://www.youtube.com/watch?v={video_id}",
        ]

        for url in transcript_urls:
            data = await self._crawl_url(url, timeout=20, content_limit=8000)
            if isinstance(data, dict) and data.get("content"):
                content = data["content"]
                # If it looks like it has transcript-like content (lots of text lines)
                if len(content) > 200:
                    return content

        return ""

    # ── Worker 10: Podcasts, Hacker News, blogs ─────────────────────────

    async def worker_podcasts_hn(self, name: str, company: str = "") -> dict:
        """Find podcast appearances, Hacker News mentions, and blog posts."""
        name, company = _clean(name), _clean(company)
        if not name:
            return {"source": "podcasts_hn", "content": "", "sources": []}
        results = []
        sources = []

        queries = [
            f'"{name}" site:news.ycombinator.com',
            f'"{name}" {company} podcast episode guest',
            f'"{name}" site:hashnode.dev OR site:blog OR site:wordpress.com',
            f'"{name}" {company} site:producthunt.com OR site:angellist.com',
        ]

        search_tasks = [self._search_duckduckgo(q, max_results=4) for q in queries]
        search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

        all_urls: list[str] = []
        for r in search_results:
            if isinstance(r, list):
                all_urls.extend(r)

        seen = set()
        unique_urls = []
        skip_domains = ["duckduckgo.com", "linkedin.com", "twitter.com", "facebook.com"]
        for url in all_urls:
            if url not in seen and not any(x in url for x in skip_domains):
                seen.add(url)
                unique_urls.append(url)

        tasks = [self._crawl_url(u, timeout=18, content_limit=4000) for u in unique_urls[:8]]
        crawled = await asyncio.gather(*tasks, return_exceptions=True)

        for url, r in zip(unique_urls[:8], crawled):
            if isinstance(r, dict) and r.get("content") and len(r["content"]) > 150:
                label = "Hacker News" if "ycombinator.com" in url else \
                        "Product Hunt" if "producthunt.com" in url else \
                        "Podcast" if any(x in url.lower() for x in ["podcast", "episode", "spotify", "apple.co"]) else \
                        "Blog/Post"
                results.append(f"[{label}: {url}]\n{r['content'][:4000]}")
                sources.append(url)

        return {
            "source": "podcasts_hn",
            "content": "\n\n---\n\n".join(results[:5]),
            "sources": sources[:5],
        }

    # ── Worker 9: General web search (catch-all) ──────────────────────────

    async def worker_general_search(self, name: str, company: str = "",
                                     title: str = "", already_seen: set = None) -> dict:
        """Broad web search to catch anything the other workers missed."""
        name, company, title = _clean(name), _clean(company), _clean(title)
        if not name:
            return {"source": "general_search", "content": "", "sources": []}
        if already_seen is None:
            already_seen = set()

        results = []
        sources = []

        # Cast a wide net with different query angles
        query_parts = [name]
        if company:
            query_parts.append(company)
        if title:
            query_parts.append(title)

        queries = [
            f'"{name}" {company} {title}',
            f'"{name}" bio about profile',
            f'"{name}" {company} blog OR opinion OR perspective',
        ]

        search_tasks = [self._search_duckduckgo(q, max_results=6) for q in queries]
        search_results = await asyncio.gather(*search_tasks, return_exceptions=True)

        all_urls: list[str] = []
        for r in search_results:
            if isinstance(r, list):
                all_urls.extend(r)

        # Deduplicate and filter out URLs we've already crawled in other workers
        seen = set()
        skip_domains = ["duckduckgo.com", "duck.com", "google.com", "bing.com",
                        "yahoo.com", "facebook.com", "instagram.com"]
        new_urls = []
        for url in all_urls:
            domain = urlparse(url).netloc
            # Skip if we've seen this exact URL or domain was already covered
            if url in seen or url in already_seen:
                continue
            if any(x in url for x in skip_domains):
                continue
            seen.add(url)
            new_urls.append(url)

        if not new_urls:
            return {"source": "general_search", "content": "", "sources": []}

        logger.info(f"[general_search] {name}: found {len(new_urls)} new URLs not seen by other workers")

        tasks = [self._crawl_url(u, timeout=18, content_limit=4000) for u in new_urls[:8]]
        crawled = await asyncio.gather(*tasks, return_exceptions=True)

        for url, r in zip(new_urls[:8], crawled):
            if isinstance(r, dict) and r.get("content") and len(r["content"]) > 150:
                domain = urlparse(url).netloc
                results.append(f"[{domain}: {url}]\n{r['content'][:4000]}")
                sources.append(url)

        return {
            "source": "general_search",
            "content": "\n\n---\n\n".join(results[:5]),
            "sources": sources[:5],
        }
