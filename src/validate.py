"""
Validation layer. Per-series freshness checks driven by config metadata.

Three outcomes:
  - FRESH:      latest obs within expected_lag_days  -> proceed silently
  - STALE_OK:   between expected_lag_days and hard_fail_days
                -> if carry_forward: log info, proceed
                -> else: log warning, proceed (transform will surface it)
  - STALE_FAIL: older than hard_fail_days
                -> raise ValidationError; pipeline aborts

Range checks from the old validate_series are dropped: the cadence/lag model
is the right instrument for detecting broken sources. Range checks added noise
without catching the failure modes that matter (stale data, wrong series ID).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

import pandas as pd

log = logging.getLogger(__name__)


class ValidationError(Exception):
    """Raised when a series fails its hard freshness threshold."""


FreshnessStatus = Literal["fresh", "stale_ok", "stale_fail"]


@dataclass
class FreshnessReport:
    series_id: str
    component_id: str
    cadence: str
    latest_observation: pd.Timestamp
    age_days: int
    status: FreshnessStatus
    carried_forward: bool
    expected_lag_days: int
    hard_fail_days: int


def assess_freshness(
    df: pd.DataFrame,
    cfg: dict,
    today: pd.Timestamp | None = None,
) -> FreshnessReport:
    """Classify the freshness of a fetched series against its config thresholds.

    Does NOT mutate the DataFrame. Carry-forward is applied later in the transform.
    Raises ValidationError on STALE_FAIL.

    cfg must contain: id, cadence, expected_lag_days, hard_fail_days.
    carry_forward defaults to False if absent.
    """
    today = today or pd.Timestamp.now().normalize()

    if df.empty or df["value"].isna().all():
        raise ValidationError(
            f"{cfg['id']}: empty or all-null after fetch. "
            f"Check series ID and fetcher."
        )

    latest = pd.to_datetime(df["date"]).max()
    age = int((today - latest).days)
    expected = int(cfg["expected_lag_days"])
    hard = int(cfg["hard_fail_days"])
    carry = bool(cfg.get("carry_forward", False))

    if age <= expected:
        status: FreshnessStatus = "fresh"
    elif age <= hard:
        status = "stale_ok"
    else:
        status = "stale_fail"

    report = FreshnessReport(
        series_id=cfg.get("series_id", "(derived)"),
        component_id=cfg["id"],
        cadence=cfg["cadence"],
        latest_observation=latest,
        age_days=age,
        status=status,
        carried_forward=(status == "stale_ok" and carry),
        expected_lag_days=expected,
        hard_fail_days=hard,
    )

    if status == "stale_fail":
        raise ValidationError(
            f"{cfg['id']}: latest obs {age}d old, exceeds hard_fail_days "
            f"({hard}d). Source likely broken — investigate before next run."
        )
    if status == "stale_ok":
        if carry:
            log.info(
                "%s: %dd old (expected ≤%dd) — carrying forward last value",
                cfg["id"], age, expected,
            )
        else:
            log.warning(
                "%s: %dd old (expected ≤%dd) and carry_forward=false — "
                "value will be missing in next month's index",
                cfg["id"], age, expected,
            )

    return report
