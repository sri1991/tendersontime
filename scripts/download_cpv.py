"""
Download the official EU CPV taxonomy (all ~9,000 codes) and save to data/cpv_taxonomy.json.

Source: EU Open Data Portal — CPV codes 2008 revision
Run once before starting the pipeline:
    python scripts/download_cpv.py
"""
import json
import sys
import urllib.request
from pathlib import Path

# EU CPV 2008 codes — publicly available CSV
CPV_CSV_URL = "https://simap.ted.europa.eu/documents/10184/36234/cpv_2008_ver_2013.csv"

OUTPUT_PATH = Path(__file__).parent.parent / "data" / "cpv_taxonomy.json"


def download_cpv():
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    print(f"Downloading CPV taxonomy from:\n  {CPV_CSV_URL}")
    print("(This is the official EU CPV 2008 revision with ~9,400 codes)\n")

    try:
        req = urllib.request.Request(CPV_CSV_URL, headers={"User-Agent": "TenderScout/1.0"})
        with urllib.request.urlopen(req, timeout=30) as response:
            content = response.read().decode("utf-8-sig")  # handles BOM
    except Exception as e:
        print(f"Download failed: {e}")
        print("\nFalling back to embedded minimal CPV set...")
        _write_fallback()
        return

    lines = content.strip().splitlines()
    print(f"Downloaded {len(lines)} lines")

    codes = []
    skipped = 0
    for line in lines[1:]:  # skip header
        parts = line.split(";")
        if len(parts) < 2:
            skipped += 1
            continue
        code_raw = parts[0].strip().strip('"')
        desc_raw = parts[1].strip().strip('"') if len(parts) > 1 else ""

        # CPV codes look like "30213100-6" — strip the check digit suffix
        code = code_raw.replace("-", "").replace(" ", "")[:8]
        if len(code) == 8 and code.isdigit() and desc_raw:
            codes.append({"code": code, "description": desc_raw})

    print(f"Parsed {len(codes)} valid CPV codes ({skipped} skipped)")

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(codes, f, ensure_ascii=False, indent=2)

    print(f"\nSaved to: {OUTPUT_PATH}")
    print("CPV taxonomy ready for use.")


def _write_fallback():
    """Write a larger fallback set if download fails."""
    fallback = [
        {"code": "03000000", "description": "Agricultural, farming, fishing, forestry and related products"},
        {"code": "09000000", "description": "Petroleum products, fuel, electricity and other sources of energy"},
        {"code": "14000000", "description": "Mining, basic metals and related products"},
        {"code": "15000000", "description": "Food, beverages, tobacco and related products"},
        {"code": "16000000", "description": "Agricultural machinery"},
        {"code": "18000000", "description": "Clothing, footwear, luggage articles and accessories"},
        {"code": "19000000", "description": "Leather and textile fabrics, plastic and rubber materials"},
        {"code": "22000000", "description": "Printed matter and related products"},
        {"code": "24000000", "description": "Chemical products"},
        {"code": "30000000", "description": "Office and computing machinery, equipment and supplies"},
        {"code": "30200000", "description": "Computer equipment and supplies"},
        {"code": "30213000", "description": "Personal computers"},
        {"code": "30213100", "description": "Portable computers"},
        {"code": "31000000", "description": "Electrical machinery, apparatus, equipment and consumables"},
        {"code": "32000000", "description": "Radio, television, communication, telecommunication and related equipment"},
        {"code": "33000000", "description": "Medical equipments, pharmaceuticals and personal care products"},
        {"code": "34000000", "description": "Transport equipment and auxiliary products to transportation"},
        {"code": "34100000", "description": "Motor vehicles"},
        {"code": "35000000", "description": "Security, fire-fighting, police and defence equipment"},
        {"code": "37000000", "description": "Musical instruments, sport goods, games, toys, handicraft, art materials and accessories"},
        {"code": "38000000", "description": "Laboratory, optical and precision equipments"},
        {"code": "39000000", "description": "Furniture, household equipment and cleaning products"},
        {"code": "41000000", "description": "Collected and purified water"},
        {"code": "42000000", "description": "Industrial machinery"},
        {"code": "43000000", "description": "Machinery for mining, quarrying, construction equipment"},
        {"code": "44000000", "description": "Construction structures and materials; auxiliary products"},
        {"code": "45000000", "description": "Construction work"},
        {"code": "45100000", "description": "Site preparation work"},
        {"code": "45200000", "description": "Works for complete or part construction and civil engineering work"},
        {"code": "45300000", "description": "Building installation work"},
        {"code": "45400000", "description": "Building completion work"},
        {"code": "48000000", "description": "Software package and information systems"},
        {"code": "50000000", "description": "Repair and maintenance services"},
        {"code": "51000000", "description": "Installation services"},
        {"code": "55000000", "description": "Hotel, restaurant and retail trade services"},
        {"code": "60000000", "description": "Transport services"},
        {"code": "63000000", "description": "Supporting and auxiliary transport services; travel agencies services"},
        {"code": "64000000", "description": "Postal and telecommunications services"},
        {"code": "65000000", "description": "Public utilities"},
        {"code": "66000000", "description": "Financial and insurance services"},
        {"code": "70000000", "description": "Real estate services"},
        {"code": "71000000", "description": "Architectural, construction, engineering and inspection services"},
        {"code": "72000000", "description": "IT services: consulting, software development, Internet and support"},
        {"code": "73000000", "description": "Research and development services and related consultancy services"},
        {"code": "75000000", "description": "Administration, defence and social security services"},
        {"code": "76000000", "description": "Services related to the oil and gas industry"},
        {"code": "77000000", "description": "Agricultural, forestry, horticultural, aquacultural and apicultural services"},
        {"code": "79000000", "description": "Business services: law, marketing, consulting, recruitment, printing and security"},
        {"code": "80000000", "description": "Education and training services"},
        {"code": "85000000", "description": "Health and social work services"},
        {"code": "90000000", "description": "Sewage, refuse, cleaning and environmental services"},
        {"code": "92000000", "description": "Recreational, cultural and sporting services"},
        {"code": "98000000", "description": "Other community, social and personal services"},
    ]

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(fallback, f, ensure_ascii=False, indent=2)
    print(f"Fallback CPV set ({len(fallback)} codes) saved to: {OUTPUT_PATH}")


if __name__ == "__main__":
    download_cpv()
