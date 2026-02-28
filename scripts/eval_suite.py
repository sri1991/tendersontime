import os
import asyncio
import json
import logging
from typing import List, Dict, Any
from src.search.engine import SmartSearchEngine

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class SearchEvaluator:
    def __init__(self, golden_data_path: str):
        self.engine = SmartSearchEngine()
        self.golden_data = self._load_golden_data(golden_data_path)

    def _load_golden_data(self, path: str) -> List[Dict[str, Any]]:
        if not os.path.exists(path):
            logging.error(f"Golden data file not found: {path}")
            return []
        with open(path, 'r') as f:
            return json.load(f)

    async def evaluate(self, k_values: List[int] = [1, 5, 10]):
        """
        Evaluates the search engine against the golden dataset.
        """
        if not self.golden_data:
            print("No golden data to evaluate.")
            return

        results = []
        
        print(f"\n--- Running Evaluation on {len(self.golden_data)} queries ---\n")
        
        for entry in self.golden_data:
            query = entry.get("query")
            expected_ids = entry.get("expected_ids", [])
            if not query or not expected_ids:
                continue

            print(f"Query: '{query}' | Expected IDs: {expected_ids}")
            
            # Perform search
            search_results = await self.engine.search(query, k=max(k_values))
            retrieved_ids = search_results.get("ids", [[]])[0]
            
            # Calculate metrics
            metrics = {
                "query": query,
                "expected_ids": expected_ids,
                "retrieved_count": len(retrieved_ids),
                "hits": {}
            }
            
            # Hit Rate at K
            for k in k_values:
                top_k = retrieved_ids[:k]
                hits = [eid for eid in expected_ids if eid in top_k]
                metrics["hits"][f"hit@{k}"] = len(hits) / len(expected_ids) if expected_ids else 0
            
            # MRR (Mean Reciprocal Rank) for the first expected ID
            mrr = 0
            first_expected = expected_ids[0]
            if first_expected in retrieved_ids:
                rank = retrieved_ids.index(first_expected) + 1
                mrr = 1 / rank
            metrics["mrr"] = mrr
            
            results.append(metrics)
            print(f"  -> Hit@1: {metrics['hits']['hit@1']:.2f} | Hit@5: {metrics['hits']['hit@5']:.2f} | MRR: {mrr:.2f}")

        # Aggregate metrics
        self._print_aggregate_metrics(results, k_values)

    def _print_aggregate_metrics(self, results: List[Dict[str, Any]], k_values: List[int]):
        print("\n" + "="*40)
        print("AGGREGATE PERFORMANCE METRICS")
        print("="*40)
        
        num_queries = len(results)
        if num_queries == 0:
            return

        for k in k_values:
            avg_hit = sum(r["hits"][f"hit@{k}"] for r in results) / num_queries
            print(f"Average Hit@{k}: {avg_hit:.4f}")
            
        avg_mrr = sum(r["mrr"] for r in results) / num_queries
        print(f"Average MRR:    {avg_mrr:.4f}")
        print("="*40 + "\n")

if __name__ == "__main__":
    golden_path = "tests/golden_data.json"
    evaluator = SearchEvaluator(golden_path)
    asyncio.run(evaluator.evaluate())
