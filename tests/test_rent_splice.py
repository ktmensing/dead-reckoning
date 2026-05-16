"""Tests for build_rent_with_backfill — rent chain-linking logic."""

import pandas as pd
import pytest

from src.transform.dri import build_rent_with_backfill


def _make_bls(start: str, end: str, start_val: float = 180.0,
              growth_pct: float = 0.003) -> pd.Series:
    """Monthly BLS series with constant percent growth."""
    idx = pd.date_range(start=start, end=end, freq="MS")
    vals = [start_val * ((1 + growth_pct) ** i) for i in range(len(idx))]
    return pd.Series(vals, index=idx, name="bls_rent")


def _make_zori(start: str, end: str, start_val: float = 1500.0,
               growth_pct: float = 0.004) -> pd.Series:
    """Monthly ZORI series — starts later than BLS, in different units."""
    idx = pd.date_range(start=start, end=end, freq="MS")
    vals = [start_val * ((1 + growth_pct) ** i) for i in range(len(idx))]
    return pd.Series(vals, index=idx, name="rent")


# ---------------------------------------------------------------------------
# Chain-link splice correctness
# ---------------------------------------------------------------------------

def test_rent_splice_chain_links():
    """ZORI values at and after its start are unchanged; pre-2015 values follow
    BLS percent changes and the series is continuous with no jump at the splice."""
    bls = _make_bls("2000-01-01", "2015-01-01")
    zori = _make_zori("2015-01-01", "2022-12-01")

    result = build_rent_with_backfill(zori, bls)

    # Post-splice: ZORI values must be exactly preserved
    for dt in zori.index:
        assert abs(result[dt] - zori[dt]) < 1e-9, \
            f"ZORI value at {dt.date()} changed by splice: {result[dt]:.4f} != {zori[dt]:.4f}"

    # No jump at the splice point — value immediately before 2015-01 should
    # produce a smooth transition (ratio ≈ 1 + BLS monthly growth rate)
    splice_val = result[pd.Timestamp("2015-01-01")]
    pre_splice_val = result[pd.Timestamp("2014-12-01")]
    bls_ratio = bls[pd.Timestamp("2015-01-01")] / bls[pd.Timestamp("2014-12-01")]
    result_ratio = splice_val / pre_splice_val
    assert abs(result_ratio - bls_ratio) < 1e-6, \
        f"Jump at splice: ratio {result_ratio:.6f} != BLS ratio {bls_ratio:.6f}"


def test_rent_splice_year_2000():
    """Pre-splice values should be meaningfully lower than the ZORI launch value,
    consistent with historical rent trends."""
    bls = _make_bls("2000-01-01", "2015-01-01", start_val=180.0, growth_pct=0.003)
    zori = _make_zori("2015-01-01", "2022-12-01", start_val=1500.0, growth_pct=0.004)

    result = build_rent_with_backfill(zori, bls)

    # Jan 2000 should be well below the 2015 splice value
    val_2000 = result[pd.Timestamp("2000-02-01")]  # first valid (2000-01 has NaN pct_change)
    val_2015 = result[pd.Timestamp("2015-01-01")]
    assert val_2000 < val_2015, \
        f"Jan 2000 value ({val_2000:.2f}) should be below 2015 splice ({val_2015:.2f})"

    # With 3% monthly BLS growth over 180 months (15 years), the ratio should be
    # approximately (1.003)^180 ≈ 1.72, so val_2000 ≈ val_2015 / 1.72
    expected_ratio = (1.003 ** 180)
    actual_ratio = val_2015 / val_2000
    assert abs(actual_ratio - expected_ratio) < 0.1, \
        f"15-year ratio {actual_ratio:.3f} not close to expected {expected_ratio:.3f}"


def test_rent_splice_preserves_length():
    """Output series should span from the BLS start to the ZORI end."""
    bls = _make_bls("2000-01-01", "2015-01-01")
    zori = _make_zori("2015-01-01", "2022-12-01")

    result = build_rent_with_backfill(zori, bls)

    assert result.index.min() <= pd.Timestamp("2000-02-01")  # first valid BLS pct_change
    assert result.index.max() == zori.index.max()
