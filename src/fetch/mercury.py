import pandas as pd
from typing import Dict

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
