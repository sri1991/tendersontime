import pandas as pd
import json
import os

EXCEL_PATH = "/Users/bharani/Desktop/Developer/ten/tendersontime/Sector-Subsector 25 Jan 2026.xlsx"
OUTPUT_PATH = "/Users/bharani/Desktop/Developer/ten/tendersontime/src/enrichment/keywords.json"

def convert_excel_to_json():
    try:
        df = pd.read_excel(EXCEL_PATH)
        keyword_map = {}
        
        # Iterate over columns
        # Based on analysis, valid sectors are in columns that don't start with "Unnamed"
        
        current_sector = None
        
        for col in df.columns:
            if str(col).startswith("Unnamed"):
                continue
            
            sector_name = col.strip()
            # Get all non-null values in this column
            sub_sectors = df[col].dropna().astype(str).str.strip().tolist()
            
            # Filter out empty strings just in case
            sub_sectors = [s for s in sub_sectors if s]
            
            if sub_sectors:
                keyword_map[sector_name] = sub_sectors
                
        # Write to JSON
        os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
        with open(OUTPUT_PATH, 'w', encoding='utf-8') as f:
            json.dump(keyword_map, f, indent=2, ensure_ascii=False)
            
        print(f"Successfully converted Excel to {OUTPUT_PATH}")
        print(f"Found {len(keyword_map)} sectors.")
        return keyword_map

    except Exception as e:
        print(f"Error converting excel: {e}")
        return None

if __name__ == "__main__":
    convert_excel_to_json()
