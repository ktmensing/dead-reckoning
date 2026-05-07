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
    publish_dri_vs_cpi,
)
from src.store import PUBLISHED_DIR


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
    # Dates should be YYYY-MM-DD strings
    assert result["Date"].iloc[0] == "2020-01-01"


def test_dri_components_columns(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    panel, weights = _make_panel()
    (tmp_path / "data" / "published").mkdir(parents=True)
    publish_dri_components(panel, weights)

    result = pd.read_csv(tmp_path / "data" / "published" / "dri_components.csv")
    assert list(result.columns) == ["Date", "Component", "Value"]


def test_dri_components_is_long_format(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    panel, weights = _make_panel()
    (tmp_path / "data" / "published").mkdir(parents=True)
    publish_dri_components(panel, weights)

    result = pd.read_csv(tmp_path / "data" / "published" / "dri_components.csv")
    # 24 months × 2 components = 48 rows
    assert len(result) == 24 * 2


def test_dri_component_table_columns(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    panel, weights = _make_panel()
    (tmp_path / "data" / "published").mkdir(parents=True)
    publish_dri_component_table(panel, weights)

    result = pd.read_csv(tmp_path / "data" / "published" / "dri_component_table.csv")
    assert list(result.columns) == ["Component", "Latest", "MoM %", "YoY %", "Weight"]


def test_dri_component_table_row_count(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    panel, weights = _make_panel()
    (tmp_path / "data" / "published").mkdir(parents=True)
    publish_dri_component_table(panel, weights)

    result = pd.read_csv(tmp_path / "data" / "published" / "dri_component_table.csv")
    # One row per component (2 in fixture)
    assert len(result) == 2


def test_dri_component_table_yoy_requires_13_months(tmp_path, monkeypatch):
    """YoY should be None/NaN when panel has fewer than 13 rows."""
    monkeypatch.chdir(tmp_path)
    dates = pd.date_range("2020-01-01", periods=12, freq="MS")  # 12 only
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
