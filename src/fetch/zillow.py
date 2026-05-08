"""
Zillow research data fetcher.

ZORI and other Zillow research datasets are distributed as wide-format CSVs:
one row per geography, one column per month-end date. This fetcher:

1. Downloads the requested dataset CSV from files.zillowstatic.com.
2. Filters to the requested region (default: United States, RegionType=country).
3. Melts the date columns into the canonical [date, value, series_id, source, fetched_at] schema.
4. Caches to data/raw/zillow/{dataset}.csv in canonical schema.

Datasets supported:
    zori_sfrcondomfr_sm_sa
        URL: https://files.zillowstatic.com/research/public_csvs/zori/
             Metro_zori_uc_sfrcondomfr_sm_sa_month.csv
        Note: National (United States) data is the first row (SizeRank=0) in the
        Metro file. Zillow removed the separate Country_ file; Metro_ now covers
        all geographies including RegionType=country.

If Zillow changes the URL pattern or filename, update DATASET_URLS below and
document the change in the module docstring.
"""

import io

import pandas as pd
import requests

from src.fetch import FetchError, RAW_DIR

DATASET_URLS: dict = {
    "zori_sfrcondomfr_sm_sa": (
        "https://files.zillowstatic.com/research/public_csvs/zori/"
        "Metro_zori_uc_sfrcondomfr_sm_sa_month.csv"
    ),
}


def _request_zillow(dataset: str) -> str:
    url = DATASET_URLS.get(dataset)
    if url is None:
        raise FetchError(
            f"Zillow: unknown dataset '{dataset}'. Known datasets: {list(DATASET_URLS)}"
        )
    resp = requests.get(url, timeout=60)
    if not resp.ok:
        raise FetchError(
            f"Zillow {dataset}: HTTP {resp.status_code} fetching {url}"
        )
    return resp.text


def _parse_zillow(csv_text: str, dataset: str, region: str) -> pd.DataFrame:
    raw = pd.read_csv(io.StringIO(csv_text))

    mask = (raw["RegionType"] == "country") & (raw["RegionName"] == region)
    matched = raw[mask]
    if matched.empty:
        raise FetchError(
            f"Zillow {dataset}: no row with RegionType='country', RegionName='{region}'"
        )

    # Identify date columns by attempting to parse each column name as a date.
    date_cols = []
    for col in raw.columns:
        try:
            pd.to_datetime(col)
            date_cols.append(col)
        except (ValueError, TypeError):
            pass

    if not date_cols:
        raise FetchError(f"Zillow {dataset}: no date columns found in CSV")

    row = matched.iloc[0]
    records = []
    for col in date_cols:
        val = row[col]
        try:
            fval = float(val)
        except (ValueError, TypeError):
            continue
        if pd.isna(fval):
            continue
        records.append({"date": col, "value": fval})

    if not records:
        raise FetchError(
            f"Zillow {dataset}: no numeric values in date columns for region '{region}'"
        )

    df = pd.DataFrame(records)
    df["date"] = pd.to_datetime(df["date"])
    df["series_id"] = dataset
    df["source"] = "zillow"
    df["fetched_at"] = pd.Timestamp.utcnow()
    return df.sort_values("date").reset_index(drop=True)


def fetch(
    dataset: str,
    region: str = "United States",
    *,
    cache: bool = True,
) -> pd.DataFrame:
    """Fetch a Zillow research dataset and cache to data/raw/zillow/{dataset}.csv.

    Returns DataFrame with columns [date, value, series_id, source, fetched_at].
    Dates are pd.Timestamp (month-end as published by Zillow).
    Raises FetchError on any HTTP or data problem.
    """
    csv_text = _request_zillow(dataset)
    df = _parse_zillow(csv_text, dataset, region)

    if cache:
        cache_path = RAW_DIR / "zillow" / f"{dataset}.csv"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(cache_path, index=False)

    return df
