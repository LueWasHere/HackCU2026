import asyncio
import queue as queue_module


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

    try:
        crawler = HackathonCrawler()
        analyzer = HackathonAnalyzer()

        # ── Step 1: Crawl hackathon page ──────────────────────────────────
        _send(q, {"type": "status", "step": 1, "message": "Crawling hackathon page..."})
        page_content = await crawler.crawl_hackathon_page(url)

        if not page_content or len(page_content) < 100:
            _send(q, {"type": "error", "message": "Could not retrieve page content. Check the URL and try again."})
            db.mark_error(job_id, "Page content too short")
            return

        # ── Step 2: Extract hackathon info + judges ───────────────────────
        _send(q, {"type": "status", "step": 2, "message": "Identifying hackathon details and judges with AI..."})
        extracted = await analyzer.extract_hackathon_info(page_content, url)

        hackathon = extracted.get("hackathon", {})
        judges_raw = extracted.get("judges", [])

        if not hackathon.get("name"):
            hackathon["name"] = "Hackathon"

        _send(q, {
            "type": "hackathon_found",
            "hackathon": hackathon,
        })

        if not judges_raw:
            _send(q, {"type": "error", "message": "No judges found on this page. Try linking directly to the judges section."})
            db.mark_error(job_id, "No judges found")
            return

        _send(q, {
            "type": "judges_found",
            "count": len(judges_raw),
            "judges": [{"name": j.get("name", "?"), "title": j.get("title", ""), "company": j.get("company", "")} for j in judges_raw],
        })

        # ── Step 3: Research each judge with 3 parallel workers ───────────
        _send(q, {
            "type": "status",
            "step": 3,
            "message": f"Launching 3 research workers per judge ({len(judges_raw)} judges × 3 = {len(judges_raw)*3} concurrent crawls)...",
        })

        judge_tasks = [
            _research_single_judge(judge, crawler, analyzer, q)
            for judge in judges_raw
        ]
        researched_judges = await asyncio.gather(*judge_tasks, return_exceptions=True)

        valid_judges = []
        for judge, result in zip(judges_raw, researched_judges):
            if isinstance(result, Exception):
                _send(q, {"type": "judge_error", "judge_name": judge.get("name", "?"), "error": str(result)})
                valid_judges.append({**judge, "research_confidence": "low"})
            else:
                valid_judges.append(result)

        # ── Step 4: Generate final analysis ──────────────────────────────
        _send(q, {
            "type": "status",
            "step": 4,
            "message": "Synthesizing judge intelligence and generating project ideas...",
        })
        final = await analyzer.generate_final_analysis(hackathon, valid_judges)

        result_payload = {
            "hackathon": hackathon,
            "judges": valid_judges,
            "aggregate_stats": final.get("aggregate_stats", {}),
            "project_ideas": final.get("project_ideas", []),
        }

        db.update_analysis(job_id, result_payload)

        _send(q, {"type": "complete", "data": result_payload})

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        _send(q, {"type": "error", "message": str(e)})
        db.mark_error(job_id, f"{e}\n{tb[:300]}")


async def _research_single_judge(judge: dict, crawler, analyzer, q: queue_module.Queue) -> dict:
    """Run 3 parallel workers for a single judge, then analyze."""
    name = judge.get("name", "Unknown")
    company = judge.get("company", "")
    title = judge.get("title", "")

    _send(q, {"type": "judge_start", "judge_name": name})

    # ── 3 parallel research workers ──────────────────────────────────────
    worker_results = await asyncio.gather(
        crawler.worker_linkedin(name, company, title),
        crawler.worker_github_personal(name, company),
        crawler.worker_news_papers_talks(name, company),
        return_exceptions=True,
    )

    research_data = {}
    for i, r in enumerate(worker_results):
        key = f"worker_{i+1}"
        if isinstance(r, Exception):
            research_data[key] = {"source": key, "content": "", "sources": [], "error": str(r)}
        else:
            research_data[key] = r

    source_counts = sum(1 for r in research_data.values() if r.get("content") and len(r.get("content", "")) > 50)
    _send(q, {
        "type": "judge_research_done",
        "judge_name": name,
        "sources_found": source_counts,
    })

    # ── Analyze with Gemini ───────────────────────────────────────────────
    profile = await analyzer.analyze_judge(judge, research_data)

    _send(q, {"type": "judge_complete", "judge_name": name, "judge": profile})
    return profile
