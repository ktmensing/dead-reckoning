"""Tests for src/store.py — roundtrip save/load."""

import pandas as pd
import pytest

from src.store import load_derived, save_derived, save_published


def _make_df() -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.date_range("2020-01-01", periods=6, freq="MS"),
        "dri": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0],
        "cpi": [100.0, 100.5, 101.0, 101.5, 102.0, 102.5],
    })


def test_roundtrip_derived(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    df = _make_df()
    save_derived("test_panel", df)

    result = load_derived("test_panel")
    assert len(result) == len(df)
    assert list(result.columns) == list(df.columns)
    assert result["dri"].tolist() == df["dri"].tolist()


def test_save_derived_returns_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    df = _make_df()
    path = save_derived("test_panel", df)
    assert path.exists()
    assert path.name == "test_panel.csv"


def test_save_published_creates_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    df = _make_df()
    path = save_published("test_output", df)
    assert path.exists()
    assert path.name == "test_output.csv"


def test_load_derived_raises_if_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError):
        load_derived("nonexistent")
