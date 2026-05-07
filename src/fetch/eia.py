"""
EIA fetcher (Energy Information Administration), API v2.

Uses the backward-compatible seriesid route in the v2 API, which accepts the
same dotted series IDs as the deprecated v1 API. This is the right approach
for the gas price series (PET.EMM_EPMR_PTE_NUS_DPG.W) because the v2 facet-
based route for petroleum requires knowing which facet dimensions to query —
the seriesid route handles that mapping internally.

Resampling weekly → monthly is the transform layer's job, not the fetcher's.
The fetcher returns raw weekly observations; dri.py calls .resample("MS").mean().

Docs: https://www.eia.gov/opendata/documentation.php
"""

from typing import Optional

import pandas as pd
import requests

from src.fetch import FetchError, RAW_DIR, require_env

_BASE_URL = "https://api.eia.gov/v2/seriesid"
_PAGE_LENGTH = 5000  # Max rows per request; weekly 2019-present ≈ 300 rows, well within limit


def _request_eia(series_id: str, start: Optional[str], api_key: str) -> dict:
    url = f"{_BASE_URL}/{series_id}"
    params: dict = {
        "api_key": api_key,
        "sort[0][column]": "period",
        "sort[0][direction]": "asc",
        "length": _PAGE_LENGTH,
    }
    if start:
        params["start"] = start

    resp = requests.get(url, params=params, timeout=60)
    if resp.status_code == 403:
        raise FetchError(f"EIA {series_id}: bad API key or unauthorized (403)")
    if resp.status_code == 404:
        raise FetchError(f"EIA {series_id}: series not found (404) — check the series ID")
    if resp.status_code == 429:
        raise FetchError(f"EIA {series_id}: rate limited (429)")
    if not resp.ok:
        raise FetchError(f"EIA {series_id}: HTTP {resp.status_code} — {resp.text[:200]}")

    body = resp.json()
    if "error" in body:
        raise FetchError(f"EIA {series_id}: API error — {body['error']}")

    return body


def _parse_eia(data: dict, series_id: str) -> pd.DataFrame:
    try:
        records = data["response"]["data"]
    except (KeyError, TypeError) as exc:
        raise FetchError(f"EIA {series_id}: unexpected response shape — {exc}") from exc

    rows = []
    for item in records:
        period = item.get("period", "")
        raw_val = item.get("value")
        if raw_val is None:
            continue
        try:
            value = float(raw_val)
            dt = pd.to_datetime(period)
        except (ValueError, TypeError):
            continue
        rows.append({"date": dt, "value": value})

    if not rows:
        raise FetchError(f"EIA {series_id}: no parseable observations returned")

    df = pd.DataFrame(rows)
    df["series_id"] = series_id
    df["source"] = "eia"
    df["fetched_at"] = pd.Timestamp.utcnow()
    return df.sort_values("date").reset_index(drop=True)


def fetch(
    series_id: str,
    start: Optional[str] = "2019-01-01",
) -> pd.DataFrame:
    """Fetch an EIA series and cache to data/raw/eia/{safe_id}.csv.

    series_id: EIA dotted format, e.g. "PET.EMM_EPMR_PTE_NUS_DPG.W".
    start: ISO date string for earliest observation, default 2019-01-01.

    Returns DataFrame with columns [date, value, series_id, source, fetched_at].
    For weekly series like gas, dates are the Monday of each reporting week.
    Resampling to monthly is handled in the transform layer.

    Raises FetchError on any API or data problem.
    """
    api_key = require_env("EIA_API_KEY")
    data = _request_eia(series_id, start, api_key)
    df = _parse_eia(data, series_id)

    safe_id = series_id.replace(".", "_")
    cache = RAW_DIR / "eia" / f"{safe_id}.csv"
    cache.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache, index=False)

    return df
