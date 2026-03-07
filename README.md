# HackCU 2026 - JudgeIntel

JudgeIntel is a hackathon intelligence app built at HackCU 2026.

You provide a hackathon event URL, and the system:
- crawls the event page,
- extracts judge information,
- researches each judge across the web,
- builds judge profiles (likes, dislikes, interests, and pitch guidance),
- generates ranked project ideas tailored to that specific panel.

The app includes a live progress UI (Server-Sent Events) and persists results in SQLite.

## Team

- Adam Duncan
- Wyatt Greene

## Repository Layout

- `README.md` - top-level project documentation
- `hackathon-intel/` - main application
  - `app.py` - Flask web app + API routes + SSE streaming endpoint
  - `pipeline.py` - orchestration for crawl -> extract -> research -> analyze
  - `crawler.py` - Crawl4AI-based web crawling and source collection workers
  - `analyzer.py` - Gemini-powered extraction and profile generation
  - `database.py` - SQLite persistence helpers
  - `templates/index.html` - frontend interface (Tailwind + vanilla JS)
  - `requirements.txt` - Python dependencies
  - `hackathon_intel.db` - SQLite database file (generated/updated at runtime)

## Core Features

- Hackathon page crawling (Devpost and generic event pages)
- AI extraction of:
  - hackathon metadata,
  - judges list
- Multi-worker judge research pipeline (runs in parallel)
- Per-judge intelligence including:
  - expertise,
  - industries,
  - likes/dislikes,
  - pitch strategy,
  - key insights,
  - supporting sources
- Judge-panel aggregate analysis
- Ranked, panel-specific project ideas
- Real-time pipeline progress in the browser via SSE
- Local history of analyses in SQLite

## High-Level Architecture

1. User submits a URL in the frontend (`/`).
2. Backend creates a job ID and starts a background thread.
3. Thread runs an async pipeline:
   - crawl hackathon page,
   - extract judges + event metadata,
   - research judges with parallel workers,
   - generate structured judge profiles,
   - synthesize final analysis + project ideas.
4. Pipeline emits events to a per-job queue.
5. Frontend listens on `/stream/<job_id>` and updates live.
6. Final payload is saved in SQLite and can be reloaded from history.

## Tech Stack

- Backend: Python + Flask
- Crawling: Crawl4AI (Playwright-backed)
- AI: Google Gemini via `google.genai`
- Frontend: HTML + Tailwind CSS + vanilla JavaScript
- Storage: SQLite

## Prerequisites

- Python 3.10+ recommended
- `pip`
- Network access for crawling and AI calls
- Gemini API key

## Setup

### 1. Clone

```bash
git clone https://github.com/LueWasHere/HackCU2026.git
cd HackCU2026/hackathon-intel
```

### 2. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Install Crawl4AI browser dependencies (one-time)

```bash
crawl4ai-setup
```

If that command is unavailable:

```bash
python -m crawl4ai.install
```

### 5. Configure environment variables

Create a `.env` file in `hackathon-intel/`:

```bash
GEMINI_API_KEY=your_api_key_here
```

Get a key from Google AI Studio.

## Run the App

From `hackathon-intel/`:

```bash
python3 app.py
```

Open:

- `http://localhost:5000`

## API Endpoints

- `GET /`
  - Serves the web UI.

- `POST /analyze`
  - Body: `{ "url": "https://..." }`
  - Starts a new analysis job.
  - Returns `{ "job_id": "..." }`.

- `GET /stream/<job_id>`
  - SSE endpoint for live progress updates.

- `GET /results/<job_id>`
  - Returns saved job + final result payload.

- `GET /history`
  - Returns previous analyses from SQLite.

## Pipeline Stages

The analysis pipeline runs in four major stages:

1. Crawl Event Page
- Fetches main event content.
- For Devpost links, also attempts additional detail coverage.

2. Extract Event + Judges
- Gemini parses event text into structured JSON.
- Produces hackathon metadata and initial judge list.

3. Judge Research (parallel workers)
- Runs multiple source-specific workers (LinkedIn, GitHub/site, news, social, academic, company, talks, transcripts, general web, podcasts/HN).
- Consolidates crawled evidence and source links.

4. Final Synthesis
- Builds detailed judge profiles.
- Computes panel-level summary and recommendations.
- Generates ranked project ideas.

## Data Model (Conceptual)

Final result payload includes:

- `hackathon`: name, theme, tracks, prizes, timeline, organizer, etc.
- `judges[]`: enriched per-judge profile fields (bio, expertise, likes/dislikes, links, confidence, sources)
- `aggregate_stats`: panel summary and consensus signals
- `project_ideas[]`: ranked ideas with rationale, stack, and judge appeal

## Known Limitations

- LinkedIn is intentionally difficult to crawl; some profiles/photos may be missing.
- Output quality depends on page structure and public web presence of judges.
- Large judge lists can increase runtime significantly.
- AI extraction can occasionally miss or misclassify sparse page content.

## Troubleshooting

- `GEMINI_API_KEY is not set`
  - Ensure `.env` exists in `hackathon-intel/` and includes `GEMINI_API_KEY=...`.

- Crawl/browser errors
  - Re-run `crawl4ai-setup` (or `python -m crawl4ai.install`).

- Very slow analyses
  - This is expected for judge-heavy pages; each judge triggers multiple crawls and AI calls.

- No judges found
  - Try a URL that directly includes the event judges section.

## Development Notes

- The current app runs Flask in debug mode on port `5000`.
- Results are stored in `hackathon_intel.db` in the app directory.
- Frontend is intentionally dependency-light and uses vanilla JS for render logic.

## Suggested Next Improvements

- Add automated tests for crawler normalization and SSE event flow.
- Add stronger URL/entity disambiguation for judge profile matching.
- Add retry/backoff strategy for transient crawl/API failures.
- Add export options (JSON/PDF report).
- Add Dockerfile and one-command local startup.

## License

MIT License (see [LICENSE](LICENSE)).
