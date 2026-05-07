"""
CSV persistence for derived and published data.

Plain CSV is the right choice for Phase 1: files are human-inspectable,
version-controllable (Datawrapper reads directly from GitHub), and there's no
analytical query surface yet that would justify DuckDB. Revisit at Phase 6.
"""

from pathlib import Path

import pandas as pd

DERIVED_DIR = Path("data/derived")
PUBLISHED_DIR = Path("data/published")


def save_derived(name: str, df: pd.DataFrame) -> Path:
    """Write df to data/derived/{name}.csv. Returns the path written."""
    DERIVED_DIR.mkdir(parents=True, exist_ok=True)
    path = DERIVED_DIR / f"{name}.csv"
    df.to_csv(path, index=False)
    return path


def load_derived(name: str) -> pd.DataFrame:
    """Load data/derived/{name}.csv. Raises FileNotFoundError if missing."""
    path = DERIVED_DIR / f"{name}.csv"
    return pd.read_csv(path, parse_dates=["date"])


def save_published(name: str, df: pd.DataFrame) -> Path:
    """Write df to data/published/{name}.csv. Returns the path written."""
    PUBLISHED_DIR.mkdir(parents=True, exist_ok=True)
    path = PUBLISHED_DIR / f"{name}.csv"
    df.to_csv(path, index=False)
    return path
