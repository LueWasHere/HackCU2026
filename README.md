# JudgeIntel — Hackathon Intelligence Platform

**HackCU 2026 Project**

Paste any hackathon URL. Get deep intelligence on every judge — and tailor your project to win.

Built during **HackCU 2026** as a rapid prototype exploring how AI-powered research pipelines can help hackers better understand judging panels and pitch more strategically.

---

# Overview

JudgeIntel analyzes hackathon judging panels and generates actionable insights to help teams craft stronger project ideas.

Simply provide a hackathon page URL and JudgeIntel will:

1. Crawl the event page (Devpost, MLH, or custom sites)
2. Extract the list of judges
3. Perform automated research on each judge
4. Build strategic profiles
5. Generate project ideas tailored to that specific judging panel

All analysis streams live while the system runs and is saved for later viewing.

---

# Features

## Automated Judge Discovery
- Crawls hackathon pages to locate and extract judges
- Works with Devpost, MLH, and many custom event sites

## Parallel Judge Research
For every judge, the system launches **three parallel research workers**:

- LinkedIn profile discovery
- GitHub / personal website discovery
- News articles, talks, and research papers

## Strategic Judge Profiles
Each judge profile includes insights such as:

- Professional background
- Areas of expertise
- Technical interests
- Signals about what they value in projects

## AI-Generated Project Ideas
JudgeIntel generates **7–9 ranked project ideas** tailored to the specific panel of judges.

This helps teams align their project with:

- judge expertise
- company interests
- emerging technical trends

## Live Streaming Results
All research and analysis streams live using **Server-Sent Events (SSE)** while the pipeline runs.

## Persistent History
All analyses are stored in **SQLite**, allowing teams to revisit past hackathons and judge panels.

---

# Tech Stack

**Backend**
- Python
- Flask
- SQLite

**AI**
- Gemini 2.0 Flash

**Web Crawling**
- Crawl4AI
- Playwright browsers

**Frontend**
- HTML
- Tailwind CSS
- Vanilla JavaScript

---

# Architecture

app.py  
Flask app with SSE streaming endpoint

pipeline.py  
Background orchestration (thread + asyncio)

crawler.py  
Web crawling using Crawl4AI  
Launches 3 research workers per judge

analyzer.py  
Judge extraction + analysis using Gemini 2.0 Flash

database.py  
SQLite storage for past analyses

templates/index.html  
Single page frontend (Tailwind + vanilla JS)

---

# Crawling Strategy (No Paid APIs)

JudgeIntel performs search by directly crawling DuckDuckGo HTML search results.

Workers perform the following searches:

**Worker 1**
LinkedIn profiles

**Worker 2**
GitHub and personal websites

**Worker 3**
News articles, papers, interviews, and talks

Search queries are executed through:

https://html.duckduckgo.com/html/?q=...

This avoids paid search APIs while still enabling broad discovery.

---

# Setup

## 1. Clone the repository

```
git clone https://github.com/LueWasHere/HackCU2026.git
cd HackCU2026
```

## 2. Install dependencies

```
pip install -r requirements.txt
```

## 3. Install Crawl4AI browsers (one-time setup)

```
crawl4ai-setup
```

or

```
python -m crawl4ai.install
```

## 4. Configure your API key

```
cp .env.example .env
```

Add your Gemini API key to `.env`:

```
GEMINI_API_KEY=your_key_here
```

Get a free key here:  
https://aistudio.google.com/app/apikey

## 5. Run the application

```
python app.py
```

Then open:

http://localhost:5000

---

# Usage

Paste any hackathon event URL into the interface.

Examples:

https://yourchallenge.devpost.com  
https://mlh.io/events/some-hackathon

The system will:

1. Extract judges
2. Launch research workers
3. Build judge profiles
4. Generate tailored project ideas

Typical runtime for **5 judges**: ~2–4 minutes.

---

# Notes

- LinkedIn often blocks scraping, so results may vary.
- Gemini calls are throttled to avoid rate limits.
- All results are stored in:

hackathon_intel.db

---

# Future Improvements

- Improve judge discovery reliability
- Better LinkedIn extraction
- Enhanced idea ranking
- UI/UX improvements
- Exportable reports
- Team collaboration features

---

# Team

Adam Duncan  
Wyatt Greene

---

# HackCU 2026

This project was built during **HackCU 2026** as a fast-paced hackathon prototype focused on experimentation, AI tooling, and rapid iteration.
