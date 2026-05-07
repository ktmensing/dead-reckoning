"""
Fetch layer shared utilities.

Loads .env at import time so any module that imports from here gets keys populated.
RAW_DIR is the canonical cache root; fetchers write {RAW_DIR}/{source}/{series_id}.csv.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

RAW_DIR = Path("data/raw")


class FetchError(Exception):
    pass


def require_env(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise FetchError(
            f"Missing environment variable: {key}. "
            f"Copy .env.example to .env and fill in your API keys."
        )
    return val
