"""
analyzer.py – all Gemini AI calls.

Uses the new google-genai SDK (pip install google-genai).
Model default: gemini-2.0-flash  (override with GEMINI_MODEL env var).
"""

import json
import os
import re
import time

from dotenv import load_dotenv

load_dotenv()

# ── Client setup ──────────────────────────────────────────────────────────────
try:
    from google import genai
    from google.genai import types as gtypes

    _client = None

    def _get_client():
        global _client
        if _client is None:
            api_key = os.getenv("GEMINI_API_KEY")
            if not api_key:
                raise RuntimeError("GEMINI_API_KEY env var not set")
            _client = genai.Client(api_key=api_key)
        return _client

    def _call(prompt: str, max_tokens: int = 2_000, temperature: float = 0.7) -> str:
        model = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
        client = _get_client()
        start = time.time()
        try:
            resp = client.models.generate_content(
                model=model,
                contents=prompt,
                config=gtypes.GenerateContentConfig(
                    max_output_tokens=max_tokens,
                    temperature=temperature,
                ),
            )
            elapsed = time.time() - start
            text = resp.text or ""
            # Write to file for debugging since stdout may not be visible in threads
            with open("/tmp/analyzer_debug.log", "a") as f:
                f.write(f"[analyzer] API call completed in {elapsed:.2f}s, response length: {len(text)}\n")
            return text
        except Exception as e:
            elapsed = time.time() - start
            with open("/tmp/analyzer_debug.log", "a") as f:
                f.write(f"[analyzer] API call failed after {elapsed:.2f}s: {e}\n")
            raise

except ImportError:
    # Fallback to older google-generativeai SDK
    import google.generativeai as genai_old  # type: ignore

    def _call(prompt: str, max_tokens: int = 2_000, temperature: float = 0.7) -> str:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY not set")
        genai_old.configure(api_key=api_key)
        model_name = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
        model = genai_old.GenerativeModel(model_name)
        resp = model.generate_content(
            prompt,
            generation_config=genai_old.types.GenerationConfig(
                max_output_tokens=max_tokens,
                temperature=temperature,
            ),
        )
        return resp.text or ""


# ── JSON parsing ─────────────────────────────────────────────────────────────-

def _parse_json(text: str):
    if not text:
        return {}
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()
    try:
        return json.loads(text)
    except Exception as e:
        # Try to extract JSON object/array with braces/brackets
        m = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                pass
        # Try to fix truncated JSON (missing closing braces)
        open_braces = text.count('{') - text.count('}')
        if open_braces > 0:
            try:
                fixed = text + ('}' * open_braces)
                return json.loads(fixed)
            except Exception:
                pass
    return {}


# ── Public API ────────────────────────────────────────────────────────────────

def analyze_hackathon(hackathon_data: dict) -> dict:
    """Extract all structured info from the raw hackathon page content."""
    content = (hackathon_data.get("raw_content") or "")[:25_000]
    url = hackathon_data.get("url", "")

    # Pass linkedin links as context for judge extraction
    linkedin_links = hackathon_data.get("linkedin_links", [])
    linkedin_str = "\n".join(
        f"- {l['text']}: {l['href']}" for l in linkedin_links[:20]
    )

    prompt = f"""You are analyzing a hackathon event page. Extract every piece of useful information.

URL: {url}

LINKEDIN LINKS FOUND ON PAGE:
{linkedin_str or "(none found)"}

PAGE CONTENT:
{content}

Return a JSON object with these fields (use empty arrays/strings if not found):
- name: official hackathon name
- description: 2-3 sentence description  
- dates: event dates
- location: virtual/in-person/hybrid + city
- prizes: array of {{place, amount, description}}
- tracks: array of {{name, description, focus}}
- timeline: array of {{event, date}}
- requirements: array of strings
- themes: array of strings
- judges: array of {{name, title, company, photo_url, linkedin_url, bio}}
- sponsors: array of strings
- technologies: array of strings
- judging_criteria: array of strings
- key_focus_areas: array of strings
- summary: strategic overview of what this hackathon wants

Be concise. Return ONLY valid JSON. No markdown, no explanation.
"""
    try:
        raw = _call(prompt, max_tokens=8_000)
        with open("/tmp/analyzer_debug.log", "a") as f:
            f.write(f"[analyzer] hackathon: got response, length={len(raw)}\n")
            f.write(f"[analyzer] hackathon: FULL RESPONSE:\n{raw}\n---END RESPONSE---\n")
        result = _parse_json(raw)
        with open("/tmp/analyzer_debug.log", "a") as f:
            f.write(f"[analyzer] hackathon: parsed result type={type(result)}, keys={list(result.keys()) if isinstance(result, dict) else 'N/A'}\n")
        if not isinstance(result, dict):
            with open("/tmp/analyzer_debug.log", "a") as f:
                f.write(f"[analyzer] hackathon: result is not dict, returning empty\n")
            result = {}
        return result
    except Exception as e:
        with open("/tmp/analyzer_debug.log", "a") as f:
            f.write(f"[analyzer] hackathon analysis error: {e}\n")
            import traceback
            f.write(traceback.format_exc())
        return {}


def analyze_judge(judge: dict, research: dict) -> dict:
    """Deep personality + preference analysis for one judge."""
    name = judge.get("name", "Unknown")
    title = judge.get("title", "")
    company = judge.get("company", "")
    bio = (judge.get("bio") or "")[:800]

    linkedin = (research.get("linkedin_content") or "")[:3_500]
    website = (research.get("website_content") or "")[:3_500]
    papers = "\n---\n".join(
        p.get("content", "")[:1_200] for p in research.get("papers_talks", [])
    )[:3_500]
    news = "\n---\n".join(
        n.get("content", "")[:800] for n in research.get("news", [])
    )[:2_000]

    prompt = f"""You are a competitive intelligence analyst for a hackathon team.
Analyze this judge and give deep, actionable insights.

JUDGE: {name}
TITLE: {title}
COMPANY: {company}
BIO: {bio}

LINKEDIN DATA:
{linkedin or "(not available)"}

PERSONAL WEBSITE / BLOG:
{website or "(not available)"}

TALKS & PAPERS:
{papers or "(not available)"}

NEWS & INTERVIEWS:
{news or "(not available)"}

Return a JSON object:
{{
  "summary": "2-3 sentence professional summary",
  "background_type": "technical | business | academic | hybrid",
  "expertise": ["specific area 1", "specific area 2", "specific area 3"],
  "technical_skills": ["Python", "ML", "distributed systems"],
  "industry_focus": ["fintech", "healthcare", "devtools"],
  "interests": ["what they personally care about"],
  "likes": [
    {{"item": "what they love to see in projects", "reason": "why they value this"}}
  ],
  "dislikes": [
    {{"item": "what puts them off", "reason": "why it's a red flag for them"}}
  ],
  "hot_topics": ["2-3 topics they're currently excited about"],
  "keywords_to_use": ["specific words and phrases they use themselves"],
  "score_weight": "what they weight most: innovation | impact | technical | business | demo polish",
  "how_to_impress": "2-3 specific, tactical actions to win this judge over",
  "how_to_avoid": "2-3 specific mistakes that will kill your score with this judge",
  "tip": "single best tactical tip for this judge — be specific and bold",
  "confidence": "high | medium | low (based on data quality)"
}}

Return ONLY valid JSON. No explanation.
"""
    try:
        raw = _call(prompt, max_tokens=2_500)
        result = _parse_json(raw)
        if not isinstance(result, dict):
            result = {}
        return result
    except Exception as e:
        print(f"[analyzer] judge analysis error for {name}: {e}")
        return {
            "summary": f"{name} is a {title} at {company}.",
            "expertise": [],
            "likes": [],
            "dislikes": [],
            "tip": "Research this judge manually for the best results.",
            "confidence": "low",
        }


def generate_ideas(hackathon_data: dict, hackathon_analysis: dict, judges: list) -> list:
    """Generate 10 winning project ideas tailored to the judges and event."""
    judge_profiles = []
    for j in judges:
        a = j.get("analysis", {})
        judge_profiles.append({
            "name": j.get("name"),
            "title": j.get("title"),
            "company": j.get("company"),
            "background_type": a.get("background_type", ""),
            "expertise": a.get("expertise", []),
            "likes": [
                (x.get("item") if isinstance(x, dict) else x)
                for x in a.get("likes", [])
            ],
            "hot_topics": a.get("hot_topics", []),
            "keywords": a.get("keywords_to_use", []),
            "score_weight": a.get("score_weight", ""),
        })

    def _merge(key):
        return (
            hackathon_data.get(key)
            or hackathon_analysis.get(key)
            or []
        )

    prizes = _merge("prizes")
    tracks = _merge("tracks")
    themes = _merge("themes")
    criteria = _merge("judging_criteria")
    technologies = _merge("technologies")
    focus_areas = _merge("key_focus_areas")
    summary = (
        hackathon_analysis.get("summary")
        or hackathon_data.get("description")
        or ""
    )

    prompt = f"""You are a world-class hackathon strategist with a 80% win rate.
Generate exactly 10 distinct, winning project ideas for this specific hackathon.
Each idea MUST be tailored to specific judges' documented preferences.

═══ HACKATHON ═══
Name: {hackathon_data.get("name", "")}
Summary: {summary}
Tracks: {json.dumps(tracks)}
Prizes: {json.dumps(prizes)}
Themes: {json.dumps(themes)}
Judging Criteria: {json.dumps(criteria)}
Technologies: {json.dumps(technologies)}
Key Focus Areas: {json.dumps(focus_areas)}

═══ JUDGES ═══
{json.dumps(judge_profiles, indent=2)}

═══ INSTRUCTIONS ═══
- Mix bold/innovative ideas with safe/solid ones
- Each idea should have a different primary appeal (different judges or criteria)
- Be specific — real tech stack, real problem, real demo-able outcome
- Include a sharp pitch opening line a team could say on stage
- Score honestly (0-100) against this panel

Return a JSON array of EXACTLY 10 objects:
[
  {{
    "rank": 1,
    "title": "Catchy project name (2-5 words)",
    "tagline": "One compelling sentence (max 15 words)",
    "description": "3-4 sentences: what it does, who it helps, why it matters",
    "problem_solved": "The real, specific problem being solved",
    "solution": "How it works technically (2-3 sentences)",
    "tech_stack": ["React", "Python FastAPI", "OpenAI"],
    "tracks": ["which tracks this fits"],
    "judge_alignment": {{
      "Judge Name": "specific reason this appeals to them"
    }},
    "overall_score": 87,
    "innovation_score": 80,
    "feasibility_score": 90,
    "impact_score": 88,
    "why_it_wins": "The single strongest argument for why this beats the competition",
    "pitch_opening": "Exact words to open your pitch with",
    "demo_idea": "What the 2-minute live demo should show",
    "risk": "Biggest risk or challenge",
    "differentiator": "What makes this stand out from 50 other submissions",
    "time_to_build": "realistic estimate: 8h | 24h | 48h"
  }}
]

Return ONLY valid JSON array. No explanation.
"""
    try:
        raw = _call(prompt, max_tokens=8_000, temperature=0.8)
        ideas = _parse_json(raw)
        if isinstance(ideas, list):
            return ideas[:10]
        return []
    except Exception as e:
        print(f"[analyzer] ideas generation error: {e}")
        return []


def build_chat_context(
    hackathon_data: dict,
    hackathon_analysis: dict,
    judges: list,
    ideas: list,
) -> str:
    """Build the full context string injected into every chat message."""
    parts = []

    name = hackathon_data.get("name") or hackathon_analysis.get("name") or "Hackathon"
    parts.append(f"# HACKATHON: {name}")
    parts.append(f"URL: {hackathon_data.get('url', '')}")
    parts.append(
        f"SUMMARY: {hackathon_analysis.get('summary') or hackathon_data.get('description', '')}"
    )

    def _get(key):
        return hackathon_data.get(key) or hackathon_analysis.get(key) or []

    prizes = _get("prizes")
    if prizes:
        parts.append(f"\nPRIZES:\n{json.dumps(prizes, indent=2)}")

    tracks = _get("tracks")
    if tracks:
        parts.append(f"\nTRACKS:\n{json.dumps(tracks, indent=2)}")

    criteria = _get("judging_criteria")
    if criteria:
        parts.append(f"\nJUDGING CRITERIA: {', '.join(str(c) for c in criteria)}")

    themes = _get("themes")
    if themes:
        parts.append(f"THEMES: {', '.join(str(t) for t in themes)}")

    parts.append("\n# JUDGES")
    for j in judges:
        a = j.get("analysis", {})
        parts.append(f"\n## {j.get('name')} — {j.get('title')} @ {j.get('company')}")
        parts.append(f"Background: {a.get('background_type', '')}")
        parts.append(f"Expertise: {', '.join(a.get('expertise', []))}")
        likes = [
            (x.get("item") if isinstance(x, dict) else x) for x in a.get("likes", [])
        ]
        parts.append(f"Likes: {', '.join(likes)}")
        dislikes = [
            (x.get("item") if isinstance(x, dict) else x) for x in a.get("dislikes", [])
        ]
        parts.append(f"Dislikes: {', '.join(dislikes)}")
        parts.append(f"Hot Topics: {', '.join(a.get('hot_topics', []))}")
        parts.append(f"Score Weight: {a.get('score_weight', '')}")
        parts.append(f"Golden Tip: {a.get('tip', '')}")
        if j.get("research", {}).get("linkedin_url"):
            parts.append(f"LinkedIn: {j['research']['linkedin_url']}")

    parts.append("\n# GENERATED IDEAS")
    for idea in ideas[:10]:
        parts.append(
            f"\n## Idea #{idea.get('rank')}: {idea.get('title')} (score: {idea.get('overall_score')})"
        )
        parts.append(idea.get("tagline", ""))
        parts.append(idea.get("description", ""))
        parts.append(f"Tech: {', '.join(idea.get('tech_stack', []))}")
        parts.append(f"Why it wins: {idea.get('why_it_wins', '')}")

    return "\n".join(parts)


def chat_response(job: dict, message: str, history: list) -> str:
    """Stream-friendly chat endpoint with full hackathon context."""
    context = job.get("chat_context", "")
    hackathon_name = job.get("hackathon_name", "this hackathon")

    history_str = ""
    for h in history[-12:]:
        role = h.get("role", "user").upper()
        content = h.get("content", "")
        history_str += f"\n{role}: {content}"

    prompt = f"""You are an elite hackathon strategist. You help teams WIN {hackathon_name}.
You have scraped and analyzed everything about this event: the judges, prizes, tracks, themes.
Be specific, tactical, and reference actual people/details by name.
Be direct and confident — you are the expert, not a generic AI.

═══ HACKATHON INTELLIGENCE ═══
{context}

═══ CONVERSATION ═══
{history_str}

USER: {message}

Respond as the strategist. Be concise but impactful. Reference specific judges/prizes/tracks.
Use formatting (bullet points, bold) when it helps clarity.
"""
    try:
        return _call(prompt, max_tokens=2_000, temperature=0.75)
    except Exception as e:
        return f"Error: {e}"
