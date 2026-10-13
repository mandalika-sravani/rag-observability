"""Load settings from a .env file in the project root (if present).

Variables already set in the shell win over .env values.
"""
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:  # python-dotenv not installed: fall back to shell env vars only
    pass
