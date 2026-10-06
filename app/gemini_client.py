import os
from dotenv import load_dotenv
from google import genai

load_dotenv()

API_KEY = os.getenv("GEMINI_API_KEY")
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

if not API_KEY:
    raise ValueError("GEMINI_API_KEY is not configured")

client = genai.Client(api_key=API_KEY)