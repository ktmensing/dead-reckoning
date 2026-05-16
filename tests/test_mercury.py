"""Tests for Mercury divergence and partisan distortion functions."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from src.fetch.mercury import calculate_partisan_distortion, z_score


_CONFIG_PATH = Path("config/series.yaml")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _monthly_index(start: str, periods: int, freq: str = "MS") -> pd.DatetimeIndex:
    return pd.date_range(start=start, periods=periods, freq=freq)


# ---------------------------------------------------------------------------
# calculate_partisan_distortion — unit consistency
# ---------------------------------------------------------------------------

def test_partisan_distortion_unit_consistency():
    """gap_pp = mich - dri_yoy_pct in percentage points (units must match)."""
    # 10 years of monthly data so z_score has enough warmup
    idx = _monthly_index("2015-01-01", 120)

    # DRI grows at exactly 4% YoY (compound monthly)
    dri = pd.Series([100 * (1.04 ** (i / 12)) for i in range(120)], index=idx)

    # MICH alternates ±0.3 around 4.0 so rolling std is non-zero (avoids 0/0 in z_score)
    mich_vals = [4.0 + 0.3 * (1 if i % 2 == 0 else -1) for i in range(120)]
    mich = pd.Series(mich_vals, index=idx)

    result = calculate_partisan_distortion(mich, dri, window=60)

    assert not result.empty, "should produce non-empty output with enough warmup"

    # gap_pp must equal mich - dri_yoy_pct within floating-point tolerance
    diff = (result["gap_pp"] - (result["mich"] - result["dri_yoy_pct"])).abs()
    assert diff.max() < 1e-9, f"gap_pp != mich - dri_yoy_pct (max diff {diff.max()})"


# ---------------------------------------------------------------------------
# calculate_partisan_distortion — flag logic
# ---------------------------------------------------------------------------

def test_partisan_distortion_flag():
    """partisan_flag = 1 when |gap_z| > 1.0."""
    # Build 15 years of data: DRI at 2% YoY, MICH normally ~2%
    idx = _monthly_index("2005-01-01", 180)
    dri = pd.Series([100 * (1.02 ** (i / 12)) for i in range(180)], index=idx)

    # MICH: baseline 2.0 for first 10 years, then spike to 7.0 for 24 months
    mich_vals = [2.0] * 120 + [7.0] * 24 + [2.0] * 36
    mich = pd.Series(mich_vals, index=idx)

    result = calculate_partisan_distortion(mich, dri, window=60)

    assert not result.empty

    # During the spike window, gap is ~5pp above baseline → should produce z > 1 → flag = 1
    spike_rows = result[(result["date"] >= "2015-01-01") & (result["date"] <= "2016-12-01")]
    assert not spike_rows.empty, "no rows in spike window"
    assert (spike_rows["partisan_flag"] == 1).any(), (
        f"expected flag=1 during spike; got {spike_rows['partisan_flag'].tolist()}"
    )

    # Before the spike, gap is near zero → flag should be 0
    pre_spike_rows = result[(result["date"] >= "2007-01-01") & (result["date"] <= "2009-12-01")]
    assert (pre_spike_rows["partisan_flag"] == 0).all(), (
        "expected flag=0 in pre-spike baseline period"
    )


# ---------------------------------------------------------------------------
# YAML structural checks
# ---------------------------------------------------------------------------

def test_mercury_composite_excludes_mich():
    """umich_expectations (MICH) must be in mercury_caveats, not mercury_components."""
    cfg = yaml.safe_load(_CONFIG_PATH.read_text())
    comp_ids = [c["id"] for c in cfg.get("mercury_components", [])]
    caveat_ids = [c["id"] for c in cfg.get("mercury_caveats", [])]

    assert "umich_expectations" not in comp_ids, (
        "umich_expectations should not be in mercury_components (moved to mercury_caveats)"
    )
    assert "umich_expectations" in caveat_ids, (
        "umich_expectations should be in mercury_caveats"
    )


def test_mercury_weights_sum_to_one():
    """mercury_components weights must sum to 1.0."""
    cfg = yaml.safe_load(_CONFIG_PATH.read_text())
    total = sum(c.get("weight", 0) for c in cfg.get("mercury_components", []))
    assert abs(total - 1.0) < 1e-9, (
        f"mercury_components weights sum to {total:.6f}, expected 1.0"
    )
