"""
DRI Price Layer transform.

Takes a dict of fetched DataFrames (one per component) and returns a DRIResult
containing the panel, normalized weights, per-component data_as_of dates, and
freshness reports.

Weight normalization: the YAML sums to 0.90 (the remaining 0.10 is reserved
for quarterly inputs not yet wired up). Additionally, "rent" is deferred and
"cc_interest" is excluded_from_index. The transform normalizes weights over
whatever components are actually present in the index, so the composite is
always meaningful even when the full component set isn't complete.

Carry-forward: quarterly/sparse series (mortgage_payment, cc_interest) hold
their last known value across inter-release gaps. The limit is cadence-based
so no component is projected further than one cadence period. data_as_of
always reflects the latest *real* observation, not the carried-forward date.

Mortgage payment derivation: MSPUS (quarterly) and MORTGAGE30US (weekly) are
passed as raw fetched series under those FRED series IDs. The transform aligns
them to monthly, computes the monthly P&I payment using standard amortization,
and exposes the result as the "mortgage_payment" component.

Amortization check: $400k home, 7% rate, 20% down →
  P = 320,000, r = 0.07/12 ≈ 0.005833, n = 360
  payment = 320000 * 0.005833 / (1 - 1.005833^-360) ≈ $2,129  (matches expected ~$2,128)

Panel date range
----------------
Start: the first month where all in-index, non-deferred components have data.
End: the last month where BLS-sourced in-index components have data (BLS monthly
releases control the current state of the index).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from src.validate import FreshnessReport, ValidationError, assess_freshness

log = logging.getLogger(__name__)

# Components whose normalized weight exceeds this threshold are considered
# "material". DRI is suppressed (set to NaN) for any row where a material
# component has no data after carry-forward, rather than silently treating
# it as zero and publishing a structurally wrong composite.
MATERIAL_WEIGHT_THRESHOLD = 0.05

_CONFIG_PATH = Path("config/series.yaml")
_BASE_DATE = "2020-01-01"
# Panel history extends back to this date when components have sufficient data.
# Components that only start after this date (e.g. quarterly_reserve) don't constrain
# the historical start; they simply don't contribute until their first observation.
_HISTORY_START = pd.Timestamp("2000-01-01")

# Months to forward-fill per cadence when carry_forward=true.
# Limits projection to one cadence period so stale data doesn't silently
# propagate across multiple release cycles.
FFILL_LIMIT_MONTHS: dict = {
    "weekly": 1,
    "monthly": 1,
    "quarterly": 3,
    "semiannual": 6,
    "annual": 12,
}


@dataclass
class DRIResult:
    panel: pd.DataFrame                      # date, dri, cpi, *components (rebased)
    weights: pd.Series                       # normalized weights for in-index components
    data_as_of: dict                         # {component_id: pd.Timestamp} — pre-ffill
    freshness: dict                          # {component_id: FreshnessReport}


def _load_config(config_path: Path = _CONFIG_PATH) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def _to_monthly(df: pd.DataFrame, resample_method: str = "last") -> pd.Series:
    """Resample a [date, value] DataFrame to month-start frequency."""
    s = df.set_index("date")["value"].sort_index()
    if resample_method == "mean":
        return s.resample("MS").mean()
    else:
        # "last": for monthly series already at month-start, this is a no-op.
        # Does not project beyond the last data point — ffill is applied
        # separately per component via carry_forward logic below.
        return s.resample("MS").last()


def _compute_mortgage_payment(
    mspus: pd.Series,
    rate: pd.Series,
) -> pd.Series:
    """Monthly P&I for a 30-yr fixed mortgage on the median-priced home at 20% down."""
    common_idx = mspus.index.intersection(rate.index)
    price = mspus.reindex(common_idx)
    r = rate.reindex(common_idx) / 100.0 / 12.0

    principal = price * 0.80
    n = 360
    payment = np.where(
        r > 0,
        principal * r / (1 - (1 + r) ** (-n)),
        principal / n,
    )
    return pd.Series(payment, index=common_idx, name="mortgage_payment")


def build_rent_with_backfill(zori: pd.Series, bls_rent: pd.Series) -> pd.Series:
    """Chain-link BLS CPI rent pre-ZORI to extend rent history back to 2000.

    Preserves ZORI's level and percent changes from ZORI's first observation
    onward.  Uses BLS CPI Rent of Primary Residence (CUSR0000SEHA) percent
    changes to extend history backward — same conceptual measure, different
    sampling methodology.  The splice point is ZORI's earliest observation.
    """
    zori_start = zori.index.min()

    # BLS region: all months up to (but not including) the ZORI splice point.
    bls_pre = bls_rent.loc[:zori_start].dropna()
    bls_pre = bls_pre.iloc[:-1]   # drop the overlap month (zori_start itself)
    bls_pct = bls_pre.pct_change()

    # Walk backward from ZORI's first value, applying BLS percent changes in reverse.
    # value[t-1] = value[t] / (1 + pct_change[t])
    extended: list = [zori.loc[zori_start]]
    dates: list = [zori_start]
    for date in reversed(bls_pct.index):
        prev_value = extended[-1] / (1 + bls_pct.loc[date])
        extended.append(prev_value)
        dates.append(date)

    pre = pd.Series(
        list(reversed(extended[1:])),
        index=list(reversed(dates[1:])),
    )
    return pd.concat([pre, zori]).sort_index()


def build_dri(
    timeseries: dict,
    freshness_reports: dict,
    config_path: Path = _CONFIG_PATH,
) -> DRIResult:
    """Build the DRI Price Layer.

    Parameters
    ----------
    timeseries : dict
        Keys are component IDs (e.g. "food_at_home") plus special keys
        "MSPUS", "MORTGAGE30US", and "cpi_headline". Values are DataFrames
        with columns [date, value, ...].
    freshness_reports : dict
        {component_id: FreshnessReport} for directly-fetched components,
        generated by run_weekly before calling this function. Derived
        components (mortgage_payment) have their report added here.

    Returns
    -------
    DRIResult with panel, weights, data_as_of, and freshness.
    """
    cfg = _load_config(config_path)
    components = cfg["dri_components"]

    # Build lookup: component_id → config entry
    comp_cfg: dict = {c["id"]: c for c in components}

    # --- Step 1: resample each directly-fetched component to monthly ---
    monthly: dict = {}

    for comp in components:
        cid = comp["id"]
        if comp.get("deferred"):
            continue
        if comp["source"] == "derived":
            continue
        if cid not in timeseries:
            continue
        method = comp.get("resample_method", "last")
        monthly[cid] = _to_monthly(timeseries[cid], method)

        # Apply rent backfill splice if configured (ZORI pre-2015 → BLS chain-link)
        if cid == "rent" and comp.get("backfill_series_id"):
            bsid = comp["backfill_series_id"]
            if bsid in timeseries:
                bls_rent_m = _to_monthly(timeseries[bsid], "last")
                monthly[cid] = build_rent_with_backfill(monthly[cid], bls_rent_m)
                log.info("rent: applied pre-2015 chain-link from %s", bsid)

    # --- Step 2: derive mortgage_payment ---
    if "MSPUS" in timeseries and "MORTGAGE30US" in timeseries:
        mspus_m = _to_monthly(timeseries["MSPUS"], "last")
        rate_m = _to_monthly(timeseries["MORTGAGE30US"], "mean")
        mortgage_m = _compute_mortgage_payment(mspus_m, rate_m)
        monthly["mortgage_payment"] = mortgage_m

    # --- Step 3: record data_as_of BEFORE carry-forward ---
    data_as_of: dict = {}
    for cid, s in monthly.items():
        last_valid = s.last_valid_index()
        if last_valid is not None:
            data_as_of[cid] = last_valid

    # --- Step 4: assess freshness for mortgage_payment (derived) ---
    if "mortgage_payment" in monthly and "mortgage_payment" not in freshness_reports:
        mp_df = monthly["mortgage_payment"].rename("value").reset_index()
        mp_df.columns = ["date", "value"]
        mp_cfg = comp_cfg.get("mortgage_payment", {})
        if "cadence" in mp_cfg:
            try:
                report = assess_freshness(mp_df, mp_cfg)
                freshness_reports = {**freshness_reports, "mortgage_payment": report}
            except ValidationError:
                raise

    # --- Step 5: build panel aligned on union of all monthly indices ---
    panel_df = pd.DataFrame(monthly)
    panel_df.index.name = "date"

    # --- Step 6: apply carry-forward per component ---
    # Must happen AFTER pd.DataFrame(monthly) so the full union index is available.
    # Two-pass fill: interpolate fills interior NaN gaps (e.g. BLS publication
    # interruptions); ffill handles trailing gaps (from last real obs to panel end).
    # limit=N prevents filling beyond one cadence period in either direction.
    for comp in components:
        cid = comp["id"]
        if cid not in panel_df.columns:
            continue
        cadence = comp.get("cadence")
        carry = comp.get("carry_forward", False)
        if carry and cadence in FFILL_LIMIT_MONTHS:
            limit = FFILL_LIMIT_MONTHS[cadence]
            last_real = panel_df[cid].last_valid_index()

            panel_df[cid] = (
                panel_df[cid]
                .interpolate(method="linear", limit=limit, limit_area="inside")
                .ffill(limit=limit)
            )

            # Warn about trailing fills (past last real observation).
            if last_real is not None:
                trailing = int(panel_df[cid].loc[last_real:].iloc[1:].notna().sum())
                if trailing > 0:
                    log.warning(
                        "%s: carry-forward filled %d month(s) past last real obs (%s)",
                        cid, trailing, last_real.strftime("%Y-%m"),
                    )

            # Warn about NaN that remain within the component's active range
            # (from first real observation onward). Pre-history NaN (before the
            # component starts) are expected and removed by the date-range clip.
            first_valid = panel_df[cid].first_valid_index()
            if first_valid is not None:
                remaining = int(panel_df[cid].loc[first_valid:].isna().sum())
                if remaining > 0:
                    log.warning(
                        "%s: %d NaN(s) remain within active range after fill "
                        "— gap exceeded carry_forward limit (%d months)",
                        cid, remaining, limit,
                    )

    # --- Step 7: determine valid date range ---
    # Start: earliest month where all "early" in-index components have data.
    # "Early" means first data predates _HISTORY_START — late-starting components
    # (e.g. quarterly_reserve, which only begins in 2020) don't constrain
    # how far back the panel extends. They simply contribute NaN (which becomes
    # zero-weighted via the suppression check in step 10) until their first obs.
    in_index_ids = [
        c["id"] for c in components
        if not c.get("deferred")
        and not c.get("excluded_from_index")
        and c["source"] != "derived"
        and c["id"] in panel_df.columns
    ]
    all_first_valids = {
        cid: panel_df[cid].first_valid_index()
        for cid in in_index_ids
        if panel_df[cid].first_valid_index() is not None
    }
    # Also mortgage_payment
    if "mortgage_payment" in panel_df.columns:
        mp_start = panel_df["mortgage_payment"].first_valid_index()
        if mp_start is not None:
            all_first_valids["mortgage_payment"] = mp_start

    # Separate components that reach back to the history start from late starters.
    early_starts = [v for v in all_first_valids.values() if v <= _HISTORY_START]
    if early_starts:
        # Start where all early-history components first have data.
        panel_df = panel_df.loc[max(early_starts):]
    elif all_first_valids:
        # No component reaches the history start — fall back to max as before.
        panel_df = panel_df.loc[max(all_first_valids.values()):]

    # End: last month where BLS in-index components have data.
    bls_in_index = [
        c["id"] for c in components
        if not c.get("deferred")
        and not c.get("excluded_from_index")
        and c["source"] == "bls"
        and c["id"] in panel_df.columns
    ]
    bls_cutoff = None
    if bls_in_index:
        bls_cutoff = panel_df[bls_in_index].dropna(how="any").index.max()
        if bls_cutoff is not None:
            panel_df = panel_df.loc[:bls_cutoff]

    # --- Step 8: rebase each component to Jan 2020 = 100 ---
    base_date = pd.Timestamp(_BASE_DATE)
    if base_date not in panel_df.index:
        candidates = panel_df.index[panel_df.index >= base_date]
        if len(candidates) == 0:
            raise ValueError("No data at or after Jan 2020 — cannot rebase")
        base_date = candidates[0]

    base_values = panel_df.loc[base_date]
    rebased = (panel_df / base_values) * 100.0

    # --- Step 9: normalize weights over in-index components present ---
    raw_weights = {}
    for comp in components:
        cid = comp["id"]
        if comp.get("deferred") or comp.get("excluded_from_index"):
            continue
        if cid not in rebased.columns:
            continue
        raw_weights[cid] = comp["weight"]

    if not raw_weights:
        raise ValueError("No in-index components present after filtering")

    weight_series = pd.Series(raw_weights)
    normalized_weights = weight_series / weight_series.sum()

    # --- Step 10: compute DRI composite (in-index components only) ---
    comp_cols = list(normalized_weights.index)

    # Identify rows where a material-weight component is missing AND has already
    # started (i.e., its first real observation is on or before this date).
    # Components that haven't started yet (e.g. quarterly_reserve before 2020-01)
    # are expected to be NaN and must not suppress the historical DRI.
    material_cols = [c for c in comp_cols if normalized_weights[c] > MATERIAL_WEIGHT_THRESHOLD]
    first_valid_per_comp = {c: rebased[c].first_valid_index() for c in material_cols}

    suppress_mask = pd.Series(False, index=rebased.index)
    for dt in rebased.index:
        real_absent = [
            c for c in material_cols
            if pd.isna(rebased.loc[dt, c])
            and first_valid_per_comp[c] is not None
            and dt >= first_valid_per_comp[c]
        ]
        if real_absent:
            missing_pct = normalized_weights[real_absent].sum() * 100
            log.warning(
                "%s: DRI suppressed — %s missing (%.0f%% of index weight)",
                pd.Timestamp(dt).strftime("%Y-%m"), real_absent, missing_pct,
            )
            suppress_mask[dt] = True

    dri = (rebased[comp_cols] * normalized_weights).sum(axis=1)
    dri[suppress_mask] = float("nan")

    # --- Step 11: rebase CPI for comparison ---
    cpi_raw = _to_monthly(timeseries["cpi_headline"], "last")
    cpi_cfg = cfg.get("cpi_headline", {})
    if cpi_cfg.get("carry_forward") and cpi_cfg.get("cadence") in FFILL_LIMIT_MONTHS:
        cpi_raw = cpi_raw.ffill(limit=FFILL_LIMIT_MONTHS[cpi_cfg["cadence"]])
    if bls_cutoff is not None:
        cpi_raw = cpi_raw.loc[:bls_cutoff]
    cpi_base = cpi_raw.get(base_date) if base_date in cpi_raw.index else cpi_raw.iloc[0]
    cpi_rebased = (cpi_raw / cpi_base) * 100.0

    # --- Step 12: assemble output panel (all components, including excluded) ---
    all_comp_cols = [c for c in panel_df.columns if c in rebased.columns]
    out = rebased[all_comp_cols].copy()
    out["dri"] = dri
    out["cpi"] = cpi_rebased
    out = out.dropna(subset=["dri"]).reset_index()

    return DRIResult(
        panel=out,
        weights=normalized_weights,
        data_as_of=data_as_of,
        freshness=freshness_reports,
    )
