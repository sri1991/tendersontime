import unittest
from src.cleaning.cleaner import CurrencyNormalizer, DateStandardizer, Deduplicator

class TestCleaning(unittest.TestCase):
    
    def test_currency_normalization(self):
        cases = [
            ("50 Lakhs", 5000000),
            ("1.5 Cr", 15000000),
            ("5,00,000", 500000),
            ("10k", 10000),
            ("Invalid", None),
            (None, None)
        ]
        for input_str, expected in cases:
            with self.subTest(input=input_str):
                self.assertEqual(CurrencyNormalizer.normalize(input_str), expected)

    def test_date_standardization(self):
        cases = [
            ("31-12-2023", "2023-12-31"),
            ("01/01/2024", "2024-01-01"),
            ("2024-02-28", "2024-02-28"),
            ("15-Jan-2025", "2025-01-15"),
            ("Invalid", None)
        ]
        for input_str, expected in cases:
            with self.subTest(input=input_str):
                self.assertEqual(DateStandardizer.to_iso(input_str), expected)

    def test_deduplication(self):
        t1, l1 = "Road Construction", "Delhi"
        t2, l2 = "road construction ", " Delhi"
        
        h1 = Deduplicator.generate_hash(t1, l1)
        h2 = Deduplicator.generate_hash(t2, l2)
        
        self.assertEqual(h1, h2)
        
        t3 = "Other Work"
        h3 = Deduplicator.generate_hash(t3, l1)
        self.assertNotEqual(h1, h3)

if __name__ == '__main__':
    unittest.main()
