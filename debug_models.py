
import os
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    print("No API Key")
    exit(1)

genai.configure(api_key=api_key)

models_to_test = [
    "models/text-embedding-004",
    "models/embedding-001",
    "text-embedding-004",
    "embedding-001",
]

for m in models_to_test:
    print(f"\nTesting {m}...")
    try:
        res = genai.embed_content(
            model=m,
            content="Hello world",
            task_type="retrieval_document"
        )
        print(f"SUCCESS: {m}")
        # print(res)
    except Exception as e:
        print(f"FAILED {m}: {e}")

    print(f"Testing {m} (no task_type)...")
    try:
        res = genai.embed_content(
            model=m,
            content="Hello world"
        )
        print(f"SUCCESS: {m} (no task_type)")
    except Exception as e:
        print(f"FAILED {m} (no task_type): {e}")
