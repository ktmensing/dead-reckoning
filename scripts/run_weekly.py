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

import yaml

from src.fetch import bls, eia
from src.fetch import fred as fred_module
from src.fetch import zillow as zillow_module
from src.fetch.mercury import build_mercury
from src.publish.datawrapper_csv import (
    publish_dri_components,
    publish_dri_component_table,
    publish_dri_metadata,
    publish_dri_vs_cpi,
    publish_mercury,
)
from src.store import save_derived
from src.transform.dri import build_dri
from src.validate import ValidationError, assess_freshness


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
    mercury_comps = cfg.get("mercury_components", [])

    # --- Collect series IDs by source ---
    bls_ids = []
    fred_ids = []
    eia_ids = []

    zillow_components = []
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
            for inp in comp.get("inputs", []):
                if inp["fetcher"] == "fred":
                    fred_ids.append(inp["series_id"])
        elif src == "zillow":
            zillow_components.append(comp)

    bls_ids.append(cpi_cfg["series_id"])

    mercury_fred_ids = {comp["series_id"]: comp["id"] for comp in mercury_comps if comp["source"] == "fred"}
    fred_ids.extend(mercury_fred_ids.keys())

    bls_ids = list(dict.fromkeys(bls_ids))
    fred_ids = list(dict.fromkeys(fred_ids))

    timeseries: dict = {}

    # --- 1. Fetch BLS batch ---
    print(f"Fetching {len(bls_ids)} BLS series in one batch call...")
    try:
        bls_results = bls.fetch_batch(bls_ids, start_year=2019)
    except Exception as exc:
        _die(f"BLS batch fetch failed: {exc}")

    series_id_to_comp: dict = {}
    for comp in components:
        if comp.get("deferred") or comp["source"] != "bls":
            continue
        series_id_to_comp[comp["series_id"]] = comp["id"]

    for sid, df in bls_results.items():
        comp_id = series_id_to_comp.get(sid, sid)
        if sid == cpi_cfg["series_id"]:
            timeseries["cpi_headline"] = df
        else:
            timeseries[comp_id] = df

    # --- 2. Fetch FRED series ---
    # Derived inputs (MSPUS, MORTGAGE30US) stay keyed by series_id so the transform
    # can find them. Direct FRED components (cc_interest → TERMCBCCALLNS) are remapped
    # to their component_id for uniform validation downstream.
    derived_input_sids = {
        inp["series_id"]
        for comp in components
        if comp["source"] == "derived"
        for inp in comp.get("inputs", [])
        if inp.get("fetcher") == "fred"
    }
    fred_series_id_to_comp = {
        comp["series_id"]: comp["id"]
        for comp in components
        if not comp.get("deferred")
        and comp["source"] == "fred"
        and "series_id" in comp
    }
    print(f"Fetching {len(fred_ids)} FRED series...")
    for sid in fred_ids:
        try:
            df = fred_module.fetch(sid)
        except Exception as exc:
            _die(f"FRED fetch failed for {sid}: {exc}")
        if sid in derived_input_sids:
            timeseries[sid] = df          # e.g. MSPUS, MORTGAGE30US
        elif sid in fred_series_id_to_comp:
            timeseries[fred_series_id_to_comp[sid]] = df  # e.g. cc_interest
        elif sid in mercury_fred_ids:
            timeseries[mercury_fred_ids[sid]] = df        # e.g. umich_expectations, conference_board_confidence
        else:
            timeseries[sid] = df

    # --- 3. Fetch Zillow datasets ---
    if zillow_components:
        print(f"Fetching {len(zillow_components)} Zillow dataset(s)...")
        for comp in zillow_components:
            dataset = comp["zillow_dataset"]
            region = comp.get("region", "United States")
            try:
                df = zillow_module.fetch(dataset, region)
            except Exception as exc:
                _die(f"Zillow fetch failed for {dataset}: {exc}")
            timeseries[comp["id"]] = df

    # --- 4. Fetch EIA series ---
    print(f"Fetching {len(eia_ids)} EIA series...")
    for sid in eia_ids:
        try:
            df = eia.fetch(sid)
        except Exception as exc:
            _die(f"EIA fetch failed for {sid}: {exc}")
        for comp in components:
            if comp.get("series_id") == sid:
                timeseries[comp["id"]] = df

    print(f"Fetched {len(timeseries)} total series.")

    # --- 5. Validate each series ---
    print("Validating series...")
    freshness_reports: dict = {}

    # All component series are now keyed by component_id. Only raw derived inputs
    # (MSPUS, MORTGAGE30US) remain under their series_id — skip those here.
    comp_by_id: dict = {c["id"]: c for c in components if not c.get("deferred")}

    for name, df in timeseries.items():
        if name in comp_by_id:
            comp_cfg = comp_by_id[name]
        elif name == "cpi_headline":
            comp_cfg = cpi_cfg
        else:
            continue  # raw derived inputs (MSPUS, MORTGAGE30US) or unknown keys

        if "cadence" not in comp_cfg:
            continue

        try:
            report = assess_freshness(df, comp_cfg)
            freshness_reports[comp_cfg["id"]] = report
        except ValidationError as exc:
            _die(f"Validation failed: {exc}")

    print(f"  {len(freshness_reports)} series validated.")

    # --- 6. Transform ---
    print("Building DRI panel...")
    try:
        result = build_dri(timeseries, freshness_reports)
    except ValidationError as exc:
        _die(f"Transform validation failed: {exc}")
    except Exception as exc:
        _die(f"Transform failed: {exc}")

    panel = result.panel
    weights = result.weights
    data_as_of = result.data_as_of
    freshness_reports = result.freshness

    print(f"  Panel: {len(panel)} monthly rows, {panel.shape[1]} columns.")
    print(f"  DRI range: {panel['dri'].min():.2f} – {panel['dri'].max():.2f}")

    latest_dri = panel.sort_values("date")["dri"].iloc[-1]
    latest_date = panel.sort_values("date")["date"].iloc[-1]
    print(f"  Latest DRI: {latest_dri:.4f} ({latest_date.strftime('%Y-%m')})")

    # --- 7. Persist derived ---
    print("Persisting derived data...")
    try:
        path = save_derived("dri_panel", panel)
        print(f"  Wrote {path}")
    except Exception as exc:
        _die(f"Failed to persist derived data: {exc}")

    # --- 7b. Build and persist Mercury indicator ---
    print("Building Mercury indicator...")
    try:
        dri_series = panel.set_index("date")["dri"]
        umich_series = timeseries["umich_expectations"].set_index("date")["value"]
        mercury_df = build_mercury(dri_series, umich_series)
        path = save_derived("mercury", mercury_df)
        print(f"  Wrote {path} ({len(mercury_df)} rows)")
    except Exception as exc:
        _die(f"Mercury build failed: {exc}")

    # --- 8. Publish ---
    print("Publishing Datawrapper CSVs...")
    try:
        publish_dri_vs_cpi(panel)
        print("  data/published/dri_vs_cpi.csv")
        publish_dri_components(panel, weights)
        print("  data/published/dri_components.csv")
        publish_dri_component_table(panel, weights, data_as_of)
        print("  data/published/dri_component_table.csv")
        publish_dri_metadata(freshness_reports, weights, cfg)
        print("  data/published/dri_metadata.csv")
        publish_mercury(mercury_df)
        print("  data/published/mercury.csv")
    except Exception as exc:
        _die(f"Publish step failed: {exc}")

    print("Done.")


if __name__ == "__main__":
    main()
