# LinkedIn Matching Failure Analysis

## Summary

Tested 9 judges from HackCU 12 with known ground truth LinkedIn URLs:

| Result | Count | Percentage |
|--------|-------|------------|
| ✅ Correct | 2 | 22% |
| ❌ Wrong | 7 | 78% |

## Failure Breakdown

### Type 1: Expected Profile Never in Search Results (5 cases)

The correct LinkedIn profile was NEVER returned by DuckDuckGo search. This is the most common failure mode.

**Root Cause:** Many professionals use custom LinkedIn usernames that don't contain their real names.

| Judge | Expected URL | Actual Username | Why It Failed |
|-------|-------------|-----------------|---------------|
| **Lilly Jones** | `lillysanovia` | `lillysanovia` | Username completely different from name |
| **Andy Austin** | `andyaustinadsora` | `andyaustinadsora` | Company name in username |
| **Katherine White** | `k80blanco` | `k80blanco` | Random characters |
| **Jay Malave** | `jay-malave` | `jay-malave` | Clean URL exists but DDG finds numbered versions first |
| **Dawson Botsford** | `dawsonbotsford` | `dawsonbotsford` | DDG finds `dawson-botsford` (hyphenated) instead |

**What happens:**
1. Search DuckDuckGo for "Lilly Jones"
2. DDG returns: `lilly-jones-123`, `lilly-jones-456`, etc.
3. DDG does NOT return: `lillysanovia` (the real one)
4. We pick the highest-scoring wrong profile

**Example - Lilly Jones:**
```
Expected: linkedin.com/in/lillysanovia/
Selected: linkedin.com/in/lilly-jones/ (different person!)

Top candidates found:
  Score  90: lilly-jones (SELECTED - wrong person)
  Score  90: lilly-jones-291082159
  Score  60: lilly-jones999
  
Expected profile NEVER appeared in search results!
```

---

### Type 2: Expected Profile Found But Scored Lower (2 cases)

The correct profile WAS found in search results, but another profile scored higher.

| Judge | Expected Score | Winner Score | Why It Failed |
|-------|---------------|--------------|---------------|
| **Sumeet Jeswani** | 63 | 115 | Wrong person had more content matching |
| **Anna Rahn** | 50 | 90 | Wrong person's profile had more content |

**What happens:**
1. Multiple profiles with same name found
2. Both pass validation (have name evidence)
3. Wrong person scores higher (more content, better company match, etc.)

**Example - Sumeet Jeswani:**
```
Expected: linkedin.com/in/sjeswani/ (score: 63)
Selected: linkedin.com/in/sumeet-jeswani-17222425/ (score: 115)

The wrong "Sumeet Jeswani" scored higher because:
- Both have name in slug
- Both have name in content  
- The wrong one had more content matching
```

---

## The Core Problem

**LinkedIn allows custom usernames that don't match real names.**

When we search for a person's name, we find profiles with usernames like:
- `firstname-lastname-12345` ✅ (matches search)
- `firstname-lastname-random` ✅ (matches search)

But we MISS profiles with usernames like:
- `lillysanovia` ❌ (doesn't match "Lilly Jones")
- `k80blanco` ❌ (doesn't match "Katherine White")
- `andyaustinadsora` ❌ (doesn't match "Andy Austin")

---

## Solutions

### Solution 1: Gemini Verification (Implemented)

Use Gemini AI to read the content of each candidate profile and verify if it matches the person we're looking for.

**How it works:**
1. Find all candidate profiles (even wrong ones)
2. Use Gemini to read profile content for each
3. Ask Gemini: "Does this profile belong to [Name] who works at [Company] as [Title]?"
4. Only return the profile Gemini confirms is correct

**Pros:**
- Can identify correct person even with custom username
- Works for any username format

**Cons:**
- Requires Gemini API calls (cost)
- Slower (extra API call per candidate)

### Solution 2: Crawl Official Event Website First

If the hackathon has an official website with judge LinkedIn links, crawl that FIRST before searching.

**How it works:**
1. Check if hackathon has official website
2. Crawl for LinkedIn links
3. Use those as ground truth

**Pros:**
- 100% accurate if official site has links
- Fast (no searching needed)

**Cons:**
- Not all hackathons have official sites
- Official sites may not have LinkedIn links

### Solution 3: Lower Confidence for Unverified Profiles

When we can't find a high-confidence match, return empty instead of guessing.

**How it works:**
1. Set confidence threshold (e.g., score > 80)
2. If no candidate meets threshold, return "no profile found"
3. Better to return nothing than wrong profile

**Pros:**
- Avoids false positives
- Users know we don't have the data

**Cons:**
- Less data overall
- May miss correct profiles that just scored low

---

## Recommendations

1. **Enable Gemini verification** for production use - it's the only reliable way to handle custom usernames

2. **Add official website crawler** - Check if hackathon has an official website and extract LinkedIn links from there first

3. **Add confidence scoring** - Return empty if no candidate scores above threshold (avoid false positives)

4. **Show "unverified" warning** - If we return a LinkedIn URL without Gemini verification, mark it as "unverified"
