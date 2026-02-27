
import os
import sys

# Add src to path if needed for imports
sys.path.append(os.getcwd())

from src.search.engine import SmartSearchEngine
import logging

# Configure logging to see output
logging.basicConfig(level=logging.INFO)

print("Attempting to initialize SmartSearchEngine...")
try:
    engine = SmartSearchEngine()
    print("Success: SmartSearchEngine initialized.")
except Exception as e:
    print(f"Error: Failed to initialize SmartSearchEngine: {e}")
    # Print traceback
    import traceback
    traceback.print_exc()
