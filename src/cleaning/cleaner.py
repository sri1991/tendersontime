import re
import hashlib
from datetime import datetime
from typing import Optional

class CurrencyNormalizer:
    @staticmethod
    def normalize(value_str: str) -> Optional[int]:
        """
        Parses currency strings like "50 Lakhs", "1.5 Cr", "5,00,000" into integers.
        Returns None if parsing fails.
        """
        if not value_str:
            return None
            
        value_str = value_str.lower().strip()
        # Remove commas and currency symbols
        clean_str = re.sub(r'[₹$,]', '', value_str)
        
        multiplier = 1
        if 'lakh' in clean_str or 'lac' in clean_str:
            multiplier = 100000
            clean_str = re.sub(r'\s*(lakhs|lacs|lakh|lac)', '', clean_str)
        elif 'cr' in clean_str or 'crore' in clean_str:
            multiplier = 10000000
            clean_str = re.sub(r'\s*(crores|crore|cr)', '', clean_str)
        elif 'k' in clean_str:
            multiplier = 1000
            clean_str = re.sub(r'\s*k', '', clean_str)
            
        try:
            return int(float(clean_str.strip()) * multiplier)
        except ValueError:
            return None

class DateStandardizer:
    @staticmethod
    def to_iso(date_str: str) -> Optional[str]:
        """
        Converts non-standard date strings to ISO format (YYYY-MM-DD).
        Supports: DD-MM-YYYY, DD/MM/YYYY, YYYY-MM-DD
        """
        if not date_str:
            return None
            
        date_str = date_str.strip()
        formats = [
            "%d-%m-%Y",
            "%d/%m/%Y",
            "%Y-%m-%d",
            "%d-%b-%Y", # 01-Jan-2023
            "%d %b %Y"  # 01 Jan 2023
        ]
        
        for fmt in formats:
            try:
                dt = datetime.strptime(date_str, fmt)
                return dt.date().isoformat()
            except ValueError:
                continue
                
        return None

class Deduplicator:
    @staticmethod
    def generate_hash(title: str, location: str) -> str:
        """
        Generates a SHA256 hash based on Title and Location to identify duplicates.
        Normalizes text (lowercase, stripped) before hashing to ensure consistency.
        """
        normalized_title = title.lower().strip() if title else ""
        normalized_location = location.lower().strip() if location else ""
        
        # Combine with a separator to avoid collisions like (ab, c) vs (a, bc)
        content = f"{normalized_title}|{normalized_location}"
        return hashlib.sha256(content.encode()).hexdigest()
