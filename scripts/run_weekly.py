"""
Weekly pipeline entry point.

Orchestrates: load config → fetch → validate → transform → persist → publish.
One BLS batch call, one FRED call per series, one EIA call for gas.
Exits non-zero on any failure with a clear message.

Run with: python scripts/run_weekly.py
Or via:   make all
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import yaml

from src.fetch import FetchError, RAW_DIR, bls, eia
from src.fetch import fred as fred_module
from src.fetch import zillow as zillow_module
from src.fetch.mercury import build_mercury, calculate_partisan_distortion
from src.publish.datawrapper_csv import (
    publish_dri_components,
    publish_dri_component_table,
    publish_dri_metadata,
    publish_dri_vs_cpi,
    publish_mercury,
    publish_mercury_rolling,
    publish_mercury_metadata,
    publish_partisan_distortion,
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


def _fetch_fred(sid: str, retries: int = 2, retry_delay: float = 5.0) -> pd.DataFrame:
    """Fetch a FRED series, retrying on 5xx errors, then falling back to cache.

    Retries `retries` times with a short delay. On persistent failure, loads
    from data/raw/fred/{sid}.csv if it exists. Dies if neither succeeds.
    """
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            return fred_module.fetch(sid)
        except FetchError as exc:
            msg = str(exc)
            # 4xx errors won't recover with a retry — fail immediately
            if "bad request" in msg or "bad API key" in msg or "(401)" in msg or "(400)" in msg:
                _die(f"FRED fetch failed for {sid}: {exc}")
            last_exc = exc
            if attempt < retries:
                print(f"  FRED {sid}: transient error (attempt {attempt}/{retries}), retrying in {retry_delay}s...")
                time.sleep(retry_delay)

    # Retries exhausted — try cache
    cache = RAW_DIR / "fred" / f"{sid}.csv"
    if cache.exists():
        print(f"  WARNING: FRED {sid} fetch failed after {retries} attempts — using cached data from {cache}")
        df = pd.read_csv(cache, parse_dates=["date"])
        return df

    _die(f"FRED fetch failed for {sid} and no cache found: {last_exc}")


def main() -> None:
    cfg = _load_config()
    components = cfg["dri_components"]
    cpi_cfg = cfg["cpi_headline"]
    mercury_comps = cfg.get("mercury_components", [])
    mercury_caveats = cfg.get("mercury_caveats", [])

    # --- Collect series IDs by source ---
    bls_ids = []
    fred_ids = []
    eia_ids = []
    zillow_components = []

    manual_dri_components = []
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
        elif src == "manual":
            manual_dri_components.append(comp)

    bls_ids.append(cpi_cfg["series_id"])

    # Add backfill source series IDs (e.g. CUSR0000SEHA for rent pre-2015 splice)
    for comp in components:
        if comp.get("backfill_source") == "bls" and comp.get("backfill_series_id"):
            bls_ids.append(comp["backfill_series_id"])

    # "oecd" source uses FRED as the data provider (USACSCICP02STSAM is FRED-hosted).
    # mercury_caveats (e.g. MICH for partisan distortion) are fetched the same way.
    mercury_fred_ids = {
        comp["series_id"]: comp["id"]
        for comp in mercury_comps + mercury_caveats
        if comp.get("source") in ("fred", "oecd") and comp.get("id") and comp.get("series_id")
    }
    fred_ids.extend(mercury_fred_ids.keys())

    bls_ids = list(dict.fromkeys(bls_ids))
    fred_ids = list(dict.fromkeys(fred_ids))

    timeseries: dict = {}

    # --- 1. Fetch BLS batch ---
    print(f"Fetching {len(bls_ids)} BLS series in one batch call...")
    try:
        # start_year=1999 extends history to 2000-01 for all BLS series;
        # the fetcher pages through 20-year windows automatically.
        bls_results = bls.fetch_batch(bls_ids, start_year=1999)
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
        df = _fetch_fred(sid)
        if sid in derived_input_sids:
            timeseries[sid] = df                          # e.g. MSPUS, MORTGAGE30US
        elif sid in fred_series_id_to_comp:
            timeseries[fred_series_id_to_comp[sid]] = df  # e.g. cc_interest
        elif sid in mercury_fred_ids:
            timeseries[mercury_fred_ids[sid]] = df        # e.g. umich_expectations
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

    # --- 3b. Load manual CSVs for Mercury components ---
    mercury_manual = [c for c in mercury_comps if c["source"] == "manual" and c.get("id")]
    for comp in mercury_manual:
        path = Path(comp["manual_csv"])
        if not path.exists():
            print(f"  WARNING: manual CSV for {comp['id']} not found at {path} — skipping")
            continue
        df = pd.read_csv(path, parse_dates=["date"])
        if "value" not in df.columns or "date" not in df.columns:
            print(f"  WARNING: {path} missing required columns [date, value] — skipping")
            continue
        # Snap release-date timestamps to first-of-month so they align with
        # FRED-sourced series (YYYY-MM-01 convention). Dedupe in case two
        # releases fall in the same month; keep the later one.
        df["date"] = df["date"].dt.to_period("M").dt.to_timestamp()
        df = df.sort_values("date").drop_duplicates(subset=["date"], keep="last")
        df["series_id"] = comp["id"]
        df["source"] = "manual"
        df["fetched_at"] = pd.Timestamp.utcnow()
        timeseries[comp["id"]] = df.sort_values("date").reset_index(drop=True)

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

    # --- 4b. Load manual DRI CSVs ---
    if manual_dri_components:
        print(f"Loading {len(manual_dri_components)} manual DRI CSV(s)...")
        for comp in manual_dri_components:
            path = Path(comp["manual_csv"])
            if not path.exists():
                _die(f"Manual CSV for {comp['id']} not found at {path}. "
                     f"Populate the file or set deferred: true in series.yaml.")
            df = pd.read_csv(path, parse_dates=["date"])
            if "value" not in df.columns or "date" not in df.columns:
                _die(f"Manual CSV {path} missing required columns [date, value].")
            df = df.dropna(subset=["value"])  # drop blank trailing rows
            df["series_id"] = comp["id"]
            df["source"] = "manual"
            df["fetched_at"] = pd.Timestamp.utcnow()
            timeseries[comp["id"]] = df.sort_values("date").reset_index(drop=True)
            print(f"  Loaded {path} ({len(df)} rows, through {df['date'].max().strftime('%Y-%m')})")

    print(f"Fetched {len(timeseries)} total series.")

    # --- 5. Validate each series ---
    print("Validating series...")
    freshness_reports: dict = {}

    # All component series are keyed by component_id. Only raw derived inputs
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

    print(f"  {len(freshness_reports)} DRI series validated.")

    # Validate Mercury sentiment inputs separately (warning only — Mercury is supplementary)
    mercury_freshness: dict = {}
    mercury_comp_by_id = {c["id"]: c for c in mercury_comps}
    for cid, comp_cfg in mercury_comp_by_id.items():
        if cid not in timeseries or "cadence" not in comp_cfg:
            continue
        try:
            report = assess_freshness(timeseries[cid], comp_cfg)
            mercury_freshness[cid] = report
        except ValidationError as exc:
            print(f"  WARNING: Mercury input {cid} failed freshness check: {exc}", file=sys.stderr)

    print(f"  {len(mercury_freshness)} Mercury series validated.")

    # --- 6. Transform ---
    print("Building DRI panel...")
    try:
        result = build_dri(timeseries, freshness_reports)
    except ValidationError as exc:
        _die(f"Transform validation failed: {exc}")
    except Exception as exc:
        _die(f"Transform failed: {exc}")

    panel = result.panel
    dri_weights = result.weights
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

    # --- 8. Build and persist Mercury indicator ---
    print("Building Mercury indicator...")
    try:
        dri_series = panel.set_index("date")["dri"]

        # Include all mercury_comps present in timeseries (fred, oecd, manual).
        sentiment_sources = {
            comp["id"]: timeseries[comp["id"]].set_index("date")["value"]
            for comp in mercury_comps
            if comp.get("id") and comp["id"] in timeseries
        }

        if not sentiment_sources:
            _die("Mercury build failed: no sentiment sources found in fetched data")

        mercury_weights = {
            comp["id"]: comp.get("weight", 1.0 / len(mercury_comps))
            for comp in mercury_comps
            if comp["id"] in sentiment_sources
        }
        mercury_df = build_mercury(dri_series, sentiment_sources, mercury_weights)
        path = save_derived("mercury", mercury_df)
        print(f"  Wrote {path} ({len(mercury_df)} rows)")
    except Exception as exc:
        _die(f"Mercury build failed: {exc}")

    # --- 8b. Partisan distortion cross-check ---
    print("Calculating partisan distortion...")
    partisan_df = None
    try:
        mich_ts = timeseries.get("umich_expectations")
        if mich_ts is None:
            raise ValueError("umich_expectations (MICH) not in timeseries — check mercury_caveats fetch")
        mich_series = mich_ts.set_index("date")["value"]
        dri_for_partisan = panel.set_index("date")["dri"]
        partisan_df = calculate_partisan_distortion(mich_series, dri_for_partisan)
        path = save_derived("partisan_distortion", partisan_df)
        print(f"  Wrote {path} ({len(partisan_df)} rows, "
              f"{int((partisan_df['partisan_flag'] == 1).sum())} flagged months)")
    except Exception as exc:
        print(f"  Warning: partisan distortion calculation failed: {exc}")

    # --- 9. Publish ---
    print("Publishing Datawrapper CSVs...")
    try:
        publish_dri_vs_cpi(panel)
        print("  data/published/dri_vs_cpi.csv")
        publish_dri_components(panel, dri_weights)
        print("  data/published/dri_components.csv")
        publish_dri_component_table(panel, dri_weights, data_as_of)
        print("  data/published/dri_component_table.csv")
        publish_dri_metadata(freshness_reports, dri_weights, cfg)
        print("  data/published/dri_metadata.csv")
        publish_mercury(mercury_df)
        print("  data/published/mercury.csv")
        publish_mercury_rolling(mercury_df)
        print("  data/published/mercury_rolling.csv")
        publish_mercury_metadata(mercury_freshness, mercury_comps)
        print("  data/published/mercury_metadata.csv")
        if partisan_df is not None and not partisan_df.empty:
            publish_partisan_distortion(partisan_df)
            print("  data/published/partisan_distortion.csv")
    except Exception as exc:
        _die(f"Publish step failed: {exc}")

    print("Done.")


if __name__ == "__main__":
    main()
