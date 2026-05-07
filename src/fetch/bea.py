"""
BEA fetcher (Bureau of Economic Analysis), NIPA API.

BEA is not used for Phase 1 DRI components — all price-layer inputs come from
FRED, BLS, and EIA. This fetcher exists so Phase 2+ (Mercury, personal income
data) can follow the same fetch/parse/cache pattern without architectural surgery.

series_id format: "{dataset}:{table}:{line}:{frequency}"
  Example: "NIPA:T10101:1:A"  → NIPA Table 1.1.1, line 1, Annual
  Frequency codes: A=Annual, Q=Quarterly, M=Monthly (where available)

For NIPA tables, Year can be "ALL" to fetch the full history. BEA doesn't
publish rate limits but recommends not exceeding ~100 requests/day.

Docs: https://apps.bea.gov/API/docs/index.htm
"""

from typing import Optional

import pandas as pd
import requests

from src.fetch import FetchError, RAW_DIR, require_env

_BASE_URL = "https://apps.bea.gov/api/data"


def _parse_series_id(series_id: str) -> tuple:
    """Parse "NIPA:T10101:1:A" into (dataset, table, line, frequency)."""
    parts = series_id.split(":")
    if len(parts) != 4:
        raise FetchError(
            f"BEA series_id must be 'dataset:table:line:frequency', got: {series_id}"
        )
    return parts[0], parts[1], parts[2], parts[3]


def _request_bea(
    dataset: str,
    table: str,
    line: str,
    frequency: str,
    year: str,
    api_key: str,
) -> dict:
    params = {
        "UserID": api_key,
        "method": "GetData",
        "datasetname": dataset,
        "TableName": table,
        "Frequency": frequency,
        "Year": year,
        "LineNumber": line,
        "ResultFormat": "JSON",
    }
    resp = requests.get(_BASE_URL, params=params, timeout=60)
    if resp.status_code == 401:
        raise FetchError("BEA: bad API key (401)")
    if resp.status_code == 429:
        raise FetchError("BEA: rate limited (429)")
    if not resp.ok:
        raise FetchError(f"BEA: HTTP {resp.status_code} — {resp.text[:200]}")

    body = resp.json()
    # BEA wraps errors in the response body with HTTP 200
    if "Error" in body.get("BEAAPI", {}):
        err = body["BEAAPI"]["Error"]
        raise FetchError(f"BEA API error: {err}")

    return body


def _parse_bea(data: dict, series_id: str) -> pd.DataFrame:
    try:
        records = data["BEAAPI"]["Results"]["Data"]
    except (KeyError, TypeError) as exc:
        raise FetchError(f"BEA {series_id}: unexpected response shape — {exc}") from exc

    rows = []
    for item in records:
        period = item.get("TimePeriod", "")
        raw_val = item.get("DataValue", "").replace(",", "")
        try:
            value = float(raw_val)
        except ValueError:
            continue

        # Parse period: "2020Q1", "2020", "2020-01" depending on frequency
        try:
            if "Q" in period:
                year_part, q = period.split("Q")
                month = (int(q) - 1) * 3 + 1
                dt = pd.Timestamp(year=int(year_part), month=month, day=1)
            elif len(period) == 4:
                dt = pd.Timestamp(year=int(period), month=1, day=1)
            else:
                dt = pd.to_datetime(period)
        except (ValueError, TypeError):
            continue

        rows.append({"date": dt, "value": value})

    if not rows:
        raise FetchError(f"BEA {series_id}: no parseable observations returned")

    df = pd.DataFrame(rows)
    df["series_id"] = series_id
    df["source"] = "bea"
    df["fetched_at"] = pd.Timestamp.utcnow()
    return df.sort_values("date").reset_index(drop=True)


def fetch(
    series_id: str,
    year: str = "ALL",
) -> pd.DataFrame:
    """Fetch a BEA NIPA series and cache to data/raw/bea/{safe_id}.csv.

    series_id format: "NIPA:T10101:1:A" (dataset:table:line:frequency).
    year: "ALL" for full history, or comma-separated years like "2020,2021,2022".

    Returns DataFrame with columns [date, value, series_id, source, fetched_at].
    Raises FetchError on any API or data problem.
    """
    api_key = require_env("BEA_API_KEY")
    dataset, table, line, frequency = _parse_series_id(series_id)
    data = _request_bea(dataset, table, line, frequency, year, api_key)
    df = _parse_bea(data, series_id)

    # Use a filesystem-safe filename (colons not allowed on some systems)
    safe_id = series_id.replace(":", "_")
    cache = RAW_DIR / "bea" / f"{safe_id}.csv"
    cache.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache, index=False)

    return df
