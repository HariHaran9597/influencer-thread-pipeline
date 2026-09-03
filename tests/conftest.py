"""Force offline mock mode for every test, even on machines with live keys in
.env (load_dotenv does not override variables that already exist)."""
import os
import sys
from pathlib import Path

os.environ["MOCK_LLM"] = "1"
os.environ["TAVILY_API_KEY"] = ""
os.environ["GROQ_API_KEY"] = ""
os.environ["GOOGLE_API_KEY"] = ""
os.environ["LANGSMITH_TRACING"] = "false"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
