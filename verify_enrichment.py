import asyncio
import os
import json
from dotenv import load_dotenv
from src.enrichment.processor import TenderEnricher

# Load env vars
load_dotenv()

async def main():
    print("Initializing TenderEnricher...")
    enricher = TenderEnricher()
    
    # Test case: Agricultural Biotechnology
    title = "Supply of Genome Sequencing Equipment for Agricultural Biotechnology Lab"
    description = "Procurement of high-throughput sequencers for crop improvement research."
    
    print(f"\n--- Testing Tender ---\nTitle: {title}\nDescription: {description}\n")
    
    result = await enricher.enrich_tender(title, description)
    
    print("\n--- Enrichment Result ---")
    print(json.dumps(result, indent=2))
    
    # Verification checks
    core_domain = result.get("core_domain")
    sub_domain = result.get("sub_domain")
    
    print("\n--- Verification ---")
    if core_domain == "Agriculture, Farming and Forestry":
        print(f"✅ Core Domain match: {core_domain}")
    else:
        print(f"❌ Core Domain mismatch: {core_domain}")
        
    if "Agricultural Biotechnology" in sub_domain or sub_domain == "Agricultural Biotechnology":
         print(f"✅ Sub Domain match: {sub_domain}")
    else:
         print(f"⚠️ Sub Domain might be off, checked for 'Agricultural Biotechnology', got: {sub_domain}")

if __name__ == "__main__":
    asyncio.run(main())
