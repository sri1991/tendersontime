import unittest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from src.enrichment.processor import TenderEnricher

class TestEnrichment(unittest.TestCase):
    
    @patch("src.enrichment.processor.vertexai.init")
    @patch("src.enrichment.processor.GenerativeModel")
    def test_enrichment_logic(self, mock_model_cls, mock_init):
        # Setup Mock
        mock_model_instance = MagicMock()
        mock_model_cls.return_value = mock_model_instance
        
        # Mock async response
        mock_response = MagicMock()
        mock_response.text = """
        {
            "core_domain": "Infrastructure",
            "procurement_type": "Works",
            "search_keywords": ["Clinic", "Hospital"],
            "entities": {"authority_name": "PWD", "location_city": "Delhi"},
            "signal_summary": "Construction of Ward"
        }
        """
        
        # Make generate_content_async an AsyncMock
        mock_model_instance.generate_content_async = AsyncMock(return_value=mock_response)
        
        enricher = TenderEnricher(project_id="test-project")
        
        # Run async test
        async def run_test():
            result = await enricher.enrich_tender("Construction of Hospital Ward")
            return result
        
        result = asyncio.run(run_test())
        
        self.assertEqual(result["core_domain"], "Infrastructure")
        self.assertEqual(result["entities"]["authority_name"], "PWD")
        self.assertIn("Clinic", result["search_keywords"])

if __name__ == '__main__':
    unittest.main()
