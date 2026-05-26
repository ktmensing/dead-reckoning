"""
DRI Behavior Layer (DRI-B) — dashboard of directional stress readings.

Not a composite index. Each indicator is assessed independently for direction
(stress / relief / flat) based on its most recent month-over-month change.
The output is a panel of readings suitable for a Datawrapper table chart and
a simple stress count for the Territory summary line.

Indicators (all free, mostly automated via FRED):
  savings_rate          FRED PSAVERT   — personal savings rate (down = stress)
  revolving_credit      FRED REVOLSL   — revolving credit outstanding (up = stress)
  multiple_job_holders  FRED LNS12026620 — persons at work 2+ jobs (up = stress)
  debt_service_ratio    FRED TDSP      — debt payments / disposable income (up = stress)
  nyf_sce_miss_prob     manual         — NY Fed SCE prob. of missing min payment (up = stress)
  abi_bankruptcies      manual         — monthly consumer bankruptcy filings (up = stress)

Place this file at: src/transform/dri_b.py
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

INDICATORS: dict[str, dict] = {
    "savings_rate": {
        "label": "Personal Savings Rate",
        "stress_direction": "down",   # falling savings = stress
        "unit": "%",
        "cadence": "monthly",
        "source": "FRED PSAVERT",
        "note": "Households drawing down savings buffer.",
    },
    "revolving_credit": {
        "label": "Revolving Credit Outstanding",
        "stress_direction": "up",     # rising credit card debt = stress
        "unit": "$B",
        "cadence": "monthly",
        "source": "FRED REVOLSL",
        "note": "Credit card debt accumulation.",
    },
    "multiple_job_holders": {
        "label": "Multiple Job Holders",
        "stress_direction": "up",
        "unit": "thousands",
        "cadence": "monthly",
        "source": "FRED LNS12026620",
        "note": "Persons at work in 2+ jobs.",
    },
    "debt_service_ratio": {
        "label": "Household Debt Service Ratio",
        "stress_direction": "up",
        "unit": "% of disposable income",
        "cadence": "quarterly",
        "source": "FRED TDSP",
        "note": "Debt payments as share of disposable income.",
    },
    "nyf_sce_miss_prob": {
        "label": "Prob. of Missing Debt Payment",
        "stress_direction": "up",
        "unit": "%",
        "cadence": "monthly",
        "source": "NY Fed SCE (manual)",
        "note": "Mean probability of missing minimum debt payment in next 3 months.",
    },
    "abi_bankruptcies": {
        "label": "Consumer Bankruptcy Filings",
        "stress_direction": "up",
        "unit": "filings",
        "cadence": "monthly",
        "source": "ABI (manual)",
        "note": "Monthly total consumer bankruptcy filings.",
    },
}

# A move of less than this threshold (in %) is classified as flat.
FLAT_THRESHOLD_PCT = 0.25


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class DriBResult:
    panel: pd.DataFrame        # one row per indicator, all fields
    stress_count: int          # number of indicators currently in stress
    relief_count: int          # number of indicators currently in relief
    total_available: int       # indicators with data present
    stress_pct: float          # stress_count / total_available


# ---------------------------------------------------------------------------
# Build function
# ---------------------------------------------------------------------------

def build_dri_b(timeseries: dict[str, pd.DataFrame]) -> DriBResult:
    """
    Build the DRI-B dashboard panel.

    Args:
        timeseries: dict keyed by indicator id, value is a DataFrame with
                    at minimum [date, value] columns (same contract as DRI
                    component DataFrames). Date column must be parseable as
                    datetime.

    Returns:
        DriBResult with a panel DataFrame and aggregate stress counts.
    """
    rows = []

    for indicator_id, config in INDICATORS.items():
        if indicator_id not in timeseries:
            # Indicator not yet populated — include as a placeholder row.
            rows.append({
                "indicator_id": indicator_id,
                "label": config["label"],
                "cadence": config["cadence"],
                "source": config["source"],
                "latest_date": None,
                "latest_value": None,
                "mom_pct": None,
                "yoy_pct": None,
                "direction": "missing",
                "unit": config["unit"],
                "note": config["note"],
            })
            continue

        df = timeseries[indicator_id].copy()
        df["date"] = pd.to_datetime(df["date"])
        series = df.set_index("date")["value"].sort_index().dropna()

        if len(series) == 0:
            rows.append({
                "indicator_id": indicator_id,
                "label": config["label"],
                "cadence": config["cadence"],
                "source": config["source"],
                "latest_date": None,
                "latest_value": None,
                "mom_pct": None,
                "yoy_pct": None,
                "direction": "missing",
                "unit": config["unit"],
                "note": config["note"],
            })
            continue

        latest = series.iloc[-1]
        latest_date = series.index[-1]

        # Month-over-month (or period-over-period for quarterly)
        mom_pct: float | None = None
        if len(series) >= 2:
            prev = series.iloc[-2]
            if prev != 0:
                mom_pct = (latest - prev) / abs(prev) * 100

        # Year-over-year (12 periods back)
        yoy_pct: float | None = None
        if len(series) >= 13:
            year_ago = series.iloc[-13]
            if year_ago != 0:
                yoy_pct = (latest - year_ago) / abs(year_ago) * 100

        # Classify direction relative to stress axis
        direction = _classify(mom_pct, config["stress_direction"])

        rows.append({
            "indicator_id": indicator_id,
            "label": config["label"],
            "cadence": config["cadence"],
            "source": config["source"],
            "latest_date": latest_date.strftime("%Y-%m-%d"),
            "latest_value": round(float(latest), 4),
            "mom_pct": round(mom_pct, 2) if mom_pct is not None else None,
            "yoy_pct": round(yoy_pct, 2) if yoy_pct is not None else None,
            "direction": direction,
            "unit": config["unit"],
            "note": config["note"],
        })

    panel = pd.DataFrame(rows, columns=[
        "indicator_id", "label", "cadence", "source",
        "latest_date", "latest_value", "mom_pct", "yoy_pct",
        "direction", "unit", "note",
    ])

    available = panel[panel["direction"] != "missing"]
    stress_count = int((available["direction"] == "stress").sum())
    relief_count = int((available["direction"] == "relief").sum())
    total_available = len(available)
    stress_pct = stress_count / total_available if total_available > 0 else 0.0

    return DriBResult(
        panel=panel,
        stress_count=stress_count,
        relief_count=relief_count,
        total_available=total_available,
        stress_pct=stress_pct,
    )


def _classify(mom_pct: float | None, stress_direction: str) -> str:
    """Classify a period-over-period change as stress / relief / flat / unknown."""
    if mom_pct is None:
        return "unknown"
    if abs(mom_pct) < FLAT_THRESHOLD_PCT:
        return "flat"
    if stress_direction == "up":
        return "stress" if mom_pct > 0 else "relief"
    else:  # stress_direction == "down"
        return "stress" if mom_pct < 0 else "relief"
