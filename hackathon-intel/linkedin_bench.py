#!/usr/bin/env python3
"""
LinkedIn Matching Benchmark - Fast Version

Tests the LinkedIn worker against ground truth data from benchmarks.csv.
Simply run: python3 linkedin_bench.py
"""

import asyncio
import csv
import os
import sys
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor

# Add the current directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from crawler import HackathonCrawler
from analyzer import HackathonAnalyzer


@dataclass
class JudgeTestCase:
    name: str
    title: str
    expected_linkedin: str


def parse_career_title(career_title: str) -> tuple[str, str]:
    """Parse career title into title and company parts."""
    if not career_title or career_title.strip() == "N/A":
        return "", ""
    
    separators = [" @ ", " – ", " at ", " - ", " | ", ", "]
    career_title = career_title.strip()
    
    for sep in separators:
        if sep in career_title:
            parts = career_title.split(sep, 1)
            title = parts[0].strip()
            company = parts[1].strip()
            
            # Clean up company
            company_delimiters = [" · ", ";", " | ", ", ", " and ", " – ", " - "]
            for delim in company_delimiters:
                if delim in company:
                    company = company.split(delim, 1)[0].strip()
            
            return title, company
    
    return career_title, ""


def load_benchmark_data(csv_path: str) -> list[JudgeTestCase]:
    """Load test cases from benchmarks.csv."""
    test_cases = []
    
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get("Name", "").strip()
            career_title = row.get("Career / Title", "").strip()
            linkedin_url = row.get("LinkedIn URL", "").strip()
            
            if not name:
                continue
            
            if linkedin_url.upper() == "N/A" or not linkedin_url:
                linkedin_url = ""
            
            test_cases.append(JudgeTestCase(
                name=name,
                title=career_title,
                expected_linkedin=linkedin_url
            ))
    
    return test_cases


def normalize_url(url: str) -> str:
    """Normalize URL for comparison."""
    if not url:
        return ""
    url = url.lower().replace('https://', '').replace('http://', '').replace('www.', '')
    return url.rstrip('/')


async def test_single_judge(crawler: HackathonCrawler, analyzer: HackathonAnalyzer, 
                            judge: JudgeTestCase, index: int, total: int) -> dict:
    """Test a single judge - designed for parallel execution."""
    title, company = parse_career_title(judge.title)
    
    result = await crawler.worker_linkedin(
        judge.name, 
        company, 
        title,
        analyzer=analyzer
    )
    
    found_url = result.get("linkedin_url", "")
    found_normalized = normalize_url(found_url)
    expected_normalized = normalize_url(judge.expected_linkedin)
    
    # Determine status
    if not judge.expected_linkedin:
        status = "CORRECT_NO_PROFILE" if not found_url else "FALSE_POSITIVE"
    else:
        if not found_url:
            status = "NOT_FOUND"
        elif found_normalized == expected_normalized:
            status = "CORRECT"
        else:
            status = "WRONG"
    
    status_emoji = {
        "CORRECT": "✅",
        "CORRECT_NO_PROFILE": "✅",
        "WRONG": "❌",
        "NOT_FOUND": "❌",
        "FALSE_POSITIVE": "⚠️",
    }.get(status, "?")
    
    print(f"[{index}/{total}] {status_emoji} {judge.name}: {status} - {found_url or '(none)'}", flush=True)
    
    return {
        "name": judge.name,
        "expected": judge.expected_linkedin,
        "found": found_url,
        "status": status,
    }


class LinkedInBenchmark:
    def __init__(self, use_gemini_verification: bool = False, csv_path: str = None):
        self.use_gemini = use_gemini_verification
        self.csv_path = csv_path or os.path.join(os.path.dirname(__file__), "benchmarks.csv")
        self.crawler = None
        self.analyzer = None
        
    async def setup(self):
        """Initialize crawler and analyzer."""
        self.crawler = HackathonCrawler()
        self.analyzer = HackathonAnalyzer() if self.use_gemini else None
            
    async def teardown(self):
        """Clean up resources."""
        if self.crawler:
            await self.crawler.close()
            
    async def run(self) -> dict:
        """Run the full benchmark with parallel processing."""
        await self.setup()
        
        try:
            # Load test cases
            test_cases = load_benchmark_data(self.csv_path)
            
            mode = "WITH Gemini" if self.use_gemini else "WITHOUT Gemini"
            print("=" * 80)
            print(f"LINKEDIN BENCHMARK - {mode} - {len(test_cases)} judges")
            print("=" * 80)
            print("Testing (results appear as completed):\n")
            
            # Run all tests concurrently with semaphore to limit concurrency
            semaphore = asyncio.Semaphore(3)  # Max 3 concurrent to avoid rate limits
            
            async def run_with_semaphore(judge, index, total):
                async with semaphore:
                    return await test_single_judge(
                        self.crawler, self.analyzer, judge, index, total
                    )
            
            # Create all tasks
            tasks = [
                run_with_semaphore(judge, i+1, len(test_cases))
                for i, judge in enumerate(test_cases)
            ]
            
            # Run all concurrently
            results = await asyncio.gather(*tasks)
            
            # Calculate statistics
            stats = self._calculate_stats(results)
            self._print_stats(stats)
                
            return {"results": results, "stats": stats}
            
        finally:
            await self.teardown()
            
    def _calculate_stats(self, results: list) -> dict:
        """Calculate benchmark statistics."""
        total = len(results)
        correct = sum(1 for r in results if r["status"] in ("CORRECT", "CORRECT_NO_PROFILE"))
        wrong = sum(1 for r in results if r["status"] == "WRONG")
        not_found = sum(1 for r in results if r["status"] == "NOT_FOUND")
        false_positive = sum(1 for r in results if r["status"] == "FALSE_POSITIVE")
        
        accuracy = (correct / total * 100) if total > 0 else 0
        
        should_have_profile = [r for r in results if r["expected"]]
        found_correctly = sum(1 for r in should_have_profile if r["status"] == "CORRECT")
        profile_accuracy = (found_correctly / len(should_have_profile) * 100) if should_have_profile else 0
        
        return {
            "total": total,
            "correct": correct,
            "wrong": wrong,
            "not_found": not_found,
            "false_positive": false_positive,
            "accuracy": accuracy,
            "profile_accuracy": profile_accuracy,
        }
        
    def _print_stats(self, stats: dict):
        """Print benchmark statistics."""
        print("\n" + "=" * 80)
        print("RESULTS")
        print("=" * 80)
        print(f"Total: {stats['total']} | Correct: {stats['correct']} ({stats['accuracy']:.1f}%) | "
              f"Wrong: {stats['wrong']} | Not Found: {stats['not_found']} | False Pos: {stats['false_positive']}")
        print(f"Profile Accuracy: {stats['profile_accuracy']:.1f}%")
        print("=" * 80)


async def main():
    """Main entry point - runs benchmark fast."""
    use_gemini = bool(os.environ.get("GEMINI_API_KEY"))
    
    if use_gemini:
        print("Note: Using Gemini verification (fast parallel mode)\n")
    else:
        print("Note: No Gemini - running basic mode\n")
    
    benchmark = LinkedInBenchmark(use_gemini_verification=use_gemini)
    await benchmark.run()


if __name__ == "__main__":
    asyncio.run(main())
