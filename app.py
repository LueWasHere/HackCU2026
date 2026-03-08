"""
app.py – Win The Hackathon — Flask application.
"""

import warnings
# Suppress urllib3/chardet version mismatch warnings
warnings.filterwarnings("ignore", message=".*urllib3.*", category=UserWarning)
warnings.filterwarnings("ignore", message=".*urllib3.*", category=DeprecationWarning)
warnings.filterwarnings("ignore", module="requests")

import asyncio
import json
import os
import threading
import uuid
from datetime import datetime

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request, stream_with_context

import analyzer
import db
import scraper

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-please-change")


# ── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/favicon.ico")
def favicon():
    # Return a simple 1x1 pixel to avoid 404
    return Response(b"", mimetype="image/x-icon", status=204)


@app.route("/history")
def history():
    return jsonify(db.list_jobs())


@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "URL is required"}), 400
    if not url.startswith("http"):
        url = "https://" + url

    job_id = uuid.uuid4().hex[:10]
    db.create_job(job_id, url)

    t = threading.Thread(target=_run_pipeline, args=(job_id, url), daemon=True)
    t.start()

    return jsonify({"job_id": job_id})


@app.route("/analysis/<job_id>")
def analysis_page(job_id):
    job = db.get_job(job_id)
    if not job:
        return "Job not found", 404
    return render_template("analysis.html", job_id=job_id, job=job)


@app.route("/analysis/<job_id>/stream")
def analysis_stream(job_id):
    """Server-Sent Events stream for live progress."""
    import time

    def generate():
        last_idx = 0
        max_wait = 300  # 5 minute timeout
        waited = 0
        while waited < max_wait:
            job = db.get_job(job_id)
            if not job:
                yield f"data: {json.dumps({'type':'error','message':'Job not found'})}\n\n"
                return

            log = job.get("log", [])
            for entry in log[last_idx:]:
                yield f"data: {json.dumps({'type':'log','message':entry['msg'],'level':entry.get('level','info')})}\n\n"
            last_idx = len(log)

            status = job.get("status", "pending")
            if status in ("complete", "error"):
                yield f"data: {json.dumps({'type':'done','status':status})}\n\n"
                return

            time.sleep(0.5)
            waited += 0.5

        yield f"data: {json.dumps({'type':'done','status':'error'})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.route("/analysis/<job_id>/data")
def analysis_data(job_id):
    job = db.get_job(job_id)
    if not job:
        return jsonify({"error": "Not found"}), 404
    # Remove huge raw content to keep payload small
    if "hackathon_data" in job:
        job["hackathon_data"].pop("raw_content", None)
    return jsonify(job)


@app.route("/analysis/<job_id>/chat", methods=["POST"])
def analysis_chat(job_id):
    job = db.get_job(job_id)
    if not job:
        return jsonify({"error": "Not found"}), 404
    if job.get("status") != "complete":
        return jsonify({"error": "Analysis still in progress"}), 400

    payload = request.get_json(silent=True) or {}
    message = (payload.get("message") or "").strip()
    history = payload.get("history") or []

    if not message:
        return jsonify({"error": "Message required"}), 400

    try:
        response = analyzer.chat_response(job, message, history)
        return jsonify({"response": response})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Pipeline ──────────────────────────────────────────────────────────────────

def _run_pipeline(job_id: str, url: str):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_pipeline(job_id, url))
    finally:
        loop.close()


async def _pipeline(job_id: str, url: str):
    def log(msg: str, level: str = "info"):
        db.add_log(job_id, msg, level)

    try:
        db.update_job(job_id, {"status": "running"})
        log(f"🌐 Connecting to {url}")

        # ── Step 1: Scrape hackathon page ──────────────────────────────────
        log("📄 Crawling hackathon page…")
        hackathon_raw = await scraper.scrape_hackathon(url)
        db.update_job(job_id, {"hackathon_data": hackathon_raw})

        raw_content = hackathon_raw.get("raw_content", "")
        if not raw_content or len(raw_content.strip()) < 50:
            log("⚠️ Page returned very little content — the site may be blocking crawlers or require JavaScript", level="error")
            log("💡 Tip: Try a different URL (e.g. the Devpost or info page)", level="info")
            db.update_job(job_id, {"status": "error", "error": "Could not extract content from the page. Try a direct event page URL."})
            return

        log(f"✅ Page crawled — {len(raw_content):,} chars extracted")

        # ── Step 2: Gemini structural analysis ────────────────────────────
        log("🤖 Analysing page with Gemini Flash…")
        try:
            hackathon_analysis = analyzer.analyze_hackathon(hackathon_raw)
        except Exception as e:
            log(f"⚠️ Gemini analysis failed: {e}", level="error")
            hackathon_analysis = {}
        db.update_job(job_id, {"hackathon_analysis": hackathon_analysis})

        if not hackathon_analysis:
            log("⚠️ Gemini returned empty analysis — retrying…", level="error")
            try:
                hackathon_analysis = analyzer.analyze_hackathon(hackathon_raw)
            except Exception as e:
                log(f"⚠️ Retry also failed: {e}", level="error")
                hackathon_analysis = {}
            db.update_job(job_id, {"hackathon_analysis": hackathon_analysis})

        event_name = (
            hackathon_analysis.get("name")
            or hackathon_raw.get("name")
            or "Hackathon"
        )
        db.update_job(job_id, {"hackathon_name": event_name})

        prizes = hackathon_analysis.get("prizes", [])
        tracks = hackathon_analysis.get("tracks", [])
        judges_raw = hackathon_analysis.get("judges", [])

        log(
            f"✅ Found: {event_name} — "
            f"{len(prizes)} prizes · {len(tracks)} tracks · {len(judges_raw)} judges"
        )

        # ── Step 3: Deep judge research ───────────────────────────────────
        judges = []
        if not judges_raw:
            log("⚠️ No judges found on the page — skipping judge research", level="info")
        for i, judge_info in enumerate(judges_raw):
            jname = judge_info.get("name", f"Judge {i+1}")
            if not jname or jname.startswith("Judge "):
                log(f"⚠️ Skipping unnamed judge entry", level="info")
                continue
            log(f"🔍 Researching {jname} ({i+1}/{len(judges_raw)})…")

            try:
                research = await scraper.research_judge(judge_info, log_fn=log)
            except Exception as e:
                log(f"⚠️ Research failed for {jname}: {e}", level="error")
                research = {"linkedin_url": "", "linkedin_content": "", "website_url": "", "website_content": "", "papers_talks": [], "news": []}

            log(f"🤖 Analysing {jname} with Gemini…")
            try:
                analysis = analyzer.analyze_judge(judge_info, research)
            except Exception as e:
                log(f"⚠️ Analysis failed for {jname}: {e}", level="error")
                analysis = {"summary": f"{jname}", "confidence": "low", "expertise": [], "likes": [], "dislikes": []}

            judges.append({**judge_info, "research": research, "analysis": analysis})
            log(f"✅ {jname} — confidence: {analysis.get('confidence','?')}")

        db.update_job(job_id, {"judges": judges, "judge_count": len(judges)})

        # ── Step 4: Generate ideas ─────────────────────────────────────────
        log("💡 Generating 10 winning ideas…")
        ideas = analyzer.generate_ideas(hackathon_raw, hackathon_analysis, judges)
        if not ideas:
            log("⚠️ First idea generation returned empty — retrying…", level="info")
            ideas = analyzer.generate_ideas(hackathon_raw, hackathon_analysis, judges)
        db.update_job(job_id, {"ideas": ideas})
        log(f"✅ {len(ideas)} ideas generated")

        # ── Step 5: Build chat context ─────────────────────────────────────
        log("🧠 Indexing everything for chat…")
        ctx = analyzer.build_chat_context(hackathon_raw, hackathon_analysis, judges, ideas)
        db.update_job(job_id, {"chat_context": ctx})

        db.update_job(job_id, {"status": "complete"})
        log("🎉 Analysis complete! Time to win.", level="success")

    except Exception as exc:
        import traceback

        msg = str(exc)
        log(f"❌ Fatal error: {msg}", level="error")
        db.update_job(job_id, {"status": "error", "error": msg})
        traceback.print_exc()
    finally:
        # Clean up browser session
        await scraper.cleanup_crawler()


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    db.init()
    port = int(os.getenv("PORT", 5000))
    print(f"🚀 Win The Hackathon running on http://localhost:{port}")
    app.run(debug=True, threaded=True, port=port)
