import os
import json
import re
import asyncio
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

# Semaphore to avoid blasting Gemini API with too many concurrent requests
_gemini_semaphore = asyncio.Semaphore(3)


class HackathonAnalyzer:
    def __init__(self):
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if not api_key:
            raise ValueError("GEMINI_API_KEY is not set in your .env file.")
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel("gemini-2.0-flash")

    async def _generate(self, prompt: str, expect_json: bool = True):
        """Call Gemini with semaphore-controlled concurrency."""
        async with _gemini_semaphore:
            loop = asyncio.get_event_loop()
            try:
                response = await loop.run_in_executor(
                    None, lambda: self.model.generate_content(prompt)
                )
                text = response.text.strip()
            except Exception as e:
                if expect_json:
                    return {}
                return f"Error: {e}"

        if not expect_json:
            return text

        # Strip markdown code fences
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.MULTILINE)
        text = re.sub(r"\s*```$", "", text, flags=re.MULTILINE)
        text = text.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Try to find a JSON block inside the text
            match = re.search(r"\{[\s\S]*\}", text)
            if match:
                try:
                    return json.loads(match.group())
                except Exception:
                    pass
            return {}

    async def extract_hackathon_info(self, page_content: str, url: str) -> dict:
        """Extract hackathon metadata and judge list from the crawled page."""
        prompt = f"""You are a data extraction specialist. Extract all information from this hackathon page.

URL: {url}

PAGE CONTENT:
{page_content[:14000]}

Return a JSON object with EXACTLY this structure. Do not add extra fields. Be thorough extracting every judge you can find.

{{
  "hackathon": {{
    "name": "full hackathon name",
    "theme": "main theme or focus area",
    "description": "2-3 sentence summary",
    "prizes": ["list of prizes with amounts if known"],
    "tracks": ["competition tracks if multiple"],
    "requirements": ["key requirements or rules"],
    "technologies": ["required or featured technologies"],
    "dates": {{"start": "...", "end": "...", "submission_deadline": "..."}},
    "organizer": "organizing company or institution",
    "location": "city or 'Virtual'"
  }},
  "judges": [
    {{
      "name": "full name",
      "title": "job title",
      "company": "current employer",
      "bio": "any bio text found on page",
      "photo_url": "direct image URL if found, else null"
    }}
  ]
}}

Extract EVERY judge listed. If no judges section exists, look for 'mentors', 'reviewers', or 'panel'. Return ONLY the JSON object.
"""
        return await self._generate(prompt)

    async def analyze_judge(self, judge_basic: dict, research_data: dict) -> dict:
        """Build a deep judge profile from all gathered research data."""
        name = judge_basic.get("name", "Unknown")
        company = judge_basic.get("company", "")
        title = judge_basic.get("title", "")

        # Combine all research worker outputs
        sections = []
        all_sources = []
        for key, data in research_data.items():
            if isinstance(data, dict):
                content = data.get("content", "")
                sources = data.get("sources", [])
                source_label = data.get("source", key)
                if content and len(content) > 50:
                    sections.append(f"=== {source_label.upper()} ===\n{content[:3500]}")
                all_sources.extend(sources)

        combined_research = "\n\n".join(sections) if sections else "No research data retrieved."

        prompt = f"""You are a hackathon intelligence analyst. Build a detailed judge profile to help participants win.

JUDGE: {name}
TITLE: {title}
COMPANY: {company}
BIO FROM HACKATHON PAGE: {judge_basic.get('bio', 'N/A')}

RESEARCH GATHERED FROM ACROSS THE WEB:
{combined_research[:11000]}

Analyze all this data carefully. Return a JSON profile optimized for hackathon strategy:

{{
  "name": "{name}",
  "title": "{title}",
  "company": "{company}",
  "bio": "2-3 compelling sentences about who they are professionally",
  "expertise": ["specific domain1", "specific domain2", "..."],
  "industries": ["industry they know deeply", "..."],
  "current_focus": "what they're working on or thinking about right now",
  "likes": [
    "specific thing they value (e.g. 'clean APIs and developer experience')",
    "..."
  ],
  "dislikes": [
    "specific thing they penalize or dislike (e.g. 'projects without clear revenue model')",
    "..."
  ],
  "key_insights": [
    "actionable insight for presenting to this judge (e.g. 'Lead with enterprise adoption angle')",
    "..."
  ],
  "notable_work": ["project, company, or paper they're known for", "..."],
  "personality": "brief read on their professional personality and style",
  "best_pitch_approach": "1-2 sentence concrete strategy for impressing THIS judge specifically",
  "linkedin_url": "url or null",
  "github_url": "url or null",
  "website_url": "url or null",
  "photo_url": "{judge_basic.get('photo_url', '')}",
  "research_confidence": "high/medium/low",
  "research_sources_count": {len(all_sources)}
}}

Be SPECIFIC and ACTIONABLE. Vague generalities are useless. If research data is limited, extrapolate intelligently from their title/company/bio. Return ONLY JSON.
"""
        result = await self._generate(prompt)
        if isinstance(result, dict) and result:
            # Merge with basic info, preferring analyzed result
            merged = {**judge_basic, **result}
            merged["research_sources"] = all_sources
            return merged
        return {**judge_basic, "research_sources": all_sources}

    async def generate_final_analysis(self, hackathon: dict, judges: list[dict]) -> dict:
        """Generate aggregate judge stats and ranked project ideas."""
        judges_payload = json.dumps(
            [
                {
                    "name": j.get("name"),
                    "title": j.get("title"),
                    "company": j.get("company"),
                    "expertise": j.get("expertise", []),
                    "industries": j.get("industries", []),
                    "current_focus": j.get("current_focus", ""),
                    "likes": j.get("likes", []),
                    "dislikes": j.get("dislikes", []),
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

Return a comprehensive JSON analysis:

{{
  "aggregate_stats": {{
    "panel_summary": "2-3 sentence summary of who this panel collectively is",
    "top_industries": [
      {{
        "name": "industry name",
        "count": N,
        "percentage": N,
        "judges": ["judge names interested in this"]
      }}
    ],
    "top_technologies": [
      {{
        "name": "technology or framework",
        "interest_level": "high/medium",
        "judges": ["judge names"]
      }}
    ],
    "consensus_liked": [
      "thing the majority of judges value (be specific)"
    ],
    "consensus_disliked": [
      "thing the majority of judges penalize (be specific)"
    ],
    "dominant_archetype": "e.g. 'Mostly enterprise SaaS founders who care about B2B monetization'",
    "judging_style": "how this panel tends to evaluate projects",
    "biggest_opportunity": "the single most underserved area that would excite the majority of this panel"
  }},
  "project_ideas": [
    {{
      "rank": 1,
      "title": "Specific, memorable project name",
      "tagline": "One punchy sentence that sells it",
      "description": "2-3 sentences explaining what it does and why it matters",
      "why_this_wins": "Specific reason this judge panel will love it — reference actual judges by name",
      "tech_stack": ["technology1", "technology2", "technology3"],
      "industries": ["primary industry"],
      "difficulty": "Easy / Medium / Hard",
      "track": "which hackathon track this fits best",
      "judge_appeal": {{
        "strong": ["judge names most likely to love this"],
        "weak": ["judge names less likely to resonate — and why"]
      }},
      "execution_tips": [
        "specific tip for building this in a hackathon timeframe"
      ],
      "demo_strategy": "how to demo this for maximum impact"
    }}
  ]
}}

Generate 7-9 project ideas ranked by total likelihood to win given THIS specific judge panel and hackathon context. Be creative, specific, and strategic. Ideas should span different difficulty levels. Return ONLY JSON.
"""
        return await self._generate(prompt)
