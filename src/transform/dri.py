"""
DRI Price Layer transform.

Takes a dict of fetched DataFrames (one per component) and returns a panel
DataFrame with the DRI composite, comparison CPI, and per-component values,
all rebased to Jan 2020 = 100.

Weight normalization: the YAML sums to 0.90 (the remaining 0.10 is reserved
for quarterly inputs not yet wired up). Additionally, "rent" is deferred until
the Zillow fetcher is built. The transform normalizes weights over whatever
components are actually present in the input dict, so the composite is always
meaningful even when the full component set isn't complete. This is documented
behavior, not a silent workaround.

Mortgage payment derivation: MSPUS (quarterly) and MORTGAGE30US (weekly) are
passed as raw fetched series under those FRED series IDs. The transform aligns
them to monthly, computes the monthly P&I payment using standard amortization,
and exposes the result as the "mortgage_payment" component.

Amortization check: $400k home, 7% rate, 20% down →
  P = 320,000, r = 0.07/12 ≈ 0.005833, n = 360
  payment = 320000 * 0.005833 / (1 - 1.005833^-360) ≈ $2,129  (matches expected ~$2,128)

Panel date range
----------------
Start: the first month where ALL non-deferred components have data. This is
naturally determined by the most restrictive series (e.g., cc_interest starts
later than CPI series). Using max of first-valid-dates ensures the early DRI
isn't computed from partial component coverage.

End: the last month where ALL BLS-sourced components have data (BLS monthly
releases define the current state of the index). Quarterly/weekly components
outside BLS (gas, cc_interest, mortgage_payment) are forward-filled up to 4
months within this cutoff — enough to bridge one inter-quarter gap.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import yaml

_CONFIG_PATH = Path("config/series.yaml")
_BASE_DATE = "2020-01-01"
# Max months to forward-fill quarterly series (covers one quarter + one month buffer)
_FFILL_LIMIT = 4


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
        # For quarterly series, forward-fill fills inter-quarter months within
        # the series' own date range (does not project beyond the last data point).
        return s.resample("MS").last().ffill()


def _compute_mortgage_payment(
    mspus: pd.Series,   # Median home price, monthly index
    rate: pd.Series,    # 30yr fixed rate in percent, monthly index
) -> pd.Series:
    """Monthly P&I for a 30-yr fixed mortgage on the median-priced home at 20% down."""
    common_idx = mspus.index.intersection(rate.index)
    price = mspus.reindex(common_idx)
    r = rate.reindex(common_idx) / 100.0 / 12.0  # percent → decimal → monthly

    principal = price * 0.80  # 20% down

    # Standard amortization: PMT = P * r / (1 - (1+r)^-n)
    # Guard against r=0 to avoid division by zero
    n = 360
    payment = np.where(
        r > 0,
        principal * r / (1 - (1 + r) ** (-n)),
        principal / n,
    )
    return pd.Series(payment, index=common_idx, name="mortgage_payment")


def build_dri(
    timeseries: dict,
    config_path: Path = _CONFIG_PATH,
) -> tuple:
    """Build the DRI Price Layer.

    Parameters
    ----------
    timeseries : dict
        Keys are component IDs from config (e.g. "food_at_home", "gas") plus
        special keys "MSPUS", "MORTGAGE30US", and "cpi_headline". Values are
        DataFrames with columns [date, value, ...].

    Returns
    -------
    panel : pd.DataFrame
        Columns: [date, dri, cpi, <component_id>, ...] where each component
        column is the rebased value (Jan 2020 = 100). DRI and CPI are also
        rebased to Jan 2020 = 100.
    weights : pd.Series
        Normalized weights for the components included in this run.
    """
    cfg = _load_config(config_path)
    components = cfg["dri_components"]

    # --- Step 1: resample each component to monthly ---
    monthly: dict = {}

    for comp in components:
        cid = comp["id"]

        if comp.get("deferred"):
            continue
        if comp["source"] == "derived":
            continue  # Handled separately below

        if cid not in timeseries:
            continue  # Missing series — will be excluded from composite

        method = comp.get("resample_method", "last")
        monthly[cid] = _to_monthly(timeseries[cid], method)

    # --- Step 2: derive mortgage_payment ---
    if "MSPUS" in timeseries and "MORTGAGE30US" in timeseries:
        mspus_m = _to_monthly(timeseries["MSPUS"], "last")
        rate_m = _to_monthly(timeseries["MORTGAGE30US"], "mean")
        mortgage_m = _compute_mortgage_payment(mspus_m, rate_m)
        monthly["mortgage_payment"] = mortgage_m

    # --- Step 3: build panel aligned on union of all monthly indices ---
    panel_df = pd.DataFrame(monthly)
    panel_df.index.name = "date"

    # --- Step 4: determine valid date range ---
    # Start: first month where all components have data (excludes partial-coverage
    # early history where the DRI would reflect only a subset of components).
    first_valids = [s.first_valid_index() for s in monthly.values() if s.first_valid_index() is not None]
    if first_valids:
        panel_start = max(first_valids)
        panel_df = panel_df.loc[panel_start:]

    # End: last month where all BLS-sourced components have data. BLS monthly
    # releases define the "current" state; don't show DRI for months that BLS
    # hasn't published yet, even if EIA/FRED have more recent data.
    bls_comp_ids = [
        comp["id"] for comp in components
        if not comp.get("deferred")
        and comp["source"] == "bls"
        and comp["id"] in panel_df.columns
    ]
    if bls_comp_ids:
        bls_cutoff = panel_df[bls_comp_ids].dropna(how="any").index.max()
        if bls_cutoff is not None:
            panel_df = panel_df.loc[:bls_cutoff]

    # Forward-fill quarterly/sparse series within the valid range.
    # Limit of 4 months bridges one full inter-quarter gap; mortgage_payment
    # and cc_interest are the primary beneficiaries.
    panel_df = panel_df.ffill(limit=_FFILL_LIMIT)

    # --- Step 5: rebase each component to Jan 2020 = 100 ---
    base_date = pd.Timestamp(_BASE_DATE)
    if base_date not in panel_df.index:
        candidates = panel_df.index[panel_df.index >= base_date]
        if len(candidates) == 0:
            raise ValueError("No data at or after Jan 2020 — cannot rebase")
        base_date = candidates[0]

    base_values = panel_df.loc[base_date]
    rebased = (panel_df / base_values) * 100.0

    # --- Step 6: normalize weights over components present ---
    raw_weights = {}
    for comp in components:
        cid = comp["id"]
        if comp.get("deferred") or cid not in rebased.columns:
            continue
        raw_weights[cid] = comp["weight"]

    if not raw_weights:
        raise ValueError("No components present after filtering deferred/missing")

    weight_series = pd.Series(raw_weights)
    normalized_weights = weight_series / weight_series.sum()

    # --- Step 7: compute DRI composite ---
    comp_cols = list(normalized_weights.index)
    dri = (rebased[comp_cols] * normalized_weights).sum(axis=1)

    # --- Step 8: rebase CPI for comparison ---
    cpi_raw = _to_monthly(timeseries["cpi_headline"], "last")
    # CPI cutoff: same BLS cutoff as other series
    if bls_comp_ids and bls_cutoff is not None:
        cpi_raw = cpi_raw.loc[:bls_cutoff]
    cpi_base = cpi_raw.get(base_date) if base_date in cpi_raw.index else cpi_raw.iloc[0]
    cpi_rebased = (cpi_raw / cpi_base) * 100.0

    # --- Step 9: assemble output ---
    out = rebased[comp_cols].copy()
    out["dri"] = dri
    out["cpi"] = cpi_rebased
    out = out.dropna(subset=["dri"]).reset_index()

    return out, normalized_weights
