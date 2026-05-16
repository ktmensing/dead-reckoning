import pandas as pd
from typing import Dict, Optional

def z_score(s: pd.Series, window: int = 60) -> pd.Series:
    """Calculate rolling z-score for a series."""
    return (s - s.rolling(window).mean()) / s.rolling(window).std()

def build_composite_sentiment(
    sentiment_sources: Dict[str, pd.Series],
    weights: Dict[str, float] = None
) -> pd.Series:
    """
    Build a composite sentiment index from multiple sources.
    Args:
        sentiment_sources: Dict of {source_name: pd.Series}.
        weights: Dict of {source_name: weight}. Defaults to equal weights.
    """
    if weights is None:
        weights = {k: 1.0 / len(sentiment_sources) for k in sentiment_sources}

    weighted_components = []
    for name, series in sentiment_sources.items():
        transformed = z_score(series)
        weighted = transformed * weights.get(name, 0.0)
        weighted_components.append(weighted)

    return sum(weighted_components)

def build_mercury(
    dri: pd.Series,
    sentiment_sources: Dict[str, pd.Series],
    weights: Dict[str, float] = None
) -> pd.DataFrame:
    """
    Build the Mercury divergence index.
    Args:
        dri: Felt cost index (pd.Series)
        sentiment_sources: Dict of {source_name: pd.Series}
        weights: Dict of {source_name: weight}. Defaults to equal weights.
    """
    if weights is None:
        weights = {k: 1.0/len(sentiment_sources) for k in sentiment_sources}

    # Build composite sentiment
    composite_sentiment = build_composite_sentiment(sentiment_sources, weights)

    # Calculate divergence
    conditions_z = z_score(dri) * -1
    divergence = composite_sentiment - conditions_z

    # Label zones
    def assign_zone(divergence_val: float) -> str:
        if divergence_val > 0.5:
            return "Mercury Rising / Hot Spend Summer"
        elif divergence_val < -0.5:
            return "Mercury in Retrograde / Vibecession Szn"
        else:
            return "Dead Calm / Nickel Lachey"

    idx = divergence.index
    return pd.DataFrame({
        "date": idx,
        "divergence": divergence.values,
        "sentiment_z": composite_sentiment.reindex(idx).values,
        "conditions_z": conditions_z.reindex(idx).values,
        "zone": divergence.apply(assign_zone).values,
    }).dropna()


def calculate_partisan_distortion(
    mich: pd.Series,
    dri: pd.Series,
    window: int = 60,
) -> pd.DataFrame:
    """
    Partisan distortion cross-check.

    Compares households' inflation expectations (MICH, percent) against felt
    inflation (DRI year-over-year, percent). When the standardized gap exceeds
    one standard deviation in absolute value, MICH is far from where felt
    conditions would suggest — a flag that sentiment reads may be politically
    rather than economically driven.

    Both inputs are in percentage points; subtraction is unit-consistent.
    """
    mich_m = mich.resample("ME").last()
    # ffill bridges any suppressed/absent DRI months (e.g. Nov 2025) before
    # computing pct_change, so a single NaN does not blank the rolling z_score.
    dri_m = dri.resample("ME").last().ffill()
    dri_yoy = dri_m.pct_change(12, fill_method=None) * 100  # percent

    common = mich_m.index.intersection(dri_yoy.index)
    mich_m = mich_m.loc[common]
    dri_yoy = dri_yoy.loc[common]

    gap_pp = mich_m - dri_yoy
    gap_z = z_score(gap_pp, window=window)
    flag = (gap_z.abs() > 1.0).astype(int)

    return pd.DataFrame({
        "date": common,
        "mich": mich_m.values,
        "dri_yoy_pct": dri_yoy.values,
        "gap_pp": gap_pp.values,
        "gap_z": gap_z.values,
        "partisan_flag": flag.values,
    }).dropna()
