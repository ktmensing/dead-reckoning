"""Tests for src/transform/dri.py — rebasing, weighting, normalization, mortgage math."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from src.transform.dri import _compute_mortgage_payment, _to_monthly, build_dri


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _monthly_series(start: str, end: str, value: float = 100.0) -> pd.DataFrame:
    dates = pd.date_range(start=start, end=end, freq="MS")
    return pd.DataFrame({
        "date": dates,
        "value": [value] * len(dates),
        "series_id": "TEST",
        "source": "test",
        "fetched_at": pd.Timestamp.utcnow(),
    })


def _growing_series(start: str, end: str, start_val: float, growth_per_month: float) -> pd.DataFrame:
    dates = pd.date_range(start=start, end=end, freq="MS")
    values = [start_val + i * growth_per_month for i in range(len(dates))]
    return pd.DataFrame({
        "date": dates,
        "value": values,
        "series_id": "TEST",
        "source": "test",
        "fetched_at": pd.Timestamp.utcnow(),
    })


def _minimal_config(tmp_path: Path, components: list, cpi_series_id: str = "TESTCPI") -> Path:
    cfg = {
        "dri_components": components,
        "cpi_headline": {"source": "test", "series_id": cpi_series_id},
    }
    p = tmp_path / "series.yaml"
    p.write_text(yaml.dump(cfg))
    return p


def _two_component_timeseries() -> dict:
    return {
        "comp_a": _monthly_series("2019-01-01", "2022-12-01", 100.0),
        "comp_b": _growing_series("2019-01-01", "2022-12-01", 100.0, 100.0 / 47),
        "cpi_headline": _monthly_series("2019-01-01", "2022-12-01", 250.0),
    }


# ---------------------------------------------------------------------------
# _to_monthly
# ---------------------------------------------------------------------------

def test_to_monthly_last_monthly_series():
    df = _monthly_series("2020-01-01", "2020-06-01", 100.0)
    s = _to_monthly(df, "last")
    assert len(s) == 6
    assert s.iloc[0] == 100.0


def test_to_monthly_mean_weekly_series():
    dates = pd.date_range(start="2020-01-06", periods=8, freq="W-MON")
    vals = [3.0, 3.2, 3.1, 3.3, 2.9, 3.0, 3.1, 3.2]
    df = pd.DataFrame({"date": dates, "value": vals})
    s = _to_monthly(df, "mean")
    assert len(s) == 2
    assert abs(s.iloc[0] - np.mean(vals[:4])) < 0.01


# ---------------------------------------------------------------------------
# Mortgage payment math
# ---------------------------------------------------------------------------

def test_mortgage_payment_known_case():
    idx = pd.date_range("2023-01-01", periods=1, freq="MS")
    price = pd.Series([400_000.0], index=idx)
    rate = pd.Series([7.0], index=idx)
    payment = _compute_mortgage_payment(price, rate)
    assert abs(payment.iloc[0] - 2128.0) < 5.0, f"Expected ~2128, got {payment.iloc[0]:.2f}"


def test_mortgage_payment_zero_rate():
    idx = pd.date_range("2023-01-01", periods=1, freq="MS")
    price = pd.Series([360_000.0], index=idx)
    rate = pd.Series([0.0], index=idx)
    payment = _compute_mortgage_payment(price, rate)
    assert abs(payment.iloc[0] - 800.0) < 1.0


# ---------------------------------------------------------------------------
# build_dri — rebasing and weighting
# ---------------------------------------------------------------------------

def test_rebase_to_jan2020(tmp_path):
    ts = _two_component_timeseries()
    components = [
        {"id": "comp_a", "weight": 0.5, "source": "test", "series_id": "A"},
        {"id": "comp_b", "weight": 0.5, "source": "test", "series_id": "B"},
    ]
    cfg_path = _minimal_config(tmp_path, components)
    result = build_dri(ts, {}, config_path=cfg_path)
    panel = result.panel

    jan2020 = panel[panel["date"] == pd.Timestamp("2020-01-01")]
    assert len(jan2020) == 1
    assert abs(jan2020["comp_a"].iloc[0] - 100.0) < 0.01
    assert abs(jan2020["comp_b"].iloc[0] - 100.0) < 0.01
    assert abs(jan2020["dri"].iloc[0] - 100.0) < 0.01


def test_weight_normalization_excludes_deferred(tmp_path):
    ts = _two_component_timeseries()
    components = [
        {"id": "comp_a", "weight": 0.5, "source": "test", "series_id": "A"},
        {"id": "comp_b", "weight": 0.3, "source": "test", "series_id": "B"},
        {"id": "deferred_comp", "weight": 0.2, "source": "test",
         "series_id": "X", "deferred": True},
    ]
    cfg_path = _minimal_config(tmp_path, components)
    result = build_dri(ts, {}, config_path=cfg_path)
    weights = result.weights

    assert "deferred_comp" not in weights.index
    assert abs(weights.sum() - 1.0) < 1e-9
    assert abs(weights["comp_a"] - 0.625) < 1e-9
    assert abs(weights["comp_b"] - 0.375) < 1e-9


def test_flat_series_stays_at_100(tmp_path):
    ts = {
        "comp_a": _monthly_series("2019-01-01", "2022-12-01", 200.0),
        "cpi_headline": _monthly_series("2019-01-01", "2022-12-01", 300.0),
    }
    components = [
        {"id": "comp_a", "weight": 1.0, "source": "test", "series_id": "A"},
    ]
    cfg_path = _minimal_config(tmp_path, components)
    result = build_dri(ts, {}, config_path=cfg_path)

    assert (result.panel["dri"].round(6) == 100.0).all()


def test_panel_has_required_columns(tmp_path):
    ts = _two_component_timeseries()
    components = [
        {"id": "comp_a", "weight": 0.5, "source": "test", "series_id": "A"},
        {"id": "comp_b", "weight": 0.5, "source": "test", "series_id": "B"},
    ]
    cfg_path = _minimal_config(tmp_path, components)
    result = build_dri(ts, {}, config_path=cfg_path)

    for col in ["date", "dri", "cpi", "comp_a", "comp_b"]:
        assert col in result.panel.columns, f"Missing column: {col}"


# ---------------------------------------------------------------------------
# excluded_from_index
# ---------------------------------------------------------------------------

def test_excluded_from_index_not_in_weighted_sum(tmp_path):
    """A component with excluded_from_index=True must not contribute to DRI."""
    ts = {
        "comp_a": _monthly_series("2019-01-01", "2022-12-01", 100.0),
        "comp_excluded": _growing_series("2019-01-01", "2022-12-01", 100.0, 10.0),
        "cpi_headline": _monthly_series("2019-01-01", "2022-12-01", 250.0),
    }
    # comp_excluded is doubling but excluded — DRI should stay flat (driven only by comp_a)
    components = [
        {"id": "comp_a", "weight": 0.5, "source": "test", "series_id": "A"},
        {"id": "comp_excluded", "weight": 0.3, "source": "test",
         "series_id": "X", "excluded_from_index": True},
    ]
    cfg_path = _minimal_config(tmp_path, components)
    result = build_dri(ts, {}, config_path=cfg_path)

    # comp_excluded not in weights
    assert "comp_excluded" not in result.weights.index
    # weights should normalize to 1.0 (only comp_a)
    assert abs(result.weights.sum() - 1.0) < 1e-9
    # DRI should be flat at 100 (only flat comp_a contributes)
    assert (result.panel["dri"].round(4) == 100.0).all()


def test_excluded_from_index_still_in_panel(tmp_path):
    """Even excluded components should appear as columns in the panel."""
    ts = {
        "comp_a": _monthly_series("2019-01-01", "2022-12-01", 100.0),
        "comp_excluded": _monthly_series("2019-01-01", "2022-12-01", 200.0),
        "cpi_headline": _monthly_series("2019-01-01", "2022-12-01", 250.0),
    }
    components = [
        {"id": "comp_a", "weight": 0.5, "source": "test", "series_id": "A"},
        {"id": "comp_excluded", "weight": 0.3, "source": "test",
         "series_id": "X", "excluded_from_index": True},
    ]
    cfg_path = _minimal_config(tmp_path, components)
    result = build_dri(ts, {}, config_path=cfg_path)

    assert "comp_excluded" in result.panel.columns


# ---------------------------------------------------------------------------
# data_as_of
# ---------------------------------------------------------------------------

def test_data_as_of_reflects_pre_ffill_date(tmp_path):
    """data_as_of should be the last real observation date, not the ffill-extended date."""
    # comp_a has data through 2022-06, then a gap. We record data_as_of before ffill.
    dates_short = pd.date_range("2019-01-01", "2022-06-01", freq="MS")
    dates_long = pd.date_range("2019-01-01", "2022-12-01", freq="MS")

    comp_a_short = pd.DataFrame({
        "date": dates_short,
        "value": [100.0] * len(dates_short),
        "series_id": "A", "source": "test", "fetched_at": pd.Timestamp.utcnow(),
    })

    ts = {
        "comp_a": comp_a_short,
        "comp_b": _monthly_series("2019-01-01", "2022-12-01", 100.0),
        "cpi_headline": _monthly_series("2019-01-01", "2022-12-01", 250.0),
    }
    components = [
        {"id": "comp_a", "weight": 0.5, "source": "test", "series_id": "A",
         "cadence": "monthly", "carry_forward": True},
        {"id": "comp_b", "weight": 0.5, "source": "test", "series_id": "B"},
    ]
    cfg_path = _minimal_config(tmp_path, components)
    result = build_dri(ts, {}, config_path=cfg_path)

    # data_as_of for comp_a should be 2022-06-01, not the ffill-extended date
    assert result.data_as_of["comp_a"] == pd.Timestamp("2022-06-01")


# ---------------------------------------------------------------------------
# carry-forward fill limits per cadence
# ---------------------------------------------------------------------------

def test_carry_forward_monthly_fills_exactly_1(tmp_path):
    """Monthly carry_forward should fill at most 1 month beyond last real obs."""
    # comp_a has data through 2022-03, then stops. comp_b extends to 2022-06.
    # After ffill(limit=1), comp_a should have value at 2022-04 but NaN at 2022-05+.
    dates_a = pd.date_range("2019-01-01", "2022-03-01", freq="MS")
    dates_b = pd.date_range("2019-01-01", "2022-12-01", freq="MS")

    ts = {
        "comp_a": pd.DataFrame({
            "date": dates_a, "value": [100.0] * len(dates_a),
            "series_id": "A", "source": "test", "fetched_at": pd.Timestamp.utcnow(),
        }),
        "comp_b": pd.DataFrame({
            "date": dates_b, "value": [100.0] * len(dates_b),
            "series_id": "B", "source": "test", "fetched_at": pd.Timestamp.utcnow(),
        }),
        "cpi_headline": _monthly_series("2019-01-01", "2022-12-01", 250.0),
    }
    # No BLS cutoff since source is "test", not "bls" — panel extends to comp_b's end
    components = [
        {"id": "comp_a", "weight": 0.5, "source": "test", "series_id": "A",
         "cadence": "monthly", "carry_forward": True},
        {"id": "comp_b", "weight": 0.5, "source": "test", "series_id": "B"},
    ]
    cfg_path = _minimal_config(tmp_path, components)
    result = build_dri(ts, {}, config_path=cfg_path)
    panel = result.panel.set_index("date")

    # 2022-04: one month beyond last real obs — should be filled
    assert not pd.isna(panel.loc[pd.Timestamp("2022-04-01"), "comp_a"])
    # 2022-05: two months beyond — should be NaN (limit=1)
    assert pd.isna(panel.loc[pd.Timestamp("2022-05-01"), "comp_a"])


def test_carry_forward_quarterly_fills_exactly_3(tmp_path):
    """Quarterly carry_forward should fill at most 3 months beyond last real obs."""
    dates_q = pd.date_range("2019-01-01", "2022-03-01", freq="MS")
    dates_b = pd.date_range("2019-01-01", "2022-12-01", freq="MS")

    ts = {
        "comp_q": pd.DataFrame({
            "date": dates_q, "value": [200.0] * len(dates_q),
            "series_id": "Q", "source": "test", "fetched_at": pd.Timestamp.utcnow(),
        }),
        "comp_b": pd.DataFrame({
            "date": dates_b, "value": [100.0] * len(dates_b),
            "series_id": "B", "source": "test", "fetched_at": pd.Timestamp.utcnow(),
        }),
        "cpi_headline": _monthly_series("2019-01-01", "2022-12-01", 250.0),
    }
    components = [
        {"id": "comp_q", "weight": 0.5, "source": "test", "series_id": "Q",
         "cadence": "quarterly", "carry_forward": True},
        {"id": "comp_b", "weight": 0.5, "source": "test", "series_id": "B"},
    ]
    cfg_path = _minimal_config(tmp_path, components)
    result = build_dri(ts, {}, config_path=cfg_path)
    panel = result.panel.set_index("date")

    # 2022-04, 2022-05, 2022-06: within 3 months — should be filled
    for m in ["2022-04-01", "2022-05-01", "2022-06-01"]:
        assert not pd.isna(panel.loc[pd.Timestamp(m), "comp_q"]), f"{m} should be filled"
    # 2022-07: 4 months beyond — should be NaN
    assert pd.isna(panel.loc[pd.Timestamp("2022-07-01"), "comp_q"])
