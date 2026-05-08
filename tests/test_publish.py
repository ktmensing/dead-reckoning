"""
Tests for src/publish/datawrapper_csv.py — column shape checks.

These tests are intentionally strict: a renamed column breaks a live Datawrapper
chart template, so the tests should fail immediately if names change.
"""

import pandas as pd
import pytest

from src.publish.datawrapper_csv import (
    publish_dri_components,
    publish_dri_component_table,
    publish_dri_metadata,
    publish_dri_vs_cpi,
)
from src.validate import FreshnessReport


def _make_panel() -> tuple:
    dates = pd.date_range("2020-01-01", periods=24, freq="MS")
    n = len(dates)
    panel = pd.DataFrame({
        "date": dates,
        "dri": [100.0 + i * 0.5 for i in range(n)],
        "cpi": [100.0 + i * 0.3 for i in range(n)],
        "food_at_home": [100.0 + i * 0.4 for i in range(n)],
        "gas": [100.0 + i * 0.6 for i in range(n)],
    })
    weights = pd.Series({"food_at_home": 0.6, "gas": 0.4})
    return panel, weights


def _make_data_as_of() -> dict:
    return {
        "food_at_home": pd.Timestamp("2026-03-01"),
        "gas": pd.Timestamp("2026-04-15"),
    }


def _make_freshness(weights: pd.Series) -> dict:
    reports = {}
    for cid, w in weights.items():
        reports[cid] = FreshnessReport(
            series_id=f"SID_{cid.upper()}",
            component_id=cid,
            cadence="monthly",
            latest_observation=pd.Timestamp("2026-03-01"),
            age_days=60,
            status="stale_ok",
            carried_forward=True,
            expected_lag_days=45,
            hard_fail_days=90,
        )
    return reports


def test_dri_vs_cpi_columns(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    panel, _ = _make_panel()
    (tmp_path / "data" / "published").mkdir(parents=True)
    publish_dri_vs_cpi(panel)

    result = pd.read_csv(tmp_path / "data" / "published" / "dri_vs_cpi.csv")
    assert list(result.columns) == ["Date", "Dead Reckoning Index", "Official CPI"]


def test_dri_vs_cpi_date_format(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    panel, _ = _make_panel()
    (tmp_path / "data" / "published").mkdir(parents=True)
    publish_dri_vs_cpi(panel)

    result = pd.read_csv(tmp_path / "data" / "published" / "dri_vs_cpi.csv")
    assert result["Date"].iloc[0] == "2020-01-01"


def test_dri_components_columns(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    panel, weights = _make_panel()
    (tmp_path / "data" / "published").mkdir(parents=True)
    publish_dri_components(panel, weights)

    result = pd.read_csv(tmp_path / "data" / "published" / "dri_components.csv")
    assert result.columns[0] == "Date"
    assert "Food at Home" in result.columns
    assert "Gas" in result.columns


def test_dri_components_is_wide_format(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    panel, weights = _make_panel()
    (tmp_path / "data" / "published").mkdir(parents=True)
    publish_dri_components(panel, weights)

    result = pd.read_csv(tmp_path / "data" / "published" / "dri_components.csv")
    assert len(result) == 24
    assert result.shape[1] == 3  # Date + 2 components


def test_dri_component_table_columns(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    panel, weights = _make_panel()
    (tmp_path / "data" / "published").mkdir(parents=True)
    publish_dri_component_table(panel, weights, _make_data_as_of())

    result = pd.read_csv(tmp_path / "data" / "published" / "dri_component_table.csv")
    assert list(result.columns) == ["Component", "Data as of", "Latest", "MoM %", "YoY %", "Weight"]


def test_dri_component_table_data_as_of_position(tmp_path, monkeypatch):
    """Data as of must be the second column (index 1)."""
    monkeypatch.chdir(tmp_path)
    panel, weights = _make_panel()
    (tmp_path / "data" / "published").mkdir(parents=True)
    publish_dri_component_table(panel, weights, _make_data_as_of())

    result = pd.read_csv(tmp_path / "data" / "published" / "dri_component_table.csv")
    assert result.columns[1] == "Data as of"


def test_dri_component_table_data_as_of_format(tmp_path, monkeypatch):
    """Data as of values should be YYYY-MM-DD strings."""
    monkeypatch.chdir(tmp_path)
    panel, weights = _make_panel()
    (tmp_path / "data" / "published").mkdir(parents=True)
    publish_dri_component_table(panel, weights, _make_data_as_of())

    result = pd.read_csv(tmp_path / "data" / "published" / "dri_component_table.csv")
    # food_at_home row should have 2026-03-01
    food_row = result[result["Component"] == "Food at Home"]
    assert food_row["Data as of"].iloc[0] == "2026-03-01"


def test_dri_component_table_without_data_as_of(tmp_path, monkeypatch):
    """publish_dri_component_table must work without data_as_of (defaults to None)."""
    monkeypatch.chdir(tmp_path)
    panel, weights = _make_panel()
    (tmp_path / "data" / "published").mkdir(parents=True)
    publish_dri_component_table(panel, weights)

    result = pd.read_csv(tmp_path / "data" / "published" / "dri_component_table.csv")
    assert "Data as of" in result.columns
    assert result["Data as of"].isna().all()


def test_dri_component_table_row_count(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    panel, weights = _make_panel()
    (tmp_path / "data" / "published").mkdir(parents=True)
    publish_dri_component_table(panel, weights)

    result = pd.read_csv(tmp_path / "data" / "published" / "dri_component_table.csv")
    assert len(result) == 2


def test_dri_component_table_yoy_requires_13_months(tmp_path, monkeypatch):
    """YoY should be None/NaN when panel has fewer than 13 rows."""
    monkeypatch.chdir(tmp_path)
    dates = pd.date_range("2020-01-01", periods=12, freq="MS")
    panel = pd.DataFrame({
        "date": dates,
        "dri": [100.0] * 12,
        "cpi": [100.0] * 12,
        "food_at_home": [100.0 + i for i in range(12)],
    })
    weights = pd.Series({"food_at_home": 1.0})
    (tmp_path / "data" / "published").mkdir(parents=True)
    publish_dri_component_table(panel, weights)

    result = pd.read_csv(tmp_path / "data" / "published" / "dri_component_table.csv")
    assert pd.isna(result["YoY %"].iloc[0])


# ---------------------------------------------------------------------------
# publish_dri_metadata
# ---------------------------------------------------------------------------

def test_dri_metadata_columns(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _, weights = _make_panel()
    freshness = _make_freshness(weights)
    cfg = {
        "dri_components": [
            {"id": "food_at_home", "weight": 0.6, "source": "bls", "series_id": "SID_FOOD"},
            {"id": "gas", "weight": 0.4, "source": "eia", "series_id": "SID_GAS"},
        ]
    }
    (tmp_path / "data" / "published").mkdir(parents=True)
    publish_dri_metadata(freshness, weights, cfg)

    result = pd.read_csv(tmp_path / "data" / "published" / "dri_metadata.csv")
    expected_cols = [
        "component_id", "series_id", "cadence", "data_as_of",
        "age_days", "status", "carried_forward", "in_index", "weight",
    ]
    assert list(result.columns) == expected_cols


def test_dri_metadata_row_count(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _, weights = _make_panel()
    freshness = _make_freshness(weights)
    cfg = {
        "dri_components": [
            {"id": "food_at_home", "weight": 0.6, "source": "bls"},
            {"id": "gas", "weight": 0.4, "source": "eia"},
        ]
    }
    (tmp_path / "data" / "published").mkdir(parents=True)
    publish_dri_metadata(freshness, weights, cfg)

    result = pd.read_csv(tmp_path / "data" / "published" / "dri_metadata.csv")
    assert len(result) == 2


def test_dri_metadata_deferred_appears_as_placeholder(tmp_path, monkeypatch):
    """Deferred components appear in metadata with status='deferred' and in_index=False."""
    monkeypatch.chdir(tmp_path)
    _, weights = _make_panel()
    freshness = _make_freshness(weights)
    cfg = {
        "dri_components": [
            {"id": "food_at_home", "weight": 0.6, "source": "bls"},
            {"id": "gas", "weight": 0.4, "source": "eia"},
            {"id": "rent", "weight": 0.18, "source": "zillow", "deferred": True},
        ]
    }
    (tmp_path / "data" / "published").mkdir(parents=True)
    publish_dri_metadata(freshness, weights, cfg)

    result = pd.read_csv(tmp_path / "data" / "published" / "dri_metadata.csv")
    assert len(result) == 3
    rent_row = result[result["component_id"] == "rent"]
    assert len(rent_row) == 1
    assert rent_row["status"].iloc[0] == "deferred"
    assert rent_row["in_index"].iloc[0] == False


def test_dri_metadata_includes_deferred_quarterly_reserve(tmp_path, monkeypatch):
    """Deferred quarterly_reserve must appear in metadata with status='deferred' and in_index=False."""
    monkeypatch.chdir(tmp_path)
    weights = pd.Series({"food_at_home": 1.0})
    freshness = _make_freshness(weights)
    cfg = {
        "dri_components": [
            {"id": "food_at_home", "weight": 0.13, "source": "bls", "series_id": "SID_FOOD"},
            {
                "id": "quarterly_reserve", "weight": 0.10, "source": "manual",
                "cadence": "quarterly", "deferred": True,
            },
        ]
    }
    (tmp_path / "data" / "published").mkdir(parents=True)
    publish_dri_metadata(freshness, weights, cfg)

    result = pd.read_csv(tmp_path / "data" / "published" / "dri_metadata.csv")
    qr_row = result[result["component_id"] == "quarterly_reserve"]
    assert len(qr_row) == 1
    assert qr_row["status"].iloc[0] == "deferred"
    assert qr_row["in_index"].iloc[0] == False
    assert qr_row["weight"].iloc[0] == 0.0


def test_dri_component_table_11_rows_with_rent_and_cc(tmp_path, monkeypatch):
    """Component table should have 11 rows including rent and cc_interest."""
    monkeypatch.chdir(tmp_path)
    dates = pd.date_range("2020-01-01", periods=24, freq="MS")
    n = len(dates)

    component_ids = [
        "rent", "mortgage_payment", "food_at_home", "gas", "auto_insurance",
        "cc_interest", "dining_out", "utilities", "used_cars", "eggs", "home_insurance",
    ]
    panel_data = {"date": dates, "dri": [100.0 + i * 0.5 for i in range(n)], "cpi": [100.0] * n}
    for cid in component_ids:
        panel_data[cid] = [100.0 + i * 0.3 for i in range(n)]
    panel = pd.DataFrame(panel_data)

    # Equal weights for simplicity
    weights = pd.Series({cid: 1.0 / len(component_ids) for cid in component_ids})

    (tmp_path / "data" / "published").mkdir(parents=True)
    publish_dri_component_table(panel, weights)

    result = pd.read_csv(tmp_path / "data" / "published" / "dri_component_table.csv")
    assert len(result) == 11
    assert "Rent" in result["Component"].values
    assert "Credit Card Interest" in result["Component"].values


def test_dri_metadata_excluded_from_index_in_index_false(tmp_path, monkeypatch):
    """excluded_from_index components should appear with in_index=False and weight=0."""
    monkeypatch.chdir(tmp_path)
    weights = pd.Series({"food_at_home": 1.0})
    freshness = _make_freshness(weights)
    # Add cc_interest as excluded_from_index
    freshness["cc_interest"] = FreshnessReport(
        series_id="TERMCBCCALLNS",
        component_id="cc_interest",
        cadence="quarterly",
        latest_observation=pd.Timestamp("2026-01-01"),
        age_days=120,
        status="stale_ok",
        carried_forward=True,
        expected_lag_days=95,
        hard_fail_days=200,
    )
    cfg = {
        "dri_components": [
            {"id": "food_at_home", "weight": 0.6, "source": "bls"},
            {"id": "cc_interest", "weight": 0.06, "source": "fred",
             "excluded_from_index": True},
        ]
    }
    (tmp_path / "data" / "published").mkdir(parents=True)
    publish_dri_metadata(freshness, weights, cfg)

    result = pd.read_csv(tmp_path / "data" / "published" / "dri_metadata.csv")
    cc_row = result[result["component_id"] == "cc_interest"]
    assert len(cc_row) == 1
    assert cc_row["in_index"].iloc[0] == False
    assert cc_row["weight"].iloc[0] == 0.0
