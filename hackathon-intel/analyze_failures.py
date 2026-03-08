#!/usr/bin/env python3
"""
LinkedIn Failure Analysis Report
Analyzes why each judge's LinkedIn lookup failed
"""

import asyncio
import sys
sys.path.insert(0, '.')

from crawler import HackathonCrawler, _score_linkedin_candidate, _is_linkedin_candidate_valid, _name_tokens


def normalize_url(url):
    if not url:
        return ""
    return url.lower().replace('https://', '').replace('http://', '').replace('www.', '').rstrip('/')


TEST_CASES = [
    ("Sumeet Jeswani", "Cloud Security", "Google", "https://linkedin.com/in/sjeswani/"),
    ("Dawson Botsford", "Founder of stopmailing.us and Co-Founder of HackCU", "", "https://linkedin.com/in/dawsonbotsford/"),
    ("Lilly Jones", "PHD, Associate Scientist III", "CIRES Earth and ESIIL Labs in the SEEC", "https://linkedin.com/in/lillysanovia/"),
    ("Andy (Andrev) Austin", "Founder", "Adsora", "https://linkedin.com/in/andyaustinadsora/"),
    ("Jay Malave", "Founding Engineer", "Normal Finance", "https://linkedin.com/in/jay-malave/"),
    ("Ken Fricklas", "CTO", "Nodiac", "https://linkedin.com/in/kenfricklas/"),
    ("Anna Rahn", "Application Developer", "RSM", "https://linkedin.com/in/annarahn/"),
    ("Korey Mercier", "Leadership Alignment & AI Governance Architect", "", "https://linkedin.com/in/korey/"),
    ("Katherine White", "CTO", "Maia Women's Health", "https://linkedin.com/in/k80blanco/"),
]


async def analyze_judge(crawler, name, title, company, expected):
    """Analyze a single judge and return failure reason."""
    
    result = await crawler.worker_linkedin(name, company, title, analyzer=None)
    found_url = result.get("linkedin_url", "")
    
    expected_norm = normalize_url(expected)
    found_norm = normalize_url(found_url)
    
    if found_norm == expected_norm:
        return {"name": name, "status": "CORRECT", "reason": None}
    
    # Analyze what happened
    if not found_url:
        return {"name": name, "status": "NOT_FOUND", "reason": "No valid candidates found after all search attempts"}
    
    # Check if expected was in search results but rejected
    name_clean = name.strip()
    company_clean = company.strip() if company else ""
    title_clean = title.strip() if title else ""
    
    # Try to find candidates
    queries = [
        f'"{name_clean}" {title_clean} {company_clean} site:linkedin.com/in',
        f'"{name_clean}" {company_clean} site:linkedin.com/in',
        f'"{name_clean}" site:linkedin.com/in',
    ]
    
    all_candidates = []
    seen_urls = set()
    expected_found = False
    expected_score = 0
    
    for query in queries:
        urls = await crawler._search_duckduckgo(query, max_results=6)
        linkedin_urls = [u for u in urls if 'linkedin.com/in/' in u.lower()]
        
        for url in linkedin_urls[:4]:
            if normalize_url(url) in seen_urls:
                continue
            seen_urls.add(normalize_url(url))
            
            data = await crawler._crawl_url(url, timeout=15, content_limit=5000)
            if not isinstance(data, dict):
                continue
            
            content = data.get("content", "")
            if len(content) < 100:
                continue
            
            score = _score_linkedin_candidate(url, content, name_clean, company_clean, title_clean)
            is_valid = _is_linkedin_candidate_valid(url, content, name_clean, company_clean, title_clean, score)
            
            is_expected = normalize_url(url) == expected_norm
            if is_expected:
                expected_found = True
                expected_score = score
            
            all_candidates.append({
                'url': url,
                'score': score,
                'valid': is_valid,
                'is_expected': is_expected,
            })
    
    if not expected_found:
        return {
            "name": name, 
            "status": "EXPECTED_NOT_IN_SEARCH", 
            "reason": f"Expected profile '{expected}' never appeared in DuckDuckGo search results. The profile may use a custom username that doesn't match the search terms.",
            "found_instead": found_url,
            "top_candidates": sorted(all_candidates, key=lambda x: x['score'], reverse=True)[:3]
        }
    
    # Expected was found but scored lower
    all_candidates.sort(key=lambda x: x['score'], reverse=True)
    top_score = all_candidates[0]['score'] if all_candidates else 0
    
    if expected_score < top_score:
        return {
            "name": name,
            "status": "LOWER_SCORE",
            "reason": f"Expected profile found but scored {expected_score} vs top candidate's {top_score}. The expected profile had less matching content.",
            "found_instead": found_url,
            "expected_score": expected_score,
            "top_score": top_score,
        }
    
    return {
        "name": name,
        "status": "UNKNOWN",
        "reason": "Unknown failure reason",
        "found_instead": found_url,
    }


async def main():
    crawler = HackathonCrawler()
    
    print("=" * 100)
    print("LINKEDIN MATCHING FAILURE ANALYSIS")
    print("=" * 100)
    print()
    
    results = []
    for name, title, company, expected in TEST_CASES:
        result = await analyze_judge(crawler, name, title, company, expected)
        results.append(result)
    
    await crawler.close()
    
    # Group by failure type
    not_found = [r for r in results if r['status'] == 'NOT_FOUND']
    expected_not_in_search = [r for r in results if r['status'] == 'EXPECTED_NOT_IN_SEARCH']
    lower_score = [r for r in results if r['status'] == 'LOWER_SCORE']
    correct = [r for r in results if r['status'] == 'CORRECT']
    
    # Print summary
    print(f"SUMMARY:")
    print(f"  ✅ Correct: {len(correct)}/{len(results)}")
    print(f"  ❌ Expected profile not in search results: {len(expected_not_in_search)}")
    print(f"  ❌ Expected profile had lower score: {len(lower_score)}")
    print(f"  ❌ No candidates found at all: {len(not_found)}")
    print()
    
    # Print detailed analysis
    if expected_not_in_search:
        print("=" * 100)
        print("FAILURE TYPE 1: Expected profile never appeared in search results")
        print("=" * 100)
        print("This means DuckDuckGo doesn't index the profile for this name search.")
        print("Common causes: Custom usernames (e.g., 'lillysanovia' instead of 'lilly-jones')")
        print()
        
        for r in expected_not_in_search:
            print(f"\n👤 {r['name']}")
            print(f"   Expected: {r.get('found_instead', 'N/A')}")
            print(f"   Issue: {r['reason']}")
            if r.get('top_candidates'):
                print(f"   Top candidates found instead:")
                for c in r['top_candidates']:
                    marker = " <-- This was selected"
                    print(f"     - Score {c['score']:3d}: {c['url'][:60]}{marker if c == r['top_candidates'][0] else ''}")
    
    if lower_score:
        print("\n" + "=" * 100)
        print("FAILURE TYPE 2: Expected profile found but had lower score")
        print("=" * 100)
        print("The correct profile was found but another profile scored higher.")
        print()
        
        for r in lower_score:
            print(f"\n👤 {r['name']}")
            print(f"   Expected score: {r['expected_score']}")
            print(f"   Winner score: {r['top_score']}")
            print(f"   Found instead: {r['found_instead']}")
    
    if not_found:
        print("\n" + "=" * 100)
        print("FAILURE TYPE 3: No candidates found at all")
        print("=" * 100)
        for r in not_found:
            print(f"\n👤 {r['name']}: {r['reason']}")
    
    print("\n" + "=" * 100)
    print("ROOT CAUSE SUMMARY")
    print("=" * 100)
    
    print("""
The main reason for LinkedIn matching failures is that many professionals use 
custom LinkedIn usernames that don't contain their real names:

  • Lilly Jones uses: linkedin.com/in/lillysanovia/
  • Andy Austin uses: linkedin.com/in/andyaustinadsora/
  • Katherine White uses: linkedin.com/in/k80blanco/
  • Korey Mercier uses: linkedin.com/in/korey/ (just first name)

When we search DuckDuckGo for "Lilly Jones", we find dozens of profiles with 
usernames like "lilly-jones-12345" but NOT the actual profile with username 
"lillysanovia" because it doesn't match the search terms.

SOLUTION:
The Gemini verification step (when enabled with --gemini) reads the content of 
each candidate profile and can identify the correct person even when the 
username doesn't match. This is the only reliable way to handle custom usernames.
""")


if __name__ == "__main__":
    asyncio.run(main())
