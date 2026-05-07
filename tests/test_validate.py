"""Tests for src/validate.py — all failure modes with fixture DataFrames."""

import pandas as pd
import pytest

from src.validate import ValidationError, validate_series


def _make_df(n_months: int = 36, all_null: bool = False, stale_months: int = 0) -> pd.DataFrame:
    """Return a minimal DataFrame in canonical schema."""
    end = pd.Timestamp.now().normalize() - pd.DateOffset(months=stale_months)
    dates = pd.date_range(end=end, periods=n_months, freq="MS")
    values = [None if all_null else float(i + 100) for i in range(n_months)]
    return pd.DataFrame({
        "date": dates,
        "value": values,
        "series_id": "TEST000",
        "source": "test",
        "fetched_at": pd.Timestamp.utcnow(),
    })


def test_passes_for_valid_series():
    df = _make_df()
    validate_series(df, "TEST000")  # Must not raise


def test_raises_on_empty_df():
    df = pd.DataFrame(columns=["date", "value", "series_id", "source", "fetched_at"])
    with pytest.raises(ValidationError, match="empty"):
        validate_series(df, "TEST000")


def test_raises_on_stale_data():
    # 5 months old exceeds the 90-day default for unknown series
    df = _make_df(stale_months=5)
    with pytest.raises(ValidationError, match="days old"):
        validate_series(df, "TEST000")


def test_passes_within_age_threshold():
    # 1 month old is well within the 90-day default
    df = _make_df(stale_months=1)
    validate_series(df, "TEST000")


def test_raises_when_all_null():
    df = _make_df(all_null=True)
    df["date"] = pd.date_range(end=pd.Timestamp.now(), periods=len(df), freq="MS")
    with pytest.raises(ValidationError, match="null"):
        validate_series(df, "TEST000")


def test_raises_on_out_of_range_value():
    df = _make_df()
    # gas prices (PET.EMM_EPMR_PTE_NUS_DPG.W) should be $0.50–$10/gal
    df["value"] = 99.0  # Way above $10
    df["date"] = pd.date_range(end=pd.Timestamp.now(), periods=len(df), freq="MS")
    with pytest.raises(ValidationError, match="range"):
        validate_series(df, "PET.EMM_EPMR_PTE_NUS_DPG.W")


def test_range_check_disabled():
    df = _make_df()
    df["value"] = 99.0  # Out of range for gas, but range_check=False
    df["date"] = pd.date_range(end=pd.Timestamp.now(), periods=len(df), freq="MS")
    validate_series(df, "PET.EMM_EPMR_PTE_NUS_DPG.W", range_check=False)


def test_unknown_series_skips_range_check():
    df = _make_df()
    df["value"] = 9999.0
    # No range check configured for UNKNOWN — should not raise
    validate_series(df, "UNKNOWN_SERIES")
