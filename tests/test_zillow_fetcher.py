"""Tests for src/fetch/zillow.py — fixture-based, no live HTTP calls."""

import textwrap

import pandas as pd
import pytest

from src.fetch import FetchError
from src.fetch.zillow import _parse_zillow


_FIXTURE_CSV = textwrap.dedent("""\
    RegionID,SizeRank,RegionName,RegionType,StateName,2020-01-31,2020-02-29,2020-03-31,2020-04-30
    102001,0,United States,country,,1640.32,1645.00,1650.12,1655.50
    59,1,New York,msa,NY,2100.00,2110.00,,2120.00
    99,2,Los Angeles,msa,CA,2500.00,2510.00,2520.00,2530.00
""")

_DATASET = "zori_sfrcondomfr_sm_sa"


def test_parse_returns_national_row():
    df = _parse_zillow(_FIXTURE_CSV, _DATASET, "United States")
    assert len(df) == 4
    assert (df["series_id"] == _DATASET).all()
    assert (df["source"] == "zillow").all()


def test_parse_canonical_columns():
    df = _parse_zillow(_FIXTURE_CSV, _DATASET, "United States")
    for col in ["date", "value", "series_id", "source", "fetched_at"]:
        assert col in df.columns, f"Missing column: {col}"


def test_parse_correct_values():
    df = _parse_zillow(_FIXTURE_CSV, _DATASET, "United States").set_index("date")
    assert abs(df.loc[pd.Timestamp("2020-01-31"), "value"] - 1640.32) < 0.01
    assert abs(df.loc[pd.Timestamp("2020-04-30"), "value"] - 1655.50) < 0.01


def test_parse_sorted_ascending():
    df = _parse_zillow(_FIXTURE_CSV, _DATASET, "United States")
    dates = df["date"].tolist()
    assert dates == sorted(dates)


def test_parse_skips_null_date_values():
    # New York row has a missing value for 2020-03-31 — but we're filtering to US so irrelevant.
    # Verify that null values in the national row are skipped.
    csv_with_null = textwrap.dedent("""\
        RegionID,SizeRank,RegionName,RegionType,StateName,2020-01-31,2020-02-29,2020-03-31
        102001,0,United States,country,,1640.32,,1650.12
    """)
    df = _parse_zillow(csv_with_null, _DATASET, "United States")
    assert len(df) == 2
    dates = df["date"].dt.strftime("%Y-%m-%d").tolist()
    assert "2020-02-29" not in dates


def test_parse_filters_by_region_type_and_name():
    # Only 'country' + 'United States' should match — not MSA rows.
    df = _parse_zillow(_FIXTURE_CSV, _DATASET, "United States")
    # If MSA rows were included, values would differ; spot-check first value
    assert abs(df.iloc[0]["value"] - 1640.32) < 0.01


def test_parse_nonexistent_region_raises_fetch_error():
    with pytest.raises(FetchError, match="no row"):
        _parse_zillow(_FIXTURE_CSV, _DATASET, "Atlantis")


def test_parse_nonexistent_region_type_raises_fetch_error():
    # Even if name matches, wrong RegionType should still fail.
    csv_wrong_type = textwrap.dedent("""\
        RegionID,SizeRank,RegionName,RegionType,StateName,2020-01-31
        102001,0,United States,metro,,1640.32
    """)
    with pytest.raises(FetchError, match="no row"):
        _parse_zillow(csv_wrong_type, _DATASET, "United States")
