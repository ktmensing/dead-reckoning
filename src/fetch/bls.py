"""
BLS fetcher (Bureau of Labor Statistics), API v2.

BLS v2 batches up to 50 series per POST and returns years in pages of up to
20 years. The fetch_batch() function handles both constraints automatically.

Period codes: BLS months are M01-M12. Period M13 is the annual average — we
skip it so every row maps to a specific month. Keeping M13 would create a
13th "month" row that breaks time-series operations downstream.

Docs: https://www.bls.gov/developers/api_signature_v2.htm
"""

import logging
import warnings
from datetime import date
from typing import Optional

import pandas as pd
import requests

from src.fetch import FetchError, RAW_DIR, require_env

_BASE_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
_MAX_SERIES_PER_REQUEST = 50
_MAX_YEAR_SPAN = 20

log = logging.getLogger(__name__)


def _request_bls(
    series_ids: list,
    start_year: int,
    end_year: int,
    api_key: str,
) -> dict:
    payload = {
        "seriesid": series_ids,
        "startyear": str(start_year),
        "endyear": str(end_year),
        "registrationkey": api_key,
    }
    resp = requests.post(_BASE_URL, json=payload, timeout=60)
    if resp.status_code == 401:
        raise FetchError("BLS: bad API key (401)")
    if resp.status_code == 429:
        raise FetchError("BLS: rate limited (429) — v2 allows 250 requests/day")
    if not resp.ok:
        raise FetchError(f"BLS: HTTP {resp.status_code} — {resp.text[:200]}")

    body = resp.json()
    if body.get("status") != "REQUEST_SUCCEEDED":
        raise FetchError(f"BLS: request failed — {body.get('message', body)}")

    return body


def _parse_bls_series(series_data: dict) -> pd.DataFrame:
    series_id = series_data["seriesID"]
    rows = []
    for item in series_data.get("data", []):
        period = item["period"]
        if period == "M13":
            # Annual average; not a real month
            continue
        month = int(period[1:])  # "M01" → 1
        dt = pd.Timestamp(year=int(item["year"]), month=month, day=1)
        raw_val = item["value"].replace(",", "")
        try:
            value = float(raw_val)
        except ValueError:
            value = None
        rows.append({"date": dt, "value": value})

    df = pd.DataFrame(rows) if rows else pd.DataFrame(columns=["date", "value"])
    df["series_id"] = series_id
    df["source"] = "bls"
    df["fetched_at"] = pd.Timestamp.utcnow()
    return df.sort_values("date").reset_index(drop=True)


def _fetch_series_years(
    series_ids: list, start_year: int, end_year: int, api_key: str
) -> dict:
    """Fetch one batch of series for a year range, returning {series_id: df}."""
    data = _request_bls(series_ids, start_year, end_year, api_key)
    result = {}
    for series_data in data["Results"]["series"]:
        sid = series_data["seriesID"]
        result[sid] = _parse_bls_series(series_data)
    return result


def fetch(
    series_id: str,
    start_year: Optional[int] = None,
) -> pd.DataFrame:
    """Fetch a single BLS series and cache to data/raw/bls/{series_id}.csv.

    Returns DataFrame with columns [date, value, series_id, source, fetched_at].
    Raises FetchError on any API or data problem.
    """
    api_key = require_env("BLS_API_KEY")
    if start_year is None:
        start_year = 2019  # Enough history for Jan 2020 rebase
    end_year = date.today().year

    frames = []
    # Page through 20-year windows if the requested span exceeds the API limit
    for yr_start in range(start_year, end_year + 1, _MAX_YEAR_SPAN):
        yr_end = min(yr_start + _MAX_YEAR_SPAN - 1, end_year)
        batch = _fetch_series_years([series_id], yr_start, yr_end, api_key)
        if series_id in batch:
            frames.append(batch[series_id])

    if not frames:
        raise FetchError(f"BLS {series_id}: no data returned")

    df = (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates("date")
        .sort_values("date")
        .reset_index(drop=True)
    )
    # Refresh fetched_at to reflect this run, not individual page timestamps
    df["fetched_at"] = pd.Timestamp.utcnow()

    cache = RAW_DIR / "bls" / f"{series_id}.csv"
    cache.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache, index=False)

    return df


def fetch_batch(
    series_ids: list,
    start_year: Optional[int] = None,
    strict: bool = True,
) -> dict:
    """Fetch multiple BLS series in batches of up to 50.

    With strict=True (default), raises FetchError on any individual series
    failure. With strict=False, warns and continues — use this when missing one
    series shouldn't abort the full pipeline.

    Returns {series_id: DataFrame}.
    """
    api_key = require_env("BLS_API_KEY")
    if start_year is None:
        start_year = 2019
    end_year = date.today().year

    result: dict = {}

    # Chunk into batches of 50
    for i in range(0, len(series_ids), _MAX_SERIES_PER_REQUEST):
        chunk = series_ids[i : i + _MAX_SERIES_PER_REQUEST]
        frames_by_id: dict = {sid: [] for sid in chunk}

        # Page through years
        for yr_start in range(start_year, end_year + 1, _MAX_YEAR_SPAN):
            yr_end = min(yr_start + _MAX_YEAR_SPAN - 1, end_year)
            try:
                batch = _fetch_series_years(chunk, yr_start, yr_end, api_key)
            except FetchError as exc:
                if strict:
                    raise
                warnings.warn(f"BLS batch fetch failed for years {yr_start}-{yr_end}: {exc}")
                continue
            for sid, df in batch.items():
                frames_by_id[sid].append(df)

        for sid in chunk:
            if not frames_by_id[sid]:
                msg = f"BLS {sid}: no data returned"
                if strict:
                    raise FetchError(msg)
                warnings.warn(msg)
                continue

            df = (
                pd.concat(frames_by_id[sid], ignore_index=True)
                .drop_duplicates("date")
                .sort_values("date")
                .reset_index(drop=True)
            )
            df["fetched_at"] = pd.Timestamp.utcnow()

            cache = RAW_DIR / "bls" / f"{sid}.csv"
            cache.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(cache, index=False)

            result[sid] = df

    return result
