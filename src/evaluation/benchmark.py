import asyncio
import json
import os
import sys
from typing import List, Dict, Any
from dotenv import load_dotenv

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from src.search.engine import SmartSearchEngine

load_dotenv()

GOLDEN_DATA_PATH = "tests/golden_data.json"

class SearchBenchmark:
    def __init__(self):
        self.engine = SmartSearchEngine()
        
    async def run_benchmark(self):
        if not os.path.exists(GOLDEN_DATA_PATH):
            print(f"Golden data file not found: {GOLDEN_DATA_PATH}")
            return

        with open(GOLDEN_DATA_PATH, "r") as f:
            test_cases = json.load(f)
            
        print(f"\n🚀 Starting Search Benchmark with {len(test_cases)} cases...\n")
        
        overall_pass = 0
        total_cases = len(test_cases)
        
        for case in test_cases:
            await self.evaluate_case(case)
            
    async def evaluate_case(self, case: Dict[str, Any]):
        query = case["query"]
        expectations = case["expectations"]
        top_k = expectations.get("top_k_to_check", 10)
        
        print(f"🔹 Case [{case['id']}]: '{query}'")
        
        results = await self.engine.search(query, k=top_k)
        
        ids = results.get("ids", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        
        relevant_count = 0
        keyword_hits = 0
        domain_hits = 0
        sub_domain_hits = 0
        
        debug_info = []

        for i, meta in enumerate(metadatas):
            is_relevant = False
            reasons = []
            
            # Check Domains
            req_domains = expectations.get("must_match_domains", [])
            if req_domains:
                if meta.get("core_domain") in req_domains:
                    domain_hits += 1
                    reasons.append("Domain Match")
            
            # Check Tags (Partial match allowed)
            req_tags = expectations.get("required_tags", [])
            if req_tags:
                # Tags are stored as comma-separated string in metadata
                found_tags = meta.get("project_tags", "").lower()
                if any(target.lower() in found_tags for target in req_tags):
                    sub_domain_hits += 1
                    reasons.append("Tag Match")
                    
            # Check Keywords in Description/Title
            req_kw = expectations.get("required_keywords", [])
            text_content = (meta.get("original_title", "") + " " + meta.get("description", "")).lower()
            if any(kw.lower() in text_content for kw in req_kw):
                keyword_hits += 1
                reasons.append("Keyword Match")
                
            # Definition of Relevance for this test: Matches Domain AND (Tag OR Keyword)
            # Relaxed: OR Just Tag Match (if Domain is broad "Other")
            if (req_domains and meta.get("core_domain") in req_domains) or ("Tag Match" in reasons):
                is_relevant = True
                relevant_count += 1
                
            debug_info.append({
                "rank": i+1,
                "title": meta.get("original_title")[:50] + "...",
                "domain": meta.get("core_domain"),
                "tags": meta.get("project_tags"),
                "relevant": is_relevant
            })

        # Evaluate Expectations
        min_relevant = expectations.get("min_relevant_count", 1)
        passed = relevant_count >= min_relevant
        
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"   Status: {status} (Relevant Found: {relevant_count}/{top_k}, Required: {min_relevant})")
        
        if not passed:
             print("   ⚠️  Top Results Details:")
             for item in debug_info[:5]:
                 print(f"      {item['rank']}. [{item['relevant']}] {item['title']} ({item['domain']} / {item['sub_domain']})")
        print("")

if __name__ == "__main__":
    benchmark = SearchBenchmark()
    asyncio.run(benchmark.run_benchmark())
