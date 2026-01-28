
from google import genai
import os
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

client = genai.Client(api_key=api_key)

models = [
    "text-embedding-004",
    "models/text-embedding-004",
    "gemini-embedding-001",
    "models/gemini-embedding-001"
]

for m in models:
    print(f"\nTesting model: {m}")
    try:
        response = client.models.embed_content(
            model=m,
            contents="Hello world",
        )
        print(f"SUCCESS: {m}")
    except Exception as e:
        print(f"FAILED: {e}")
