import asyncio
import queue as queue_module
import time

# ── ANSI colors for console output ─────────────────────────────────────────
CYAN    = "\033[96m"
GREEN   = "\033[92m"
YELLOW  = "\033[93m"
RED     = "\033[91m"
MAGENTA = "\033[95m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
RESET   = "\033[0m"


def _log(msg: str):
    """Print a timestamped log line to the console."""
    ts = time.strftime("%H:%M:%S")
    print(f"{DIM}[{ts}]{RESET} {msg}", flush=True)


def run_analysis_pipeline(job_id: str, url: str, q: queue_module.Queue, db):
    """Entry point for the background thread. Creates its own event loop."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_async_pipeline(job_id, url, q, db))
    finally:
        loop.close()


def _send(q: queue_module.Queue, event: dict):
    """Helper to put events on the SSE queue."""
    q.put(event)


async def _async_pipeline(job_id: str, url: str, q: queue_module.Queue, db):
    from crawler import HackathonCrawler
    from analyzer import HackathonAnalyzer

    pipeline_start = time.time()

    try:
        crawler = HackathonCrawler()
        analyzer = HackathonAnalyzer()

        # ── Step 1: Crawl hackathon page ──────────────────────────────────
        _log(f"{CYAN}{BOLD}{'='*60}")
        _log(f"{CYAN}{BOLD}  STEP 1: CRAWLING HACKATHON PAGE")
        _log(f"{CYAN}{BOLD}{'='*60}{RESET}")
        _log(f"{CYAN}  URL: {url}{RESET}")
        _send(q, {"type": "status", "step": 1, "message": "Crawling hackathon page..."})

        t0 = time.time()
        page_content = await crawler.crawl_hackathon_page(url)
        elapsed = time.time() - t0

        _log(f"{GREEN}  Crawl complete in {elapsed:.1f}s — got {len(page_content):,} chars{RESET}")

        if not page_content or len(page_content) < 100:
            _log(f"{RED}  ERROR: Page content too short ({len(page_content)} chars). Bad URL?{RESET}")
            _send(q, {"type": "error", "message": "Could not retrieve page content. Check the URL and try again."})
            db.mark_error(job_id, "Page content too short")
            return

        # ── Step 2: Extract hackathon info + judges ───────────────────────
        _log(f"")
        _log(f"{CYAN}{BOLD}{'='*60}")
        _log(f"{CYAN}{BOLD}  STEP 2: EXTRACTING HACKATHON INFO + JUDGES (AI)")
        _log(f"{CYAN}{BOLD}{'='*60}{RESET}")
        _send(q, {"type": "status", "step": 2, "message": "Identifying hackathon details and judges with AI..."})

        t0 = time.time()
        extracted = await analyzer.extract_hackathon_info(page_content, url)
        elapsed = time.time() - t0

        hackathon = extracted.get("hackathon", {})
        judges_raw = extracted.get("judges", [])

        if not hackathon.get("name"):
            hackathon["name"] = "Hackathon"

        _log(f"{GREEN}  Extraction complete in {elapsed:.1f}s{RESET}")
        _log(f"{BOLD}  Hackathon: {hackathon.get('name', '?')}{RESET}")
        _log(f"  Theme:     {hackathon.get('theme', 'N/A')}")
        _log(f"  Organizer: {hackathon.get('organizer', 'N/A')}")
        _log(f"  Location:  {hackathon.get('location', 'N/A')}")
        tracks = hackathon.get("tracks", [])
        if tracks:
            _log(f"  Tracks:    {', '.join(tracks)}")
        prizes = hackathon.get("prizes", [])
        if prizes:
            _log(f"  Prizes:    {len(prizes)} listed")

        _send(q, {
            "type": "hackathon_found",
            "hackathon": hackathon,
        })

        if not judges_raw:
            _log(f"{RED}  ERROR: No judges found on page!{RESET}")
            _send(q, {"type": "error", "message": "No judges found on this page. Try linking directly to the judges section."})
            db.mark_error(job_id, "No judges found")
            return

        _log(f"")
        _log(f"{GREEN}{BOLD}  Found {len(judges_raw)} judges:{RESET}")
        for i, j in enumerate(judges_raw, 1):
            name = j.get("name", "?")
            title = j.get("title", "")
            company = j.get("company", "")
            parts = [f"{BOLD}{name}{RESET}"]
            if title:
                parts.append(title)
            if company:
                parts.append(f"@ {company}")
            _log(f"    {i:>2}. {' — '.join(parts)}")

        _send(q, {
            "type": "judges_found",
            "count": len(judges_raw),
            "judges": [{"name": j.get("name", "?"), "title": j.get("title", ""), "company": j.get("company", "")} for j in judges_raw],
        })

        # ── Step 3: Research each judge with 3 parallel workers ───────────
        _log(f"")
        _log(f"{CYAN}{BOLD}{'='*60}")
        _log(f"{CYAN}{BOLD}  STEP 3: DEEP RESEARCH ({len(judges_raw)} judges x 10 workers)")
        _log(f"{CYAN}{BOLD}{'='*60}{RESET}")
        _send(q, {
            "type": "status",
            "step": 3,
            "message": f"Launching 10 research workers per judge ({len(judges_raw)} judges x 10 = {len(judges_raw)*10} crawl tasks)...",
        })

        t0 = time.time()
        valid_judges = []
        BATCH_SIZE = 6
        for batch_start in range(0, len(judges_raw), BATCH_SIZE):
            batch = judges_raw[batch_start:batch_start + BATCH_SIZE]
            batch_num = batch_start // BATCH_SIZE + 1
            total_batches = (len(judges_raw) + BATCH_SIZE - 1) // BATCH_SIZE
            _log(f"")
            _log(f"{CYAN}  --- Batch {batch_num}/{total_batches}: "
                 f"{', '.join(j.get('name', '?') for j in batch)} ---{RESET}")

            batch_tasks = [
                _research_single_judge(judge, crawler, analyzer, q)
                for judge in batch
            ]
            batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)

            for judge, result in zip(batch, batch_results):
                if isinstance(result, Exception):
                    _log(f"{RED}  FAILED: {judge.get('name', '?')} — {result}{RESET}")
                    _send(q, {"type": "judge_error", "judge_name": judge.get("name", "?"), "error": str(result)})
                    valid_judges.append({**judge, "research_confidence": "low"})
                else:
                    valid_judges.append(result)

        elapsed = time.time() - t0
        _log(f"")
        _log(f"{GREEN}  All judge research complete in {elapsed:.1f}s{RESET}")

        # ── Step 4: Generate final analysis ──────────────────────────────
        _log(f"")
        _log(f"{CYAN}{BOLD}{'='*60}")
        _log(f"{CYAN}{BOLD}  STEP 4: GENERATING FINAL ANALYSIS + PROJECT IDEAS")
        _log(f"{CYAN}{BOLD}{'='*60}{RESET}")
        _send(q, {
            "type": "status",
            "step": 4,
            "message": "Synthesizing judge intelligence and generating project ideas...",
        })

        t0 = time.time()
        final = await analyzer.generate_final_analysis(hackathon, valid_judges)
        elapsed = time.time() - t0

        project_ideas = final.get("project_ideas", [])
        agg = final.get("aggregate_stats", {})

        _log(f"{GREEN}  Analysis complete in {elapsed:.1f}s{RESET}")
        if agg.get("dominant_archetype"):
            _log(f"  Panel archetype: {agg['dominant_archetype']}")
        if agg.get("biggest_opportunity"):
            _log(f"  Biggest opportunity: {agg['biggest_opportunity']}")

        if project_ideas:
            _log(f"")
            _log(f"{MAGENTA}{BOLD}  Top project ideas:{RESET}")
            for idea in project_ideas[:5]:
                rank = idea.get("rank", "?")
                title = idea.get("title", "Untitled")
                tagline = idea.get("tagline", "")
                diff = idea.get("difficulty", "?")
                _log(f"    {BOLD}#{rank}{RESET} {title} {DIM}[{diff}]{RESET}")
                if tagline:
                    _log(f"       {DIM}{tagline}{RESET}")

        result_payload = {
            "hackathon": hackathon,
            "judges": valid_judges,
            "aggregate_stats": agg,
            "project_ideas": project_ideas,
        }

        db.update_analysis(job_id, result_payload)
        _send(q, {"type": "complete", "data": result_payload})

        total = time.time() - pipeline_start
        _log(f"")
        _log(f"{GREEN}{BOLD}{'='*60}")
        _log(f"{GREEN}{BOLD}  DONE — {len(valid_judges)} judges analyzed in {total:.1f}s")
        _log(f"{GREEN}{BOLD}{'='*60}{RESET}")

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        _log(f"{RED}{BOLD}  PIPELINE ERROR: {e}{RESET}")
        _log(f"{RED}{DIM}{tb[:500]}{RESET}")
        _send(q, {"type": "error", "message": str(e)})
        db.mark_error(job_id, f"{e}\n{tb[:300]}")
    finally:
        await crawler.close()


async def _research_single_judge(judge: dict, crawler, analyzer, q: queue_module.Queue) -> dict:
    """Run 10 workers for a single judge in parallel, then analyze."""
    name = judge.get("name", "Unknown")
    company = judge.get("company", "")
    title = judge.get("title", "")

    _log(f"{YELLOW}  >> Researching: {BOLD}{name}{RESET}{YELLOW} ({title}{' @ ' + company if company else ''}){RESET}")
    _send(q, {"type": "judge_start", "judge_name": name})

    t0 = time.time()

    # ── All 10 workers in parallel (including general search) ──────────────
    worker_results = await asyncio.gather(
        crawler.worker_linkedin(name, company, title, analyzer=analyzer),
        crawler.worker_github_personal(name, company),
        crawler.worker_news_interviews(name, company),
        crawler.worker_social_media(name, company),
        crawler.worker_academic(name, company),
        crawler.worker_company(name, company, title),
        crawler.worker_talks_videos(name, company),
        crawler.worker_youtube_transcripts(name, company),
        crawler.worker_general_search(name, company, title),
        crawler.worker_podcasts_hn(name, company),
        return_exceptions=True,
    )

    research_data = {}
    worker_labels = ["LinkedIn", "GitHub/Site", "News", "Social", "Academic", "Company", "Talks", "YT Transcripts", "General", "Podcasts/HN"]
    source_details = []

    # Extract validated LinkedIn URL + photo from LinkedIn worker result
    linkedin_url = ""
    photo_url = ""
    if not isinstance(worker_results[0], Exception):
        linkedin_url = worker_results[0].get("linkedin_url", "")
        photo_url = worker_results[0].get("photo_url", "")

    for i, r in enumerate(worker_results):
        key = f"worker_{i+1}"
        label = worker_labels[i]
        if isinstance(r, Exception):
            research_data[key] = {"source": key, "content": "", "sources": [], "error": str(r)}
            source_details.append(f"{RED}{label}:ERR{RESET}")
        else:
            research_data[key] = r
            src_count = len(r.get("sources", []))
            content_len = len(r.get("content", ""))
            if content_len > 50:
                source_details.append(f"{GREEN}{label}:{src_count}{RESET}")
            else:
                source_details.append(f"{DIM}{label}:0{RESET}")

    elapsed_all = time.time() - t0
    _log(f"     {name}: all workers done in {elapsed_all:.1f}s — {' | '.join(source_details)}")

    source_counts = sum(1 for r in research_data.values() if r.get("content") and len(r.get("content", "")) > 50)

    _send(q, {
        "type": "judge_research_done",
        "judge_name": name,
        "sources_found": source_counts,
    })

    # ── Analyze with Gemini ───────────────────────────────────────────────
    _log(f"     {name}: {CYAN}analyzing with Gemini...{RESET}")
    t0 = time.time()
    profile = await analyzer.analyze_judge(judge, research_data)
    elapsed = time.time() - t0

    confidence = profile.get("research_confidence", "?")
    expertise = profile.get("expertise", [])
    _log(f"     {name}: {GREEN}profile ready{RESET} in {elapsed:.1f}s — "
         f"confidence={BOLD}{confidence}{RESET}, "
         f"expertise={', '.join(expertise[:3]) if expertise else 'N/A'}")

    # Always use validated LinkedIn URL; empty string hides uncertain/hallucinated links.
    profile["linkedin_url"] = linkedin_url
    if photo_url:
        profile["photo_url"] = photo_url

    _send(q, {"type": "judge_complete", "judge_name": name, "judge": profile})
    return profile
