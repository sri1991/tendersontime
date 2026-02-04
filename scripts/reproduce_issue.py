
import requests
import json
import sys

def test_search():
    url = "http://127.0.0.1:8000/api/search"
    payload = {
        "query": "animal ear tag",
        "include_corrigendum": False,
        "limit": 50
    }
    
    try:
        response = requests.post(url, json=payload)
        response.raise_for_status()
        data = response.json()
        
        results = data.get("results", [])
        corrigendums = [r for r in results if r.get("is_corrigendum") is True]
        
        print(f"Total results: {len(results)}")
        
        # Check for textual match in title even if metadata is False
        suspicious = [r for r in results if "corrigendum" in r.get("title", "").lower()]
        
        if suspicious:
             print(f"\n⚠️ FOUND {len(suspicious)} items with 'Corrigendum' in title:")
             for s in suspicious:
                 print(f" - {s.get('title')} (is_corrigendum: {s.get('is_corrigendum')})")
        
        corrigendums = [r for r in results if r.get("is_corrigendum") is True]
        
        if corrigendums:
            print("\n❌ ISSUE REPRODUCED: Found metadata corrigendums.")
        else:
            print("\n✅ Metadata check passed (No is_corrigendum=True found).")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_search()
