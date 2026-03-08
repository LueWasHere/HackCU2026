#!/usr/bin/env python3
"""
LinkedIn Matching Benchmark

Tests the LinkedIn worker against ground truth data from benchmarks.csv.
Reads Name, Career/Title, and expected LinkedIn URL from the CSV file.
"""

import asyncio
import csv
import os
import sys
from dataclasses import dataclass
from typing import Optional

# Add the current directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from crawler import HackathonCrawler
from analyzer import HackathonAnalyzer


@dataclass
class JudgeTestCase:
    name: str
    title: str  # Career / Title from CSV
    expected_linkedin: str


def parse_career_title(career_title: str) -> tuple[str, str]:
    """Parse career title into title and company parts."""
    if not career_title or career_title.strip() == "N/A":
        return "", ""
    
    # Common separators that indicate company
    separators = [" @ ", " – ", " - ", " at ", " | ", ", "]
    
    career_title = career_title.strip()
    
    for sep in separators:
        if sep in career_title:
            parts = career_title.split(sep, 1)
            return parts[0].strip(), parts[1].strip()
    
    # No separator found, use as title only
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
            
            # Normalize LinkedIn URL
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


class LinkedInBenchmark:
    def __init__(self, use_gemini_verification: bool = False, csv_path: str = None):
        self.use_gemini = use_gemini_verification
        self.csv_path = csv_path or os.path.join(os.path.dirname(__file__), "benchmarks.csv")
        self.crawler = None
        self.analyzer = None
        self.test_cases = []
        
    async def setup(self):
        """Initialize crawler and analyzer."""
        self.crawler = HackathonCrawler()
        if self.use_gemini:
            self.analyzer = HackathonAnalyzer()
        
        # Load test data from CSV
        self.test_cases = load_benchmark_data(self.csv_path)
            
    async def teardown(self):
        """Clean up resources."""
        if self.crawler:
            await self.crawler.close()
            
    async def test_judge(self, judge: JudgeTestCase) -> dict:
        """Test a single judge and return results."""
        # Parse career title into title and company
        title, company = parse_career_title(judge.title)
        
        result = await self.crawler.worker_linkedin(
            judge.name, 
            company, 
            title,
            analyzer=self.analyzer if self.use_gemini else None
        )
        
        found_url = result.get("linkedin_url", "")
        found_normalized = normalize_url(found_url)
        expected_normalized = normalize_url(judge.expected_linkedin)
        
        # Determine status
        if not judge.expected_linkedin:
            # Judge shouldn't have LinkedIn
            if not found_url:
                status = "CORRECT_NO_PROFILE"
            else:
                status = "FALSE_POSITIVE"
        else:
            # Judge should have LinkedIn
            if not found_url:
                status = "NOT_FOUND"
            elif found_normalized == expected_normalized:
                status = "CORRECT"
            else:
                status = "WRONG"
                
        return {
            "name": judge.name,
            "title": judge.title,
            "parsed_title": title,
            "parsed_company": company,
            "expected": judge.expected_linkedin,
            "found": found_url,
            "status": status,
        }
        
    async def run(self, verbose: bool = True, limit: int = None) -> dict:
        """Run the full benchmark."""
        await self.setup()
        
        try:
            results = []
            
            # Apply limit if specified
            test_cases = self.test_cases[:limit] if limit else self.test_cases
            
            if verbose:
                mode = "WITH Gemini verification" if self.use_gemini else "WITHOUT Gemini verification"
                print("=" * 80)
                print(f"LINKEDIN MATCHING BENCHMARK - {mode}")
                print(f"Data source: {self.csv_path}")
                print("=" * 80)
                print(f"Testing {len(test_cases)} judges from CSV\n")
            
            for i, judge in enumerate(test_cases, 1):
                if verbose:
                    print(f"\n[{i}/{len(test_cases)}] Testing: {judge.name}")
                    print(f"    Career: {judge.title}")
                    print(f"    Expected: {judge.expected_linkedin or '(no profile expected)'}")
                
                result = await self.test_judge(judge)
                results.append(result)
                
                if verbose:
                    status_emoji = {
                        "CORRECT": "✅",
                        "CORRECT_NO_PROFILE": "✅",
                        "WRONG": "❌",
                        "NOT_FOUND": "❌",
                        "FALSE_POSITIVE": "⚠️",
                    }.get(result["status"], "?")
                    print(f"    {status_emoji} {result['status']}: {result['found'] or '(none)'}")
            
            # Calculate statistics
            stats = self._calculate_stats(results)
            
            if verbose:
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
        
        # Calculate accuracy
        accuracy = (correct / total * 100) if total > 0 else 0
        
        # Among judges that SHOULD have LinkedIn, how many did we find correctly?
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
        print("BENCHMARK RESULTS")
        print("=" * 80)
        print(f"Total judges tested:     {stats['total']}")
        print(f"Correct matches:         {stats['correct']} ({stats['accuracy']:.1f}%)")
        print(f"Wrong matches:           {stats['wrong']}")
        print(f"Not found (should exist): {stats['not_found']}")
        print(f"False positives:         {stats['false_positive']}")
        print()
        print(f"Profile finding accuracy: {stats['profile_accuracy']:.1f}%")
        print(f"  (Among judges with known profiles, correctly found {stats['profile_accuracy']:.0f}%)")
        print("=" * 80)


async def main():
    """Main entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(description="LinkedIn Matching Benchmark")
    parser.add_argument(
        "--gemini", "-g",
        action="store_true",
        help="Enable Gemini verification (requires GEMINI_API_KEY)"
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Only print summary statistics"
    )
    parser.add_argument(
        "--csv", "-c",
        type=str,
        default=None,
        help="Path to CSV file (default: benchmarks.csv in same directory)"
    )
    parser.add_argument(
        "--limit", "-l",
        type=int,
        default=None,
        help="Limit number of test cases to run"
    )
    
    args = parser.parse_args()
    
    if args.gemini and not os.environ.get("GEMINI_API_KEY"):
        print("Error: GEMINI_API_KEY environment variable required for --gemini")
        sys.exit(1)
    
    benchmark = LinkedInBenchmark(
        use_gemini_verification=args.gemini,
        csv_path=args.csv
    )
    await benchmark.run(verbose=not args.quiet, limit=args.limit)


if __name__ == "__main__":
    asyncio.run(main())
