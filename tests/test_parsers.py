"""
Parser unit tests — fixture JSON, no network calls.

Each fetcher's _parse_* function is tested against representative API
response payloads. These tests are the fastest signal that an API response
shape change has broken the parse layer.
"""

import pandas as pd
import pytest

from src.fetch.fred import _parse_fred
from src.fetch.bls import _parse_bls_series
from src.fetch.bea import _parse_bea
from src.fetch.eia import _parse_eia


# ---------------------------------------------------------------------------
# FRED
# ---------------------------------------------------------------------------

_FRED_RESPONSE = {
    "observations": [
        {"date": "2020-01-02", "value": "3.51"},
        {"date": "2020-01-09", "value": "3.49"},
        {"date": "2020-01-16", "value": "."},   # Missing — should be dropped
        {"date": "2020-01-23", "value": "3.52"},
    ]
}


def test_fred_parse_drops_missing():
    df = _parse_fred(_FRED_RESPONSE, "MORTGAGE30US")
    assert len(df) == 3  # The "." row is dropped


def test_fred_parse_schema():
    df = _parse_fred(_FRED_RESPONSE, "MORTGAGE30US")
    assert list(df.columns) == ["date", "value", "series_id", "source", "fetched_at"]


def test_fred_parse_dates_are_timestamps():
    df = _parse_fred(_FRED_RESPONSE, "MORTGAGE30US")
    assert pd.api.types.is_datetime64_any_dtype(df["date"])


def test_fred_parse_sorted_ascending():
    df = _parse_fred(_FRED_RESPONSE, "MORTGAGE30US")
    assert df["date"].is_monotonic_increasing


def test_fred_parse_values_are_float():
    df = _parse_fred(_FRED_RESPONSE, "MORTGAGE30US")
    assert df["value"].dtype == float


# ---------------------------------------------------------------------------
# BLS
# ---------------------------------------------------------------------------

_BLS_SERIES_DATA = {
    "seriesID": "CUSR0000SAF11",
    "data": [
        {"year": "2020", "period": "M01", "periodName": "January", "value": "278.534"},
        {"year": "2020", "period": "M02", "periodName": "February", "value": "279.012"},
        {"year": "2020", "period": "M13", "periodName": "Annual", "value": "278.800"},  # Skip
        {"year": "2020", "period": "M03", "periodName": "March", "value": "277.500"},
    ]
}


def test_bls_parse_skips_m13():
    df = _parse_bls_series(_BLS_SERIES_DATA)
    assert len(df) == 3  # M13 dropped


def test_bls_parse_schema():
    df = _parse_bls_series(_BLS_SERIES_DATA)
    assert list(df.columns) == ["date", "value", "series_id", "source", "fetched_at"]


def test_bls_parse_dates_are_month_start():
    df = _parse_bls_series(_BLS_SERIES_DATA)
    assert all(d.day == 1 for d in df["date"])


def test_bls_parse_sorted_ascending():
    df = _parse_bls_series(_BLS_SERIES_DATA)
    assert df["date"].is_monotonic_increasing


# ---------------------------------------------------------------------------
# BEA
# ---------------------------------------------------------------------------

_BEA_RESPONSE = {
    "BEAAPI": {
        "Results": {
            "Data": [
                {"TimePeriod": "2020Q1", "DataValue": "19,032.7", "LineNumber": "1"},
                {"TimePeriod": "2020Q2", "DataValue": "17,302.4", "LineNumber": "1"},
                {"TimePeriod": "2020Q3", "DataValue": "19,025.1", "LineNumber": "1"},
            ]
        }
    }
}


def test_bea_parse_schema():
    df = _parse_bea(_BEA_RESPONSE, "NIPA:T10101:1:Q")
    assert list(df.columns) == ["date", "value", "series_id", "source", "fetched_at"]


def test_bea_parse_quarterly_dates():
    df = _parse_bea(_BEA_RESPONSE, "NIPA:T10101:1:Q")
    assert df["date"].iloc[0] == pd.Timestamp("2020-01-01")  # Q1 → January
    assert df["date"].iloc[1] == pd.Timestamp("2020-04-01")  # Q2 → April


def test_bea_parse_strips_commas_from_values():
    df = _parse_bea(_BEA_RESPONSE, "NIPA:T10101:1:Q")
    # 19,032.7 should parse to 19032.7 not raise
    assert abs(df["value"].iloc[0] - 19032.7) < 0.01


# ---------------------------------------------------------------------------
# EIA
# ---------------------------------------------------------------------------

_EIA_RESPONSE = {
    "response": {
        "data": [
            {"period": "2020-01-06", "value": "2.577"},
            {"period": "2020-01-13", "value": "2.601"},
            {"period": "2020-01-20", "value": "2.621"},
        ]
    }
}


def test_eia_parse_schema():
    df = _parse_eia(_EIA_RESPONSE, "PET.EMM_EPMR_PTE_NUS_DPG.W")
    assert list(df.columns) == ["date", "value", "series_id", "source", "fetched_at"]


def test_eia_parse_dates_are_timestamps():
    df = _parse_eia(_EIA_RESPONSE, "PET.EMM_EPMR_PTE_NUS_DPG.W")
    assert pd.api.types.is_datetime64_any_dtype(df["date"])


def test_eia_parse_sorted_ascending():
    df = _parse_eia(_EIA_RESPONSE, "PET.EMM_EPMR_PTE_NUS_DPG.W")
    assert df["date"].is_monotonic_increasing


def test_eia_parse_values_are_float():
    df = _parse_eia(_EIA_RESPONSE, "PET.EMM_EPMR_PTE_NUS_DPG.W")
    assert df["value"].dtype == float
