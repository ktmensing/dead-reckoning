"""
DRI Behavior Layer (DRI-B) — dashboard of directional stress readings.

Not a composite index. Each indicator is assessed independently for direction
(stress / relief / flat) based on its most recent period-over-period change.
The output is a panel of readings for a Datawrapper table chart and a stress
count for the Territory summary line.

Indicators:
  savings_rate              FRED PSAVERT        — personal savings rate (down = stress)
  revolving_credit          FRED REVOLSL        — revolving credit outstanding (up = stress)
  multiple_job_holders      FRED LNS12026620    — persons at work 2+ jobs (up = stress)
  debt_service_ratio        FRED TDSP           — debt payments / disposable income (up = stress)
  nyf_sce_miss_prob         manual              — NY Fed SCE prob. of missing min payment (up = stress)
  abi_bankruptcies_total    manual (quarterly)  — all nonbusiness chapters (up = stress) [HEADLINE]
  abi_bankruptcies_ch7      manual (quarterly)  — chapter 7 liquidations (up = stress)
  abi_bankruptcies_ch13     manual (quarterly)  — chapter 13 reorganizations (up = stress)

abi_bankruptcies_ch7 and ch13 are included as texture, not headline signals.
The ch7/ch13 ratio (ch7 rising faster than ch13) indicates capitulation phase.
abi_bankruptcies_ch11 (upper-income K-shape marker) is tracked in series.yaml
but excluded from the DRI-B panel — use percentage-change trend for analysis.

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
    # Bankruptcy indicators — quarterly data; period-over-period = quarter-over-quarter
    "abi_bankruptcies_total": {
        "label": "Consumer Bankruptcy Filings",
        "stress_direction": "up",
        "unit": "filings/quarter",
        "cadence": "quarterly",
        "source": "U.S. Courts F-2 (manual)",
        "note": "Nonbusiness all chapters. Primary DRI-B stress signal — lagging but unambiguous.",
    },
    "abi_bankruptcies_ch7": {
        "label": "Chapter 7 Filings",
        "stress_direction": "up",
        "unit": "filings/quarter",
        "cadence": "quarterly",
        "source": "U.S. Courts F-2 (manual)",
        "note": "Liquidation filings. Rising Ch.7 vs. Ch.13 = capitulation phase.",
    },
    "abi_bankruptcies_ch13": {
        "label": "Chapter 13 Filings",
        "stress_direction": "up",
        "unit": "filings/quarter",
        "cadence": "quarterly",
        "source": "U.S. Courts F-2 (manual)",
        "note": "Reorganization filings. Tends to rise before Ch.7 in a stress cycle.",
    },
}

# A period-over-period move of less than this threshold (in %) is classified as flat.
FLAT_THRESHOLD_PCT = 0.25


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class DriBResult:
    panel: pd.DataFrame        # one row per indicator, all fields
    stress_count: int          # number of indicators currently in stress
    relief_count: int          # number of indicators currently in relief
    total_available: int       # indicators with data present (not missing)
    stress_pct: float          # stress_count / total_available
    ch7_ch13_ratio: float | None  # latest Ch.7 / Ch.13 ratio (None if data missing)


# ---------------------------------------------------------------------------
# Build function
# ---------------------------------------------------------------------------

def build_dri_b(timeseries: dict[str, pd.DataFrame]) -> DriBResult:
    """
    Build the DRI-B dashboard panel.

    Args:
        timeseries: dict keyed by indicator id, value is a DataFrame with
                    at minimum [date, value] columns. Date column must be
                    parseable as datetime.

    Returns:
        DriBResult with a panel DataFrame, aggregate stress counts, and
        the Ch.7/Ch.13 ratio for editorial use.
    """
    rows = []

    for indicator_id, config in INDICATORS.items():
        if indicator_id not in timeseries:
            rows.append(_missing_row(indicator_id, config))
            continue

        df = timeseries[indicator_id].copy()
        df["date"] = pd.to_datetime(df["date"])
        series = df.set_index("date")["value"].sort_index().dropna()

        if len(series) == 0:
            rows.append(_missing_row(indicator_id, config))
            continue

        latest = series.iloc[-1]
        latest_date = series.index[-1]

        # Period-over-period (MoM for monthly, QoQ for quarterly)
        period_pct: float | None = None
        if len(series) >= 2:
            prev = series.iloc[-2]
            if prev != 0:
                period_pct = (latest - prev) / abs(prev) * 100

        # Year-over-year: 4 periods back for quarterly, 12 for monthly
        yoy_periods = 4 if config["cadence"] == "quarterly" else 12
        yoy_pct: float | None = None
        if len(series) > yoy_periods:
            year_ago = series.iloc[-(yoy_periods + 1)]
            if year_ago != 0:
                yoy_pct = (latest - year_ago) / abs(year_ago) * 100

        direction = _classify(period_pct, config["stress_direction"])

        rows.append({
            "indicator_id": indicator_id,
            "label": config["label"],
            "cadence": config["cadence"],
            "source": config["source"],
            "latest_date": latest_date.strftime("%Y-%m-%d"),
            "latest_value": round(float(latest), 4),
            "period_pct": round(period_pct, 2) if period_pct is not None else None,
            "yoy_pct": round(yoy_pct, 2) if yoy_pct is not None else None,
            "direction": direction,
            "unit": config["unit"],
            "note": config["note"],
        })

    panel = pd.DataFrame(rows, columns=[
        "indicator_id", "label", "cadence", "source",
        "latest_date", "latest_value", "period_pct", "yoy_pct",
        "direction", "unit", "note",
    ])

    available = panel[panel["direction"] != "missing"]
    stress_count = int((available["direction"] == "stress").sum())
    relief_count = int((available["direction"] == "relief").sum())
    total_available = len(available)
    stress_pct = stress_count / total_available if total_available > 0 else 0.0

    # Ch.7 / Ch.13 ratio — for editorial use in the Territory brief
    ch7_ch13_ratio = _compute_ch7_ch13_ratio(timeseries)

    return DriBResult(
        panel=panel,
        stress_count=stress_count,
        relief_count=relief_count,
        total_available=total_available,
        stress_pct=stress_pct,
        ch7_ch13_ratio=ch7_ch13_ratio,
    )


def _missing_row(indicator_id: str, config: dict) -> dict:
    return {
        "indicator_id": indicator_id,
        "label": config["label"],
        "cadence": config["cadence"],
        "source": config["source"],
        "latest_date": None,
        "latest_value": None,
        "period_pct": None,
        "yoy_pct": None,
        "direction": "missing",
        "unit": config["unit"],
        "note": config["note"],
    }


def _classify(period_pct: float | None, stress_direction: str) -> str:
    """Classify a period-over-period change as stress / relief / flat / unknown."""
    if period_pct is None:
        return "unknown"
    if abs(period_pct) < FLAT_THRESHOLD_PCT:
        return "flat"
    if stress_direction == "up":
        return "stress" if period_pct > 0 else "relief"
    else:  # stress_direction == "down"
        return "stress" if period_pct < 0 else "relief"


def _compute_ch7_ch13_ratio(timeseries: dict[str, pd.DataFrame]) -> float | None:
    """Compute latest Ch.7 / Ch.13 ratio for stress-intensity editorial read.

    High ratio (Ch.7 rising faster than Ch.13) indicates capitulation phase:
    households giving up rather than reorganizing. A rising Ch.13 / falling
    Ch.7 ratio signals anticipatory stress that hasn't yet reached capitulation.
    """
    if "abi_bankruptcies_ch7" not in timeseries or "abi_bankruptcies_ch13" not in timeseries:
        return None
    try:
        ch7 = (
            timeseries["abi_bankruptcies_ch7"]
            .copy()
            .assign(date=lambda df: pd.to_datetime(df["date"]))
            .set_index("date")["value"]
            .sort_index()
            .dropna()
        )
        ch13 = (
            timeseries["abi_bankruptcies_ch13"]
            .copy()
            .assign(date=lambda df: pd.to_datetime(df["date"]))
            .set_index("date")["value"]
            .sort_index()
            .dropna()
        )
        # Align on shared dates
        common = ch7.index.intersection(ch13.index)
        if len(common) == 0:
            return None
        latest = common[-1]
        ch7_val = float(ch7.loc[latest])
        ch13_val = float(ch13.loc[latest])
        if ch13_val == 0:
            return None
        return round(ch7_val / ch13_val, 3)
    except Exception:
        return None
