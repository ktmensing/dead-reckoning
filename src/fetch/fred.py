"""
FRED fetcher (St. Louis Fed).

Uses the hand-rolled requests approach rather than fredapi to keep the
request/parse split testable and to avoid library-level changes in response
shape masking API behavior changes.

Endpoint: GET /fred/series/observations
Docs: https://fred.stlouisfed.org/docs/api/fred/
"""

from datetime import date
from typing import Optional

import pandas as pd
import requests

from src.fetch import FetchError, RAW_DIR, require_env

_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"


def _request_fred(
    series_id: str,
    start: Optional[date],
    end: Optional[date],
    api_key: str,
) -> dict:
    params: dict = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "asc",
    }
    if start:
        params["observation_start"] = start.isoformat()
    if end:
        params["observation_end"] = end.isoformat()

    resp = requests.get(_BASE_URL, params=params, timeout=30)
    if resp.status_code == 400:
        raise FetchError(f"FRED {series_id}: bad request — {resp.text[:200]}")
    if resp.status_code == 401:
        raise FetchError(f"FRED {series_id}: bad API key (401)")
    if resp.status_code == 429:
        raise FetchError(f"FRED {series_id}: rate limited (429)")
    if not resp.ok:
        raise FetchError(f"FRED {series_id}: HTTP {resp.status_code} — {resp.text[:200]}")

    return resp.json()


def _parse_fred(data: dict, series_id: str) -> pd.DataFrame:
    observations = data.get("observations", [])
    rows = []
    for obs in observations:
        # FRED uses "." for missing values
        if obs["value"] == ".":
            continue
        rows.append({"date": obs["date"], "value": float(obs["value"])})

    if not rows:
        raise FetchError(f"FRED {series_id}: no non-null observations returned")

    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    df["series_id"] = series_id
    df["source"] = "fred"
    df["fetched_at"] = pd.Timestamp.utcnow()
    return df.sort_values("date").reset_index(drop=True)


def fetch(
    series_id: str,
    start: Optional[date] = None,
    end: Optional[date] = None,
) -> pd.DataFrame:
    """Fetch a FRED series and cache to data/raw/fred/{series_id}.csv.

    Returns DataFrame with columns [date, value, series_id, source, fetched_at].
    Dates are pd.Timestamp at the start of the reported period.
    Raises FetchError on any API or data problem.
    """
    api_key = require_env("FRED_API_KEY")
    data = _request_fred(series_id, start, end, api_key)
    df = _parse_fred(data, series_id)

    cache = RAW_DIR / "fred" / f"{series_id}.csv"
    cache.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache, index=False)

    return df
