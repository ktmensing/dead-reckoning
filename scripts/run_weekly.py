"""
Weekly pipeline entry point.

Orchestrates: load config → fetch → validate → transform → persist → publish.
One BLS batch call, one FRED call per series, one EIA call for gas.
Exits non-zero on any failure with a clear message.

Run with: python scripts/run_weekly.py
Or via:   make all
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.fetch import bls, eia
from src.fetch import fred as fred_module
from src.publish.datawrapper_csv import (
    publish_dri_components,
    publish_dri_component_table,
    publish_dri_vs_cpi,
)
from src.store import save_derived
from src.transform.dri import build_dri
from src.validate import ValidationError, validate_series

import yaml


def _load_config() -> dict:
    with open("config/series.yaml") as f:
        return yaml.safe_load(f)


def _die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    cfg = _load_config()
    components = cfg["dri_components"]
    cpi_cfg = cfg["cpi_headline"]

    # --- Collect series IDs by source ---
    bls_ids = []
    fred_ids = []
    eia_ids = []

    for comp in components:
        if comp.get("deferred"):
            continue
        src = comp["source"]
        if src == "bls":
            bls_ids.append(comp["series_id"])
        elif src == "fred" and "series_id" in comp:
            fred_ids.append(comp["series_id"])
        elif src == "eia":
            eia_ids.append(comp["series_id"])
        elif src == "derived":
            # Pull the constituent FRED series
            for inp in comp.get("inputs", []):
                if inp["fetcher"] == "fred":
                    fred_ids.append(inp["series_id"])

    # Always fetch headline CPI
    bls_ids.append(cpi_cfg["series_id"])

    # Deduplicate while preserving order
    bls_ids = list(dict.fromkeys(bls_ids))
    fred_ids = list(dict.fromkeys(fred_ids))

    timeseries: dict = {}

    # --- 1. Fetch BLS batch ---
    print(f"Fetching {len(bls_ids)} BLS series in one batch call...")
    try:
        bls_results = bls.fetch_batch(bls_ids, start_year=2019)
    except Exception as exc:
        _die(f"BLS batch fetch failed: {exc}")

    # Map BLS results back to component IDs
    series_id_to_comp: dict = {}
    for comp in components:
        if comp.get("deferred") or comp["source"] != "bls":
            continue
        series_id_to_comp[comp["series_id"]] = comp["id"]

    for sid, df in bls_results.items():
        comp_id = series_id_to_comp.get(sid, sid)
        # cpi_headline is special
        if sid == cpi_cfg["series_id"]:
            timeseries["cpi_headline"] = df
        else:
            timeseries[comp_id] = df

    # --- 2. Fetch FRED series ---
    print(f"Fetching {len(fred_ids)} FRED series...")
    for sid in fred_ids:
        try:
            df = fred_module.fetch(sid)
        except Exception as exc:
            _die(f"FRED fetch failed for {sid}: {exc}")
        timeseries[sid] = df

    # --- 3. Fetch EIA series ---
    print(f"Fetching {len(eia_ids)} EIA series...")
    for sid in eia_ids:
        try:
            df = eia.fetch(sid)
        except Exception as exc:
            _die(f"EIA fetch failed for {sid}: {exc}")
        # Map EIA series to component id
        for comp in components:
            if comp.get("series_id") == sid:
                timeseries[comp["id"]] = df

    print(f"Fetched {len(timeseries)} total series.")

    # --- 4. Validate each series ---
    print("Validating series...")
    failed_validation = []
    for name, df in timeseries.items():
        # Use the actual FRED/BLS series ID for range checks
        # For component IDs, look up the series_id from config
        series_id_for_check = name
        for comp in components:
            if comp["id"] == name and "series_id" in comp:
                series_id_for_check = comp["series_id"]
                break
        if name == "cpi_headline":
            series_id_for_check = cpi_cfg["series_id"]

        try:
            validate_series(df, series_id_for_check)
        except ValidationError as exc:
            failed_validation.append(str(exc))

    if failed_validation:
        for msg in failed_validation:
            print(f"  VALIDATION FAIL: {msg}", file=sys.stderr)
        _die(f"{len(failed_validation)} series failed validation")

    print("  All series passed validation.")

    # --- 5. Transform ---
    print("Building DRI panel...")
    try:
        panel, weights = build_dri(timeseries)
    except Exception as exc:
        _die(f"Transform failed: {exc}")

    print(f"  Panel: {len(panel)} monthly rows, {panel.shape[1]} columns.")
    print(f"  DRI range: {panel['dri'].min():.2f} – {panel['dri'].max():.2f}")

    # --- 6. Persist derived ---
    print("Persisting derived data...")
    try:
        path = save_derived("dri_panel", panel)
        print(f"  Wrote {path}")
    except Exception as exc:
        _die(f"Failed to persist derived data: {exc}")

    # --- 7. Publish ---
    print("Publishing Datawrapper CSVs...")
    try:
        publish_dri_vs_cpi(panel)
        print("  data/published/dri_vs_cpi.csv")
        publish_dri_components(panel, weights)
        print("  data/published/dri_components.csv")
        publish_dri_component_table(panel, weights)
        print("  data/published/dri_component_table.csv")
    except Exception as exc:
        _die(f"Publish step failed: {exc}")

    print("Done.")


if __name__ == "__main__":
    main()
