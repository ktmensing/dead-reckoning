"""Tests for src/transform/dri.py — rebasing, weighting, normalization, mortgage math."""

from pathlib import Path
import tempfile

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


# ---------------------------------------------------------------------------
# _to_monthly
# ---------------------------------------------------------------------------

def test_to_monthly_last_monthly_series():
    df = _monthly_series("2020-01-01", "2020-06-01", 100.0)
    s = _to_monthly(df, "last")
    assert s.index.freq is not None or len(s) == 6
    assert s.iloc[0] == 100.0


def test_to_monthly_mean_weekly_series():
    dates = pd.date_range(start="2020-01-06", periods=8, freq="W-MON")
    vals = [3.0, 3.2, 3.1, 3.3, 2.9, 3.0, 3.1, 3.2]
    df = pd.DataFrame({"date": dates, "value": vals})
    s = _to_monthly(df, "mean")
    # Jan 2020 should average first 4 weeks ≈ 3.15, Feb should average remaining
    assert len(s) == 2
    assert abs(s.iloc[0] - np.mean(vals[:4])) < 0.01


# ---------------------------------------------------------------------------
# Mortgage payment math
# ---------------------------------------------------------------------------

def test_mortgage_payment_known_case():
    # $400k home, 7% rate, 20% down → monthly payment ~$2,128
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
    # 0% rate: P&I = principal / 360 = 288000 / 360 = 800
    assert abs(payment.iloc[0] - 800.0) < 1.0


# ---------------------------------------------------------------------------
# build_dri — rebasing and weighting
# ---------------------------------------------------------------------------

def _two_component_timeseries() -> tuple:
    """Two components with known values: comp_a stays flat, comp_b doubles."""
    ts = {
        "comp_a": _monthly_series("2019-01-01", "2022-12-01", 100.0),
        "comp_b": _growing_series("2019-01-01", "2022-12-01", 100.0, 100.0 / 47),
        "cpi_headline": _monthly_series("2019-01-01", "2022-12-01", 250.0),
    }
    return ts


def test_rebase_to_jan2020(tmp_path):
    ts = _two_component_timeseries()
    components = [
        {"id": "comp_a", "weight": 0.5, "source": "test", "series_id": "A"},
        {"id": "comp_b", "weight": 0.5, "source": "test", "series_id": "B"},
    ]
    cfg_path = _minimal_config(tmp_path, components)
    panel, weights = build_dri(ts, config_path=cfg_path)

    jan2020 = panel[panel["date"] == pd.Timestamp("2020-01-01")]
    assert len(jan2020) == 1
    # Both components should be exactly 100 at Jan 2020
    assert abs(jan2020["comp_a"].iloc[0] - 100.0) < 0.01
    assert abs(jan2020["comp_b"].iloc[0] - 100.0) < 0.01
    # DRI at base should also be 100
    assert abs(jan2020["dri"].iloc[0] - 100.0) < 0.01


def test_weight_normalization_excludes_deferred(tmp_path):
    ts = _two_component_timeseries()
    # comp_a weight=0.5, comp_b weight=0.3, deferred weight=0.2
    components = [
        {"id": "comp_a", "weight": 0.5, "source": "test", "series_id": "A"},
        {"id": "comp_b", "weight": 0.3, "source": "test", "series_id": "B"},
        {"id": "deferred_comp", "weight": 0.2, "source": "test",
         "series_id": "X", "deferred": True},
    ]
    cfg_path = _minimal_config(tmp_path, components)
    panel, weights = build_dri(ts, config_path=cfg_path)

    # Deferred should not appear in weights
    assert "deferred_comp" not in weights.index
    # Remaining weights should normalize to 1.0
    assert abs(weights.sum() - 1.0) < 1e-9
    # Individual normalized weights: 0.5/(0.5+0.3)=0.625 and 0.3/0.8=0.375
    assert abs(weights["comp_a"] - 0.625) < 1e-9
    assert abs(weights["comp_b"] - 0.375) < 1e-9


def test_flat_series_stays_at_100(tmp_path):
    # If all components are flat, DRI should equal 100 throughout
    ts = {
        "comp_a": _monthly_series("2019-01-01", "2022-12-01", 200.0),
        "cpi_headline": _monthly_series("2019-01-01", "2022-12-01", 300.0),
    }
    components = [
        {"id": "comp_a", "weight": 1.0, "source": "test", "series_id": "A"},
    ]
    cfg_path = _minimal_config(tmp_path, components)
    panel, _ = build_dri(ts, config_path=cfg_path)

    assert (panel["dri"].round(6) == 100.0).all()


def test_panel_has_required_columns(tmp_path):
    ts = _two_component_timeseries()
    components = [
        {"id": "comp_a", "weight": 0.5, "source": "test", "series_id": "A"},
        {"id": "comp_b", "weight": 0.5, "source": "test", "series_id": "B"},
    ]
    cfg_path = _minimal_config(tmp_path, components)
    panel, weights = build_dri(ts, config_path=cfg_path)

    for col in ["date", "dri", "cpi", "comp_a", "comp_b"]:
        assert col in panel.columns, f"Missing column: {col}"
