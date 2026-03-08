import asyncio
import re
import logging
from urllib.parse import parse_qs, quote, unquote, urlparse, urlunparse

logger = logging.getLogger(__name__)

# Max concurrent Playwright pages sharing the single browser
_CRAWL_SEMAPHORE = asyncio.Semaphore(20)

_EMPTY_NAMES = {"unknown", "n/a", "none", "", "tbd", "tba"}
_TRAILING_URL_PUNCT = ".,;:!?)]}\"'`"


def _clean(val: str) -> str:
    """Return empty string if val looks like a placeholder. Strips quotes."""
    v = (val or "").strip()
    if v.lower() in _EMPTY_NAMES:
        return ""
    v = v.replace('"', '').replace('\u201c', '').replace('\u201d', '')
    return re.sub(r'\s+', ' ', v).strip()


def _name_tokens(name: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", (name or "").lower()) if len(t) > 1]


def _normalize_text(text: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", " ", (text or "").lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _contains_phrase(text: str, phrase: str) -> bool:
    t = f" {_normalize_text(text)} "
    p = _normalize_text(phrase)
    if not p:
        return False
    return f" {p} " in t


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
        r'!\[.*?\]\((https://media\.licdn\.com/dms/image/[^)]+)',
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
    raw.extend(re.findall(r'https?://[^\s)\]"\'"]+', markdown))

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


def _slug_raw_from_linkedin(url: str) -> str:
    """Extract the raw slug string from a LinkedIn URL."""
    try:
        parsed = urlparse(url or "")
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2 and parts[0] in {"in", "pub"}:
            return parts[1].lower().replace("%20", "-")
    except Exception:
        pass
    return ""


def _score_linkedin_candidate(url: str, content: str, name: str, company: str, title: str) -> int:
    """Score a LinkedIn candidate."""
    score = 0
    url_l = (url or "").lower()
    content_l = (content or "").lower()
    head = content_l[:5000]

    # Base URL scoring
    if "/in/" in url_l:
        score += 20

    name_toks = _name_tokens(name)
    slug_toks = _slug_tokens_from_linkedin(url_l)
    slug_raw = _slug_raw_from_linkedin(url_l).lower()
    
    first_name = name_toks[0] if name_toks else ""
    last_name = name_toks[-1] if len(name_toks) > 1 else ""

    # ===== SLUG ANALYSIS (highest priority) =====
    
    # First initial + last: sjeswani, kpfromer - VERY STRONG signal
    first_initial_last = f"{first_name[0]}{last_name}" if (first_name and last_name) else ""
    has_initials_slug = first_initial_last and first_initial_last == slug_raw
    
    # Full name patterns
    full_name_hyphen = f"{first_name}-{last_name}" if (first_name and last_name) else ""
    full_name_joined = f"{first_name}{last_name}" if (first_name and last_name) else ""
    
    # Check if slug IS exactly the pattern (not just contains)
    has_full_slug_hyphen = slug_raw == full_name_hyphen
    has_full_slug_joined = slug_raw == full_name_joined
    
    # Check for name tokens in slug
    first_in_slug = first_name and (first_name in slug_toks or first_name in slug_raw)
    last_in_slug = last_name and (last_name in slug_toks or last_name in slug_raw)
    
    # Score slug matches
    if has_initials_slug:
        score += 60  # Highest: exact initials+last match (sjeswani)
    elif has_full_slug_hyphen or has_full_slug_joined:
        score += 50  # High: exact name match
    elif first_in_slug and last_in_slug:
        score += 30
    elif first_in_slug or last_in_slug:
        score += 12
    
    # Penalize company-appended slugs VERY heavily
    if company and first_name and last_name:
        company_toks = _name_tokens(company)
        if company_toks:
            company_slug = company_toks[0].lower()
            # Check if slug contains or ends with company name
            if company_slug in slug_raw and company_slug not in [first_name, last_name]:
                # This is a generated pattern like sumeet-jeswani-google
                # Give it a MASSIVE penalty so it never wins over real profiles
                score -= 50
                
    # Extra bonus for short, clean slugs (real profiles often have these)
    if has_initials_slug or has_full_slug_hyphen or has_full_slug_joined:
        # These are good patterns - check if they're clean (no company appended)
        is_clean = True
        if company and company_toks:
            for tok in company_toks:
                if tok.lower() in slug_raw and tok.lower() not in [first_name, last_name]:
                    is_clean = False
                    break
        if is_clean:
            score += 15  # Bonus for clean name-only slug
    
    # ===== CONTENT NAME MATCHING =====
    full_name = f"{first_name} {last_name}".strip()
    
    if full_name and _contains_phrase(head, full_name):
        score += 25
    elif first_name and last_name and first_name in head and last_name in head[:3000]:
        score += 18
    else:
        if first_name and first_name in head:
            score += 6
        if last_name and last_name in head:
            score += 6
    
    # ===== COMPANY MATCHING =====
    if company:
        company_lower = company.lower()
        if company_lower in head:
            score += 20
        else:
            company_tokens = _name_tokens(company)[:3]
            company_hits = sum(1 for tok in company_tokens if tok in head)
            if company_hits >= len(company_tokens):
                score += 12
            elif company_hits >= 1:
                score += 5
    
    # ===== TITLE MATCHING =====
    if title:
        title_tokens = _name_tokens(title)[:3]
        title_hits = sum(1 for tok in title_tokens if tok in head)
        if title_hits >= len(title_tokens):
            score += 8
        elif title_hits >= 1:
            score += 3

    # Quality signals
    if "linkedin member" in content_l and len(content_l) < 600:
        score -= 15
    if len(content_l) < 200:
        score -= 10
    if len(content_l) > 1000:
        score += 5

    return score


def _is_linkedin_candidate_valid(url: str, content: str, name: str, score: int) -> bool:
    """Basic validation - lenient."""
    if score < 5:
        return False
    
    name_toks = _name_tokens(name)
    if len(name_toks) >= 2:
        first_name = name_toks[0]
        last_name = name_toks[-1]
        head = (content or "").lower()[:3000]
        slug_raw = _slug_raw_from_linkedin(url).lower()
        
        has_first = first_name in head or first_name in slug_raw
        has_last = last_name in head or last_name in slug_raw
        
        if not (has_first or has_last):
            return False
    
    return True


class HackathonCrawler:
    """Web crawler using Crawl4AI."""

    def __init__(self):
        self._crawler = None
        self._lock = asyncio.Lock()

    async def _get_crawler(self):
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

    async def _search_google_jina(self, query: str, max_results: int = 6) -> list[str]:
        """Search DuckDuckGo via jina.ai - returns URLs found in results.
        
        NOTE: Switched from Google to DuckDuckGo because Google blocked r.jina.ai
        with "DDoS attack suspected" error.
        """
        # Use DuckDuckGo HTML search (Google is blocked via jina.ai)
        ddg_url = f"https://duckduckgo.com/html/?q={quote(query)}"
        jina_url = f"https://r.jina.ai/{ddg_url}"
        try:
            result = await self._crawl_url(jina_url, timeout=20, content_limit=20000)
            content = result.get("content", "")

            # Extract ALL URLs from the search results
            all_urls = re.findall(r'https?://[^\s)\]"\'<>]+', content)

            out = []
            seen = set()
            for u in all_urls:
                # Decode DuckDuckGo redirect URLs
                decoded = _decode_ddg_redirect(u) or u
                cleaned = _normalize_candidate_url(decoded)
                if not cleaned:
                    continue
                if not _looks_like_search_result_url(cleaned):
                    continue
                # Skip jina.ai and duckduckgo URLs themselves
                if "jina.ai" in cleaned.lower() or "duckduckgo.com" in cleaned.lower():
                    continue
                if cleaned in seen:
                    continue
                seen.add(cleaned)
                out.append(cleaned)
                if len(out) >= max_results:
                    break

            logger.info(f"[search] '{query[:50]}' -> {len(out)} results")
            return out
        except Exception as e:
            logger.info(f"[search] failed: {e}")
            return []

    async def _search_linkedin_jina(self, query: str, max_results: int = 6) -> list[str]:
        """Search DuckDuckGo via jina.ai - returns only LinkedIn profile URLs.
        
        NOTE: Switched from Google to DuckDuckGo because Google blocked r.jina.ai.
        """
        ddg_url = f"https://duckduckgo.com/html/?q={quote(query)}"
        jina_url = f"https://r.jina.ai/{ddg_url}"
        try:
            result = await self._crawl_url(jina_url, timeout=20, content_limit=20000)
            content = result.get("content", "")

            # Extract ALL URLs first (DuckDuckGo wraps results in redirect URLs)
            all_urls = re.findall(r'https?://[^\s)\]"\'<>]+', content)
            
            out = []
            seen = set()
            for u in all_urls:
                # Decode DuckDuckGo redirect URLs to get actual LinkedIn URLs
                decoded = _decode_ddg_redirect(u) or u
                
                # Check if it's a LinkedIn profile URL
                if not _is_linkedin_profile_url(decoded):
                    continue
                    
                cleaned = _normalize_candidate_url(decoded)
                if not cleaned or cleaned in seen:
                    continue
                    
                norm = _normalize_linkedin_url(cleaned)
                if norm and norm not in seen:
                    seen.add(norm)
                    seen.add(cleaned)
                    out.append(cleaned)
                    if len(out) >= max_results:
                        break

            logger.info(f"[search-linkedin] '{query[:50]}' -> {len(out)} results")
            return out
        except Exception as e:
            logger.info(f"[search-linkedin] failed: {e}")
            return []

    async def _fetch_profile_content(self, url: str) -> dict:
        """Fetch LinkedIn profile content via direct crawl."""
        try:
            result = await self._crawl_url(url, timeout=12, content_limit=5000)
            content = result.get("content", "")
            # Check if we got real content (not auth wall)
            if len(content) > 300 and "Agree & Join LinkedIn" not in content:
                return {
                    "content": content,
                    "photo_url": _extract_linkedin_photo(content),
                    "success": True,
                    "url": url,
                }
        except Exception:
            pass
        
        return {"content": "", "photo_url": "", "success": False, "url": url}

    @staticmethod
    def _generate_linkedin_urls(name: str, company: str = "") -> list[str]:
        """Generate candidate LinkedIn URLs - focus on high-quality patterns."""
        parts = _name_tokens(name)
        if not parts:
            return []
        base = "https://www.linkedin.com/in/"
        urls = []

        if len(parts) >= 2:
            first, last = parts[0], parts[-1]
            # High priority patterns (most common for real profiles)
            urls.extend([
                base + first[0] + last,           # sjeswani
                base + first + "-" + last,        # sumeet-jeswani
                base + first + last,              # sumeetjeswani
                base + "".join(parts),            # sumeetkumarjeswani
                base + "-".join(parts),           # sumeet-kumar-jeswani
            ])
            # Lower priority
            urls.extend([
                base + last,                      # jeswani
                base + first,                     # sumeet
                base + first + last[0],           # sumeetj
            ])
        elif len(parts) == 1:
            urls.append(base + parts[0])

        # Skip company-appended URLs - they're usually wrong patterns
        # Real profiles with company in slug will be found via search

        seen = set()
        result = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                result.append(u)
        return result

    @staticmethod
    def _generate_name_variants(name: str) -> list[str]:
        """Generate name variants for spelling variations."""
        variants = []
        parts = _name_tokens(name)
        if len(parts) < 2:
            return variants
        
        first, last = parts[0], parts[-1]
        
        # Known variations
        last_variants = {
            'malve': ['malave'],
            'malave': ['malve'],
        }
        
        if last in last_variants:
            for variant in last_variants[last]:
                variants.append(f"{first} {variant}".title())
        
        return variants

    # Cache for hackathon page URL mappings
    _url_cache = {}
    _photo_cache = {}

    async def _extract_judge_urls_from_page(self, page_url: str) -> dict:
        """Extract judge name -> LinkedIn URL mappings from hackathon page."""
        if page_url in self._url_cache:
            return self._url_cache[page_url]
        
        page_content = await self.crawl_hackathon_page(page_url)
        import re
        
        # Find all LinkedIn URLs and extract names from the same line
        linkedin_pattern = r'https?://[^\s\)\]"\'<>]*linkedin\.com/in/[^\s\)\]"\'<>]+'
        
        judge_urls = {}
        
        for match in re.finditer(linkedin_pattern, page_content):
            url = match.group(0)
            pos = match.start()
            
            # Find the line containing this URL
            line_start = page_content.rfind('\n', 0, pos) + 1
            line_end = page_content.find('\n', pos)
            if line_end == -1:
                line_end = len(page_content)
            
            line = page_content[line_start:line_end]
            
            # Extract name: look for capitalized words at the start of the line
            # Remove the URL part
            line_without_url = line[:line.find(url)]
            
            # Remove 'View Bio' and similar text
            line_clean = re.sub(r'View Bio.*$', '', line_without_url)
            
            # Extract name: look for 2-3 capitalized words at the start
            # Handle names with optional quotes, hyphens, apostrophes, short names

            # Try 3 words with optional quotes (e.g., "Matt \"Kelly\" Williams", "Megan La Grave")
            name_match = re.match(r'^([A-Z][a-zA-Z\'\-]+\s+"?[A-Z][a-zA-Z\'\-]+"?\s+[A-Z][a-zA-Z\'\-]+)', line_clean)
            if name_match:
                name = name_match.group(1).replace('"', '')
                judge_urls[name.lower()] = url
            else:
                # Try 2 words
                name_match = re.match(r'^([A-Z][a-zA-Z\'\-]+\s+[A-Z][a-zA-Z\'\-]+)', line_clean)
                if name_match:
                    name = name_match.group(1)
                    judge_urls[name.lower()] = url
        
        self._url_cache[page_url] = judge_urls
        return judge_urls

    async def _extract_judge_photos_from_page(self, page_url: str) -> dict:
        """Extract judge name -> photo URL mappings from hackathon page."""
        if page_url in self._photo_cache:
            return self._photo_cache[page_url]
        
        page_content = await self.crawl_hackathon_page(page_url)
        import re
        from urllib.parse import urljoin
        
        judge_photos = {}
        
        # Pattern: Look for image URLs followed by judge name and LinkedIn URL
        # The structure is: ![](image.png)Name TitleView Bio[](linkedin_url)
        
        # Find all image URLs
        img_pattern = r'!\[\]\((https?://[^\)]+\.(?:png|jpg|jpeg|gif|webp))\)'
        
        for match in re.finditer(img_pattern, page_content, re.IGNORECASE):
            img_url = match.group(1)
            img_end = match.end()
            
            # Look at the text after this image (up to 300 chars)
            context_end = min(len(page_content), img_end + 300)
            context = page_content[img_end:context_end]
            
            # Strip leading whitespace/newlines
            context = context.lstrip()
            
            # Extract name from this context
            # Try 3-word names first
            name_match = re.match(r'^([A-Z][a-z]+\s+"?[A-Z][a-z]+"?\s+[A-Z][a-z]+)', context)
            if name_match:
                name = name_match.group(1).replace('"', '')
                judge_photos[name.lower()] = img_url
            else:
                # Try 2-word names
                name_match = re.match(r'^([A-Z][a-z]+\s+[A-Z][a-z]+)', context)
                if name_match:
                    name = name_match.group(1)
                    judge_photos[name.lower()] = img_url
        
        self._photo_cache[page_url] = judge_photos
        return judge_photos

    async def worker_linkedin(self, name: str, company: str = "", title: str = "",
                               analyzer=None, hackathon_page_url: str = None) -> dict:
        """Find LinkedIn profile - use hackathon page first, then search."""
        _empty = {"source": "linkedin", "content": "", "sources": [], "linkedin_url": "", "photo_url": ""}
        name, company, title = _clean(name), _clean(company), _clean(title)
        if not name:
            return _empty

        linkedin_urls = []
        seen_urls = set()
        photo_url = ""  # Track photo URL from page

        # === Phase 1: Extract from hackathon page if provided ===
        if hackathon_page_url:
            try:
                judge_urls = await self._extract_judge_urls_from_page(hackathon_page_url)
                judge_photos = await self._extract_judge_photos_from_page(hackathon_page_url)
                
                # Look for exact or fuzzy match
                name_lower = name.lower()
                name_parts = _name_tokens(name)
                
                if len(name_parts) >= 2:
                    first, last = name_parts[0], name_parts[-1]
                    
                    # Try exact match first
                    if name_lower in judge_urls:
                        url = judge_urls[name_lower]
                        norm = _normalize_linkedin_url(url)
                        if norm:
                            linkedin_urls.append(url)
                            seen_urls.add(norm)
                            logger.info(f"[linkedin] Exact match on page: {name} -> {url}")
                            # Get photo if available
                            if name_lower in judge_photos:
                                photo_url = judge_photos[name_lower]
                                logger.info(f"[linkedin] Found photo on page: {name} -> {photo_url}")
                    else:
                        # Try partial match (first or last name)
                        for judge_name, url in judge_urls.items():
                            if first in judge_name and last in judge_name:
                                norm = _normalize_linkedin_url(url)
                                if norm and norm not in seen_urls:
                                    linkedin_urls.append(url)
                                    seen_urls.add(norm)
                                    logger.info(f"[linkedin] Partial match on page: {name} -> {url}")
                                    # Get photo if available
                                    if judge_name in judge_photos:
                                        photo_url = judge_photos[judge_name]
                                        logger.info(f"[linkedin] Found photo on page: {name} -> {photo_url}")
                                    break
            except Exception as e:
                logger.warning(f"[linkedin] Failed to extract from hackathon page: {e}")

        # === Phase 2: Search for LinkedIn profiles (if not found on page) ===
        if not linkedin_urls:
            queries = []
            if company:
                queries.append(f'"{name}" {company} linkedin')
            queries.append(f'"{name}" site:linkedin.com/in')
            
            search_tasks = [self._search_linkedin_jina(q, max_results=6) for q in queries[:2]]
            search_results = await asyncio.gather(*search_tasks)
            
            for urls in search_results:
                for u in urls:
                    norm = _normalize_linkedin_url(u)
                    if norm and norm not in seen_urls:
                        seen_urls.add(norm)
                        linkedin_urls.append(u)

        # === Phase 3: Add direct URL patterns (if still not found) ===
        if not linkedin_urls:
            direct_urls = self._generate_linkedin_urls(name, company)
            
            for variant in self._generate_name_variants(name):
                direct_urls.extend(self._generate_linkedin_urls(variant, company))
            
            for u in direct_urls[:8]:
                norm = _normalize_linkedin_url(u)
                if norm and norm not in seen_urls:
                    seen_urls.add(norm)
                    linkedin_urls.append(u)

        if not linkedin_urls:
            return _empty

        # === Phase 4: Fetch profile content ===
        fetch_tasks = [self._fetch_profile_content(u) for u in linkedin_urls[:8]]
        fetched_results = await asyncio.gather(*fetch_tasks)

        # === Phase 5: Score candidates ===
        candidates = []
        for data in fetched_results:
            if not data.get("success"):
                continue
            url = _normalize_linkedin_url(data.get("url", ""))
            content = data.get("content", "")
            if len(content) < 100:
                continue

            score = _score_linkedin_candidate(url, content, name, company, title)
            if not _is_linkedin_candidate_valid(url, content, name, score):
                continue

            candidates.append({
                "url": url,
                "content": content[:5000],
                "photo_url": data.get("photo_url", ""),
                "score": score,
            })

        if not candidates:
            # If we got URLs from hackathon page but couldn't fetch content,
            # return the first one anyway (it's probably correct)
            if linkedin_urls and hackathon_page_url:
                return {
                    "source": "linkedin",
                    "content": "Profile found on hackathon page",
                    "sources": [linkedin_urls[0]],
                    "linkedin_url": _normalize_linkedin_url(linkedin_urls[0]),
                    "photo_url": photo_url,
                }
            return _empty

        candidates.sort(key=lambda x: x["score"], reverse=True)

        # === Phase 6: Gemini verification ===
        if analyzer:
            top = candidates[:3]
            verify_tasks = [
                analyzer.verify_linkedin_match(name, company, title, c["content"], c["url"])
                for c in top
            ]
            verifications = await asyncio.gather(*verify_tasks, return_exceptions=True)

            best_candidate = None
            best_combined = -1
            conf_weight = {"high": 300, "medium": 200, "low": 100}

            for candidate, verification in zip(top, verifications):
                if isinstance(verification, Exception) or not isinstance(verification, dict):
                    continue
                if not verification.get("is_match", False):
                    continue
                conf = conf_weight.get(verification.get("confidence", "low"), 0)
                combined = conf + candidate["score"]
                if combined > best_combined:
                    best_combined = combined
                    best_candidate = candidate

            if best_candidate:
                return {
                    "source": "linkedin",
                    "content": best_candidate["content"],
                    "sources": [best_candidate["url"]],
                    "linkedin_url": best_candidate["url"],
                    "photo_url": photo_url or best_candidate.get("photo_url", ""),
                }

            # If we have candidates but Gemini rejected all, still return top if from hackathon page
            if linkedin_urls and hackathon_page_url:
                return {
                    "source": "linkedin",
                    "content": candidates[0]["content"],
                    "sources": [candidates[0]["url"]],
                    "linkedin_url": candidates[0]["url"],
                    "photo_url": photo_url or candidates[0].get("photo_url", ""),
                }

            return _empty

        top = candidates[0]
        return {
            "source": "linkedin",
            "content": top["content"],
            "sources": [c["url"] for c in candidates[:2]],
            "linkedin_url": top["url"],
            "photo_url": photo_url or top.get("photo_url", ""),
        }

    async def crawl_hackathon_page(self, url: str) -> str:
        """Crawl the main hackathon page."""
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

    async def worker_github_personal(self, name: str, company: str = "") -> dict:
        """Find GitHub profile."""
        name, company = _clean(name), _clean(company)
        if not name:
            return {"source": "github_personal", "content": "", "sources": []}
        
        query = f'"{name}" site:github.com'
        urls = await self._search_google_jina(query, max_results=4)
        
        results = []
        sources = []
        
        for url in urls[:2]:
            if "github.com/" in url:
                data = await self._crawl_url(url, timeout=15, content_limit=3000)
                if data.get("content") and len(data["content"]) > 100:
                    results.append(f"[GitHub: {url}]\n{data['content'][:3000]}")
                    sources.append(url)

        return {
            "source": "github_personal",
            "content": "\n\n---\n\n".join(results),
            "sources": sources,
        }

    async def worker_news_interviews(self, name: str, company: str = "") -> dict:
        """Find news articles."""
        name, company = _clean(name), _clean(company)
        if not name:
            return {"source": "news_interviews", "content": "", "sources": []}
        
        query = f'"{name}" {company} interview news article'
        urls = await self._search_google_jina(query, max_results=4)
        
        results = []
        sources = []
        
        for url in urls[:3]:
            if "linkedin.com" not in url:
                data = await self._crawl_url(url, timeout=15, content_limit=3000)
                if data.get("content") and len(data["content"]) > 200:
                    results.append(f"[Article: {url}]\n{data['content'][:3000]}")
                    sources.append(url)

        return {
            "source": "news_interviews",
            "content": "\n\n---\n\n".join(results[:3]),
            "sources": sources[:3],
        }

    async def worker_social_media(self, name: str, company: str = "") -> dict:
        """Find social profiles."""
        name, company = _clean(name), _clean(company)
        if not name:
            return {"source": "social_media", "content": "", "sources": []}
        
        query = f'"{name}" {company} site:twitter.com OR site:x.com'
        urls = await self._search_google_jina(query, max_results=4)
        
        results = []
        sources = []
        
        for url in urls[:3]:
            data = await self._crawl_url(url, timeout=15, content_limit=3000)
            if data.get("content") and len(data["content"]) > 100:
                results.append(f"[Social: {url}]\n{data['content'][:3000]}")
                sources.append(url)

        return {
            "source": "social_media",
            "content": "\n\n---\n\n".join(results[:3]),
            "sources": sources[:3],
        }

    async def worker_academic(self, name: str, company: str = "") -> dict:
        """Find academic content."""
        name, company = _clean(name), _clean(company)
        if not name:
            return {"source": "academic", "content": "", "sources": []}
        
        query = f'"{name}" site:scholar.google.com OR arxiv'
        urls = await self._search_google_jina(query, max_results=4)
        
        results = []
        sources = []
        
        for url in urls[:3]:
            data = await self._crawl_url(url, timeout=15, content_limit=3000)
            if data.get("content") and len(data["content"]) > 150:
                results.append(f"[Academic: {url}]\n{data['content'][:3000]}")
                sources.append(url)

        return {
            "source": "academic",
            "content": "\n\n---\n\n".join(results[:3]),
            "sources": sources[:3],
        }

    async def worker_company(self, name: str, company: str = "", title: str = "") -> dict:
        """Research company."""
        name, company, title = _clean(name), _clean(company), _clean(title)
        if not company:
            return {"source": "company", "content": "", "sources": []}

        query = f'"{company}" about crunchbase'
        urls = await self._search_google_jina(query, max_results=4)
        
        results = []
        sources = []
        
        for url in urls[:3]:
            if "linkedin.com" not in url:
                data = await self._crawl_url(url, timeout=15, content_limit=3000)
                if data.get("content") and len(data["content"]) > 150:
                    results.append(f"[Company: {url}]\n{data['content'][:3000]}")
                    sources.append(url)

        return {
            "source": "company",
            "content": "\n\n---\n\n".join(results[:3]),
            "sources": sources[:3],
        }

    async def worker_talks_videos(self, name: str, company: str = "") -> dict:
        """Find talks and videos."""
        name, company = _clean(name), _clean(company)
        if not name:
            return {"source": "talks_videos", "content": "", "sources": []}
        
        query = f'"{name}" {company} site:youtube.com'
        urls = await self._search_google_jina(query, max_results=4)
        
        results = []
        sources = []
        
        for url in urls[:3]:
            data = await self._crawl_url(url, timeout=15, content_limit=3000)
            if data.get("content") and len(data["content"]) > 100:
                results.append(f"[Video: {url}]\n{data['content'][:3000]}")
                sources.append(url)

        return {
            "source": "talks_videos",
            "content": "\n\n---\n\n".join(results[:3]),
            "sources": sources[:3],
        }

    async def worker_youtube_transcripts(self, name: str, company: str = "") -> dict:
        """Find YouTube transcripts."""
        name, company = _clean(name), _clean(company)
        if not name:
            return {"source": "youtube_transcripts", "content": "", "sources": []}
        
        query = f'"{name}" {company} site:youtube.com'
        urls = await self._search_google_jina(query, max_results=3)
        
        results = []
        sources = []
        
        for url in urls[:2]:
            data = await self._crawl_url(url, timeout=15, content_limit=3000)
            if data.get("content") and len(data["content"]) > 200:
                results.append(f"[YouTube: {url}]\n{data['content'][:3000]}")
                sources.append(url)

        return {
            "source": "youtube_transcripts",
            "content": "\n\n---\n\n".join(results[:2]),
            "sources": sources[:2],
        }

    async def worker_podcasts_hn(self, name: str, company: str = "") -> dict:
        """Find podcast appearances and Hacker News mentions."""
        name, company = _clean(name), _clean(company)
        if not name:
            return {"source": "podcasts_hn", "content": "", "sources": []}
        
        query = f'"{name}" {company} site:news.ycombinator.com OR podcast'
        urls = await self._search_google_jina(query, max_results=4)
        
        results = []
        sources = []
        
        for url in urls[:3]:
            if "linkedin.com" not in url:
                data = await self._crawl_url(url, timeout=15, content_limit=3000)
                if data.get("content") and len(data["content"]) > 150:
                    label = "Hacker News" if "ycombinator.com" in url else "Podcast"
                    results.append(f"[{label}: {url}]\n{data['content'][:3000]}")
                    sources.append(url)

        return {
            "source": "podcasts_hn",
            "content": "\n\n---\n\n".join(results[:3]),
            "sources": sources[:3],
        }

    async def worker_general_search(self, name: str, company: str = "",
                                     title: str = "", already_seen: set = None) -> dict:
        """Broad web search."""
        name, company, title = _clean(name), _clean(company), _clean(title)
        if not name:
            return {"source": "general_search", "content": "", "sources": []}
        if already_seen is None:
            already_seen = set()

        query = f'"{name}" {company} bio profile'
        urls = await self._search_google_jina(query, max_results=4)

        results = []
        sources = []
        
        for url in urls[:3]:
            if url not in already_seen and "google.com" not in url:
                data = await self._crawl_url(url, timeout=15, content_limit=3000)
                if data.get("content") and len(data["content"]) > 150:
                    results.append(f"[Web: {url}]\n{data['content'][:3000]}")
                    sources.append(url)

        return {
            "source": "general_search",
            "content": "\n\n---\n\n".join(results[:3]),
            "sources": sources[:3],
        }
