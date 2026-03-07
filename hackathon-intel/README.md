# JudgeIntel — Hackathon Intelligence Platform

Paste any hackathon URL. Get deep intel on every judge. Win.

## What it does

1. Crawls the hackathon page (Devpost, MLH, custom sites — anything)
2. Extracts judges with Gemini 2.0 Flash
3. Launches **3 parallel research workers per judge**: LinkedIn, GitHub/personal site, news/papers/talks — all via Crawl4AI (no paid search API needed)
4. Builds a full strategic profile for each judge: what they like, dislike, how to pitch them
5. Generates 7–9 ranked project ideas tailored to this specific panel
6. Streams all of this live via SSE while it runs
7. Saves everything to SQLite so you can revisit past analyses

## Setup

### 1. Install dependencies

```bash
cd hackathon-intel
pip install -r requirements.txt
```

### 2. Set up Crawl4AI (one-time)

```bash
crawl4ai-setup
# or: python -m crawl4ai.install
# This installs the required Playwright browsers
```

### 3. Configure your API key

```bash
cp .env.example .env
# Edit .env and add your Gemini API key:
# GEMINI_API_KEY=your_key_here
```

Get a free Gemini API key at: https://aistudio.google.com/app/apikey

### 4. Run

```bash
python app.py
```

Open http://localhost:5000

## Usage

Paste any of these URL types:
- `https://yourchallenge.devpost.com`
- `https://mlh.io/events/some-hackathon`
- Any event page that lists judges

## Architecture

```
app.py          Flask app with SSE streaming endpoint
pipeline.py     Background orchestrator (runs in thread, uses asyncio)
crawler.py      All web crawling via Crawl4AI (3 workers per judge)
analyzer.py     Gemini 2.0 Flash for extraction and analysis
database.py     SQLite persistence for past analyses
templates/
  index.html    Single-page frontend (Tailwind + vanilla JS)
```

## Crawling Strategy (no paid APIs)

- **Worker 1**: DuckDuckGo HTML search → LinkedIn profiles
- **Worker 2**: DuckDuckGo HTML search → GitHub + personal sites
- **Worker 3**: DuckDuckGo HTML search → news articles, papers, talks

All search is done by crawling `https://html.duckduckgo.com/html/?q=...` directly — no API keys required.

## Notes

- LinkedIn often blocks scrapers; results vary. The app gracefully handles missing data.
- Gemini concurrent calls are throttled to 3 at a time to avoid rate limits.
- Deep research on 5 judges typically takes 2–4 minutes.
- All results are persisted in `hackathon_intel.db`.
