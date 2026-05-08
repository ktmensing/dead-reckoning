"""Tests for src/validate.py — assess_freshness with fixed today for determinism."""

import pandas as pd
import pytest

from src.validate import FreshnessReport, ValidationError, assess_freshness


_TODAY = pd.Timestamp("2026-05-01")


def _make_df(age_days: int, all_null: bool = False) -> pd.DataFrame:
    """Return a DataFrame whose latest date is exactly `age_days` before _TODAY."""
    latest = _TODAY - pd.Timedelta(days=age_days)
    # Use 30-day steps so the last row is exactly `latest` (no month-start snapping).
    dates = [latest - pd.Timedelta(days=30 * i) for i in range(11, -1, -1)]
    values = [None if all_null else float(i + 100) for i in range(12)]
    return pd.DataFrame({
        "date": dates,
        "value": values,
        "series_id": "TEST",
        "source": "test",
        "fetched_at": pd.Timestamp.utcnow(),
    })


def _monthly_cfg(carry: bool = True) -> dict:
    return {
        "id": "test_series",
        "series_id": "TEST",
        "cadence": "monthly",
        "expected_lag_days": 45,
        "hard_fail_days": 90,
        "carry_forward": carry,
    }


def _quarterly_cfg(carry: bool = True) -> dict:
    return {
        "id": "test_quarterly",
        "series_id": "TESTQ",
        "cadence": "quarterly",
        "expected_lag_days": 95,
        "hard_fail_days": 200,
        "carry_forward": carry,
    }


# ---------------------------------------------------------------------------
# fresh
# ---------------------------------------------------------------------------

def test_fresh_status():
    df = _make_df(age_days=10)
    report = assess_freshness(df, _monthly_cfg(), today=_TODAY)
    assert report.status == "fresh"
    assert report.age_days == 10
    assert not report.carried_forward


def test_fresh_at_boundary():
    df = _make_df(age_days=45)
    report = assess_freshness(df, _monthly_cfg(), today=_TODAY)
    assert report.status == "fresh"


# ---------------------------------------------------------------------------
# stale_ok
# ---------------------------------------------------------------------------

def test_stale_ok_with_carry_forward():
    df = _make_df(age_days=60)
    report = assess_freshness(df, _monthly_cfg(carry=True), today=_TODAY)
    assert report.status == "stale_ok"
    assert report.carried_forward is True


def test_stale_ok_without_carry_forward():
    df = _make_df(age_days=60)
    report = assess_freshness(df, _monthly_cfg(carry=False), today=_TODAY)
    assert report.status == "stale_ok"
    assert report.carried_forward is False


def test_stale_ok_at_boundary():
    df = _make_df(age_days=89)
    report = assess_freshness(df, _monthly_cfg(), today=_TODAY)
    assert report.status == "stale_ok"


# ---------------------------------------------------------------------------
# stale_fail
# ---------------------------------------------------------------------------

def test_stale_fail_raises():
    df = _make_df(age_days=100)
    with pytest.raises(ValidationError, match="hard_fail_days"):
        assess_freshness(df, _monthly_cfg(), today=_TODAY)


def test_stale_fail_message_contains_series_id():
    df = _make_df(age_days=100)
    with pytest.raises(ValidationError, match="test_series"):
        assess_freshness(df, _monthly_cfg(), today=_TODAY)


def test_quarterly_stale_ok():
    df = _make_df(age_days=120)
    report = assess_freshness(df, _quarterly_cfg(), today=_TODAY)
    assert report.status == "stale_ok"


def test_quarterly_stale_fail():
    df = _make_df(age_days=210)
    with pytest.raises(ValidationError):
        assess_freshness(df, _quarterly_cfg(), today=_TODAY)


# ---------------------------------------------------------------------------
# empty / null
# ---------------------------------------------------------------------------

def test_raises_on_empty_df():
    df = pd.DataFrame(columns=["date", "value"])
    with pytest.raises(ValidationError, match="empty"):
        assess_freshness(df, _monthly_cfg(), today=_TODAY)


def test_raises_on_all_null():
    df = _make_df(age_days=10, all_null=True)
    with pytest.raises(ValidationError, match="null"):
        assess_freshness(df, _monthly_cfg(), today=_TODAY)


# ---------------------------------------------------------------------------
# FreshnessReport fields
# ---------------------------------------------------------------------------

def test_report_fields():
    df = _make_df(age_days=30)
    report = assess_freshness(df, _monthly_cfg(), today=_TODAY)
    assert isinstance(report, FreshnessReport)
    assert report.series_id == "TEST"
    assert report.component_id == "test_series"
    assert report.cadence == "monthly"
    assert report.expected_lag_days == 45
    assert report.hard_fail_days == 90
    assert report.age_days == 30


# ---------------------------------------------------------------------------
# Formula-derived threshold values (from series.yaml after expansion)
# ---------------------------------------------------------------------------

def _weekly_cfg() -> dict:
    return {
        "id": "test_weekly",
        "series_id": "TESTW",
        "cadence": "weekly",
        "expected_lag_days": 10,
        "hard_fail_days": 20,
        "carry_forward": False,
    }


def _monthly_formula_cfg() -> dict:
    """Monthly BLS series (lag 14d, cadence 30d): exp 45, hard 100."""
    return {
        "id": "test_monthly_formula",
        "series_id": "TESTM",
        "cadence": "monthly",
        "expected_lag_days": 45,
        "hard_fail_days": 100,
        "carry_forward": True,
    }


def _monthly_plus_cfg() -> dict:
    """Monthly APU/ZORI (lag 25d, cadence 30d): exp 50, hard 110."""
    return {
        "id": "test_monthly_plus",
        "series_id": "TESTMP",
        "cadence": "monthly",
        "expected_lag_days": 50,
        "hard_fail_days": 110,
        "carry_forward": True,
    }


def _quarterly_formula_cfg() -> dict:
    """Quarterly FRED (lag 95d, cadence 90d): exp 210, hard 320."""
    return {
        "id": "test_quarterly_formula",
        "series_id": "TESTQF",
        "cadence": "quarterly",
        "expected_lag_days": 210,
        "hard_fail_days": 320,
        "carry_forward": True,
    }


def test_weekly_formula_fresh():
    df = _make_df(age_days=5)
    report = assess_freshness(df, _weekly_cfg(), today=_TODAY)
    assert report.status == "fresh"


def test_weekly_formula_stale_ok():
    df = _make_df(age_days=15)
    report = assess_freshness(df, _weekly_cfg(), today=_TODAY)
    assert report.status == "stale_ok"


def test_weekly_formula_stale_fail():
    df = _make_df(age_days=25)
    with pytest.raises(ValidationError):
        assess_freshness(df, _weekly_cfg(), today=_TODAY)


def test_monthly_formula_fresh():
    df = _make_df(age_days=30)
    report = assess_freshness(df, _monthly_formula_cfg(), today=_TODAY)
    assert report.status == "fresh"


def test_monthly_formula_stale_ok_at_67_days():
    """March data at 67 days old should be stale_ok, not stale_fail (the original bug)."""
    df = _make_df(age_days=67)
    report = assess_freshness(df, _monthly_formula_cfg(), today=_TODAY)
    assert report.status == "stale_ok"


def test_monthly_formula_stale_fail():
    df = _make_df(age_days=105)
    with pytest.raises(ValidationError):
        assess_freshness(df, _monthly_formula_cfg(), today=_TODAY)


def test_monthly_plus_formula_fresh():
    df = _make_df(age_days=40)
    report = assess_freshness(df, _monthly_plus_cfg(), today=_TODAY)
    assert report.status == "fresh"


def test_monthly_plus_formula_stale_ok():
    df = _make_df(age_days=80)
    report = assess_freshness(df, _monthly_plus_cfg(), today=_TODAY)
    assert report.status == "stale_ok"


def test_monthly_plus_formula_stale_fail():
    df = _make_df(age_days=115)
    with pytest.raises(ValidationError):
        assess_freshness(df, _monthly_plus_cfg(), today=_TODAY)


def test_quarterly_formula_fresh():
    df = _make_df(age_days=100)
    report = assess_freshness(df, _quarterly_formula_cfg(), today=_TODAY)
    assert report.status == "fresh"


def test_quarterly_formula_stale_ok():
    df = _make_df(age_days=250)
    report = assess_freshness(df, _quarterly_formula_cfg(), today=_TODAY)
    assert report.status == "stale_ok"


def test_quarterly_formula_stale_fail():
    df = _make_df(age_days=330)
    with pytest.raises(ValidationError):
        assess_freshness(df, _quarterly_formula_cfg(), today=_TODAY)
