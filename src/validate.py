"""
Series validation.

Validate is the layer between fetch and transform. It converts "the API returned
something" into "the data is suitable for building the index." Fail loud: a
ValidationError at 8am Sunday is better than a silently wrong chart.
"""

from typing import Optional

import pandas as pd


class ValidationError(Exception):
    pass


# Per-series max age overrides.
# Monthly BLS CPI releases with a ~5-6 week lag, so on any given day the latest
# data is 35-67 days old — 90 days covers the full lag window.
# Quarterly series (MSPUS, TERMCBCCALLNS) update every ~90 days with an
# additional processing lag; 150 days gives a full quarter plus a month of slack.
_MAX_AGE_DAYS: dict = {
    "MSPUS": 150,
    "TERMCBCCALLNS": 150,
}

_DEFAULT_MAX_AGE_DAYS = 90  # Covers monthly series with government publication lag

# Per-series plausibility ranges (min, max). Add entries as needed.
# Values are in the series' native units (CPI index, $/gal, rate %).
_RANGE_CHECKS: dict = {
    "CUSR0000SAF11": (100, 500),    # CPI food at home
    "CUSR0000SA0":   (100, 500),    # CPI all items
    "CUSR0000SETE":  (100, 1000),   # CPI auto insurance
    "CUSR0000SEFV":  (100, 500),    # CPI dining out
    "CUSR0000SEHF":  (50, 500),     # CPI energy services
    "CUSR0000SETA02": (50, 500),    # CPI used cars
    "APU0000708111": (0.5, 20.0),   # Egg price per dozen ($)
    "CUUR0000SEHD":  (100, 1000),   # CPI renters insurance
    "PET.EMM_EPMR_PTE_NUS_DPG.W": (0.5, 10.0),  # Gas $/gal
    "TERMCBCCALLNS": (5.0, 40.0),   # CC interest rate (%)
    "MORTGAGE30US":  (1.0, 25.0),   # 30yr mortgage rate (%)
    "MSPUS":         (15_000, 5_000_000),  # Median home price ($); series starts 1963 at ~$17.8k
}


def validate_series(
    df: pd.DataFrame,
    series_id: str,
    max_age_days: int = None,
    range_check: bool = True,
) -> None:
    """Raise ValidationError if df fails any quality check.

    Checks (in order):
      1. Not empty.
      2. Latest observation is within max_age_days of today.
      3. Not all-null values.
      4. Per-series numeric range check (if configured and range_check=True).

    max_age_days defaults to _MAX_AGE_DAYS[series_id] if configured, else
    _DEFAULT_MAX_AGE_DAYS (90). Quarterly series need a higher threshold than
    monthly ones due to publication lag.
    """
    if df.empty:
        raise ValidationError(f"{series_id}: empty DataFrame")

    if max_age_days is None:
        max_age_days = _MAX_AGE_DAYS.get(series_id, _DEFAULT_MAX_AGE_DAYS)

    latest_date = pd.to_datetime(df["date"]).max()
    age_days = (pd.Timestamp.now() - latest_date).days
    if age_days > max_age_days:
        raise ValidationError(
            f"{series_id}: latest observation is {age_days} days old "
            f"(threshold: {max_age_days} days, latest: {latest_date.date()})"
        )

    if df["value"].isna().all():
        raise ValidationError(f"{series_id}: all values are null")

    if range_check and series_id in _RANGE_CHECKS:
        lo, hi = _RANGE_CHECKS[series_id]
        non_null = df["value"].dropna()
        out_of_range = non_null[(non_null < lo) | (non_null > hi)]
        if len(out_of_range) > 0:
            sample = out_of_range.iloc[0]
            raise ValidationError(
                f"{series_id}: value {sample:.4f} outside expected range "
                f"[{lo}, {hi}]"
            )
