"""
Datawrapper-shaped CSV writers.

Each function writes one CSV to data/published/. Column headers in these CSVs
become legend labels in Datawrapper, so renames here break chart templates.
Do not change column names without also updating any live Datawrapper charts
that point at these files.

Four outputs:
  dri_vs_cpi.csv          Headline line chart: DRI vs official CPI
  dri_components.csv      Component contributions, wide format (stacked area)
  dri_component_table.csv Current values, MoM, YoY, weight (table chart)
  dri_metadata.csv        Freshness report: one row per component
"""

from __future__ import annotations

import pandas as pd

from src.store import save_published

# Human-readable labels for component IDs in the component table and legend
_COMPONENT_LABELS: dict = {
    "food_at_home": "Food at Home",
    "mortgage_payment": "Mortgage Payment",
    "gas": "Gas",
    "auto_insurance": "Auto Insurance",
    "cc_interest": "Credit Card Interest",
    "dining_out": "Dining Out",
    "utilities": "Utilities",
    "used_cars": "Used Cars",
    "eggs": "Eggs",
    "home_insurance": "Home Insurance",
    "rent": "Rent",
}


def _label(component_id: str) -> str:
    return _COMPONENT_LABELS.get(component_id, component_id.replace("_", " ").title())


def publish_dri_vs_cpi(panel: pd.DataFrame) -> None:
    """Write dri_vs_cpi.csv — wide format, two lines for Datawrapper line chart.

    Columns: [Date, Dead Reckoning Index, Official CPI]
    Both series rebased to Jan 2020 = 100.
    """
    out = panel[["date", "dri", "cpi"]].copy()
    out.columns = ["Date", "Dead Reckoning Index", "Official CPI"]
    out["Date"] = pd.to_datetime(out["Date"]).dt.strftime("%Y-%m-%d")
    out = out.dropna(subset=["Dead Reckoning Index"])
    save_published("dri_vs_cpi", out)


def publish_dri_components(panel: pd.DataFrame, weights: pd.Series) -> None:
    """Write dri_components.csv — wide format for Datawrapper stacked area.

    Datawrapper stacked area charts expect one column per series, not tidy
    long format. Each component column holds its weighted contribution to the
    DRI (rebased_value * normalized_weight), so the columns sum to the DRI line.

    Columns: [Date, <Component Label>, ...]  — one column per component.
    """
    comp_cols = [c for c in weights.index if c in panel.columns]
    out = panel[["date"]].copy()
    out["Date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    out = out.drop(columns=["date"])

    for col in comp_cols:
        out[_label(col)] = (panel[col] * weights[col]).round(4)

    save_published("dri_components", out)


def publish_dri_component_table(
    panel: pd.DataFrame,
    weights: pd.Series,
    data_as_of: dict | None = None,
) -> None:
    """Write dri_component_table.csv — one row per component for table chart.

    Columns: [Component, Data as of, Latest, MoM %, YoY %, Weight]
    Latest: most recent rebased index value (Jan 2020 = 100 baseline).
    MoM %: month-over-month percent change in the rebased value.
    YoY %: year-over-year percent change in the rebased value.
    Weight: normalized weight as a decimal (not percentage).
    """
    data_as_of = data_as_of or {}
    comp_cols = [c for c in weights.index if c in panel.columns]
    panel_sorted = panel.sort_values("date")

    rows = []
    for col in comp_cols:
        s = panel_sorted.set_index("date")[col].dropna()
        if len(s) == 0:
            continue

        latest = s.iloc[-1]

        mom_pct = None
        if len(s) >= 2:
            prev_month = s.iloc[-2]
            if prev_month != 0:
                mom_pct = round((latest - prev_month) / prev_month * 100, 2)

        yoy_pct = None
        if len(s) >= 13:
            year_ago = s.iloc[-13]
            if year_ago != 0:
                yoy_pct = round((latest - year_ago) / year_ago * 100, 2)

        as_of = data_as_of.get(col)
        as_of_str = pd.Timestamp(as_of).strftime("%Y-%m-%d") if as_of is not None else None

        rows.append({
            "Component": _label(col),
            "Data as of": as_of_str,
            "Latest": round(latest, 2),
            "MoM %": mom_pct,
            "YoY %": yoy_pct,
            "Weight": round(float(weights[col]), 4),
        })

    out = pd.DataFrame(rows, columns=["Component", "Data as of", "Latest", "MoM %", "YoY %", "Weight"])
    save_published("dri_component_table", out)


def publish_dri_metadata(
    freshness: dict,
    weights: pd.Series,
    cfg: dict,
) -> None:
    """Write dri_metadata.csv — one row per non-deferred component.

    Columns: component_id, series_id, cadence, data_as_of, age_days, status,
             carried_forward, in_index, weight

    Includes excluded_from_index components (e.g. cc_interest) so the data
    layer is fully visible. Deferred components are omitted.
    """
    components = cfg.get("dri_components", [])
    raw_weights = {}
    for comp in components:
        if not comp.get("deferred") and not comp.get("excluded_from_index"):
            raw_weights[comp["id"]] = comp.get("weight", 0.0)

    rows = []
    for comp in components:
        cid = comp["id"]
        if comp.get("deferred"):
            continue
        report = freshness.get(cid)
        if report is None:
            continue

        in_index = not comp.get("excluded_from_index", False)
        w = float(weights.get(cid, 0.0)) if in_index else 0.0

        rows.append({
            "component_id": cid,
            "series_id": report.series_id,
            "cadence": report.cadence,
            "data_as_of": report.latest_observation.strftime("%Y-%m-%d"),
            "age_days": report.age_days,
            "status": report.status,
            "carried_forward": report.carried_forward,
            "in_index": in_index,
            "weight": round(w, 6),
        })

    out = pd.DataFrame(rows, columns=[
        "component_id", "series_id", "cadence", "data_as_of",
        "age_days", "status", "carried_forward", "in_index", "weight",
    ])
    save_published("dri_metadata", out)
