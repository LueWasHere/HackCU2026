import os
import json
import asyncio
import logging
from google import genai
from google.genai import types
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

load_dotenv()

# Semaphore to avoid blasting Gemini API with too many concurrent requests
_gemini_semaphore = asyncio.Semaphore(6)

# ── JSON Schemas ──────────────────────────────────────────────────────────────

HACKATHON_EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "hackathon": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "theme": {"type": "string"},
                "description": {"type": "string"},
                "prizes": {"type": "array", "items": {"type": "string"}},
                "tracks": {"type": "array", "items": {"type": "string"}},
                "requirements": {"type": "array", "items": {"type": "string"}},
                "technologies": {"type": "array", "items": {"type": "string"}},
                "dates": {
                    "type": "object",
                    "properties": {
                        "start": {"type": "string"},
                        "end": {"type": "string"},
                        "submission_deadline": {"type": "string"},
                    },
                },
                "organizer": {"type": "string"},
                "location": {"type": "string"},
            },
            "required": ["name"],
        },
        "judges": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "title": {"type": "string"},
                    "company": {"type": "string"},
                    "bio": {"type": "string"},
                    "photo_url": {"type": "string"},
                },
                "required": ["name"],
            },
        },
    },
    "required": ["hackathon", "judges"],
}

JUDGE_PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "title": {"type": "string"},
        "company": {"type": "string"},
        "bio": {"type": "string"},
        "expertise": {"type": "array", "items": {"type": "string"}},
        "industries": {"type": "array", "items": {"type": "string"}},
        "technologies": {"type": "array", "items": {"type": "string"}},
        "current_focus": {"type": "string"},
        "recent_activity": {"type": "string"},
        "likes": {"type": "array", "items": {"type": "string"}},
        "dislikes": {"type": "array", "items": {"type": "string"}},
        "hot_takes": {"type": "array", "items": {"type": "string"}},
        "key_insights": {"type": "array", "items": {"type": "string"}},
        "notable_work": {"type": "array", "items": {"type": "string"}},
        "career_trajectory": {"type": "string"},
        "personality": {"type": "string"},
        "judging_prediction": {"type": "string"},
        "best_pitch_approach": {"type": "string"},
        "buzzwords_to_use": {"type": "array", "items": {"type": "string"}},
        "topics_to_avoid": {"type": "array", "items": {"type": "string"}},
        "linkedin_url": {"type": "string"},
        "github_url": {"type": "string"},
        "twitter_url": {"type": "string"},
        "website_url": {"type": "string"},
        "photo_url": {"type": "string"},
        "research_confidence": {"type": "string"},
        "research_sources_count": {"type": "integer"},
    },
    "required": ["name", "bio", "expertise", "likes", "key_insights", "research_confidence"],
}

FINAL_ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "aggregate_stats": {
            "type": "object",
            "properties": {
                "panel_summary": {"type": "string"},
                "top_industries": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "count": {"type": "integer"},
                            "percentage": {"type": "integer"},
                            "judges": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
                "top_technologies": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "interest_level": {"type": "string"},
                            "judges": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
                "consensus_liked": {"type": "array", "items": {"type": "string"}},
                "consensus_disliked": {"type": "array", "items": {"type": "string"}},
                "dominant_archetype": {"type": "string"},
                "judging_style": {"type": "string"},
                "biggest_opportunity": {"type": "string"},
            },
        },
        "project_ideas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "rank": {"type": "integer"},
                    "title": {"type": "string"},
                    "tagline": {"type": "string"},
                    "description": {"type": "string"},
                    "why_this_wins": {"type": "string"},
                    "tech_stack": {"type": "array", "items": {"type": "string"}},
                    "industries": {"type": "array", "items": {"type": "string"}},
                    "difficulty": {"type": "string"},
                    "track": {"type": "string"},
                    "judge_appeal": {
                        "type": "object",
                        "properties": {
                            "strong": {"type": "array", "items": {"type": "string"}},
                            "weak": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                    "execution_tips": {"type": "array", "items": {"type": "string"}},
                    "demo_strategy": {"type": "string"},
                },
            },
        },
    },
    "required": ["aggregate_stats", "project_ideas"],
}


# ── Analyzer ──────────────────────────────────────────────────────────────────

class HackathonAnalyzer:
    def __init__(self):
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise ValueError("GEMINI_API_KEY is not set in your .env file.")
        self.client = genai.Client(api_key=api_key)
        self.model_name = "gemini-3-flash-preview"

    async def _generate(self, prompt: str, schema: dict = None, raise_on_limit: bool = False) -> dict:
        """Call Gemini with structured JSON output and semaphore-controlled concurrency."""
        async with _gemini_semaphore:
            loop = asyncio.get_event_loop()
            try:
                config = types.GenerateContentConfig(
                    response_mime_type="application/json",
                )
                if schema:
                    config.response_schema = schema

                response = await loop.run_in_executor(
                    None,
                    lambda: self.client.models.generate_content(
                        model=self.model_name,
                        contents=prompt,
                        config=config,
                    ),
                )
                text = response.text.strip()
            except Exception as e:
                err_msg = str(e).lower()
                if raise_on_limit and any(
                    kw in err_msg
                    for kw in ("token", "limit", "too long", "too large", "max_tokens", "context_length", "quota")
                ):
                    raise
                logger.warning(f"[_generate] Gemini error: {e}")
                return {}

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            logger.warning(f"[_generate] JSON parse failed. First 300 chars: {text[:300]}")
            return {}

    async def extract_hackathon_info(self, page_content: str, url: str) -> dict:
        """Extract hackathon metadata and judge list from the crawled page.

        Tries escalating content windows so judges buried deep in the page
        are found, and token-limit errors are handled gracefully.
        """
        content_limits = [8000, 20000, 40000, 70000, len(page_content)]
        seen = set()
        limits = []
        for lim in content_limits:
            clamped = min(lim, len(page_content))
            if clamped not in seen:
                seen.add(clamped)
                limits.append(clamped)

        last_error = None
        result = {}
        for attempt, limit in enumerate(limits, 1):
            logger.info(f"[extract] attempt {attempt}/{len(limits)} — sending {limit} chars to Gemini")
            prompt = f"""You are a data extraction specialist. Extract all information from this hackathon page.

URL: {url}

PAGE CONTENT:
{page_content[:limit]}

Extract EVERY judge listed. If no judges section exists, look for 'mentors', 'reviewers', or 'panel'. Be thorough — do not miss any judges."""

            try:
                result = await self._generate(prompt, schema=HACKATHON_EXTRACT_SCHEMA, raise_on_limit=True)
            except Exception as e:
                last_error = e
                logger.warning(f"[extract] attempt {attempt} failed ({type(e).__name__}): {e}")
                if limit >= len(page_content):
                    break
                continue

            judges = result.get("judges", []) if isinstance(result, dict) else []
            if judges:
                logger.info(f"[extract] found {len(judges)} judges at {limit} chars")
                return result

            logger.info(f"[extract] no judges found at {limit} chars, escalating...")
            last_error = None

        if last_error:
            return {}
        return result if isinstance(result, dict) else {}

    async def analyze_judge(self, judge_basic: dict, research_data: dict) -> dict:
        """Build a deep judge profile from all gathered research data."""
        name = judge_basic.get("name", "Unknown")
        company = judge_basic.get("company", "")
        title = judge_basic.get("title", "")

        sections = []
        all_sources = []
        for key, data in research_data.items():
            if isinstance(data, dict):
                content = data.get("content", "")
                sources = data.get("sources", [])
                source_label = data.get("source", key)
                if content and len(content) > 50:
                    sections.append(f"=== {source_label.upper()} ===\n{content[:6000]}")
                all_sources.extend(sources)

        combined_research = "\n\n".join(sections) if sections else "No research data retrieved."

        prompt = f"""You are a hackathon intelligence analyst. Build a detailed judge profile to help participants win.

JUDGE: {name}
TITLE: {title}
COMPANY: {company}
BIO FROM HACKATHON PAGE: {judge_basic.get('bio', 'N/A')}

RESEARCH GATHERED FROM ACROSS THE WEB (10 sources: LinkedIn, GitHub, News, Social Media, Academic, Company, Talks, YouTube Transcripts, General Web, Podcasts/HN):
{combined_research[:32000]}

Analyze ALL this data carefully — LinkedIn experience, GitHub repos, tweets, blog posts, papers, talks, company info.

Be EXTREMELY SPECIFIC and ACTIONABLE. Reference actual evidence from the research data. Vague generalities are useless — "values innovation" is worthless, "pushed 3 Rust crates in the last month and tweeted 'every team should try Rust'" is gold. If research data is limited, extrapolate intelligently from their title/company/bio but set research_confidence to "low"."""

        result = await self._generate(prompt, schema=JUDGE_PROFILE_SCHEMA)
        if isinstance(result, dict) and result.get("name"):
            merged = {**judge_basic, **result}
            merged["research_sources"] = all_sources
            merged["research_sources_count"] = len(all_sources)
            logger.info(f"[analyze_judge] {name}: got {len(result)} keys — "
                        f"confidence={result.get('research_confidence', '?')}")
            return merged
        logger.warning(f"[analyze_judge] {name}: Gemini returned empty/invalid result: {repr(result)[:200]}")
        return {**judge_basic, "research_sources": all_sources, "research_confidence": "low"}

    async def generate_final_analysis(self, hackathon: dict, judges: list[dict]) -> dict:
        """Generate aggregate judge stats and ranked project ideas."""
        judges_payload = json.dumps(
            [
                {
                    "name": j.get("name"),
                    "title": j.get("title"),
                    "company": j.get("company"),
                    "expertise": j.get("expertise", []),
                    "technologies": j.get("technologies", []),
                    "industries": j.get("industries", []),
                    "current_focus": j.get("current_focus", ""),
                    "likes": j.get("likes", []),
                    "dislikes": j.get("dislikes", []),
                    "hot_takes": j.get("hot_takes", []),
                    "judging_prediction": j.get("judging_prediction", ""),
                    "buzzwords_to_use": j.get("buzzwords_to_use", []),
                }
                for j in judges
            ],
            indent=2,
        )

        prompt = f"""You are a hackathon strategy expert. Analyze this judge panel and generate winning project ideas.

HACKATHON DETAILS:
{json.dumps(hackathon, indent=2)}

JUDGE PANEL ({len(judges)} judges):
{judges_payload}

Generate 7-9 project ideas ranked by total likelihood to win given THIS specific judge panel and hackathon context. Be creative, specific, and strategic. Ideas should span different difficulty levels. Reference judges by name in why_this_wins and judge_appeal fields."""

        logger.info(f"[final_analysis] Sending {len(judges)} judges to Gemini for final synthesis...")
        result = await self._generate(prompt, schema=FINAL_ANALYSIS_SCHEMA)
        if not isinstance(result, dict) or not result:
            logger.warning(f"[final_analysis] Gemini returned empty/invalid: {repr(result)[:200]}")
            return {}
        logger.info(f"[final_analysis] Got {len(result.get('project_ideas', []))} project ideas, "
                     f"agg keys: {list(result.get('aggregate_stats', {}).keys())}")
        return result
