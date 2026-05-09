import pandas as pd

def z_score(s: pd.Series, window: int = 60) -> pd.Series:
    return (s - s.rolling(window).mean()) / s.rolling(window).std()

def build_mercury(dri: pd.Series, umich: pd.Series) -> pd.DataFrame:
    # Align to monthly
    dri_m = dri.resample("ME").last()
    umich_m = umich.resample("ME").last()

      # Align to common index before computing
    common = dri_m.index.intersection(umich_m.index)
    dri_m = dri_m.loc[common]
    umich_m = umich_m.loc[common]

    sentiment_z = z_score(umich_m)
    conditions_z = z_score(dri_m) * -1
    divergence = sentiment_z - conditions_z

    return pd.DataFrame({
        "date": divergence.index,
        "divergence": divergence.values,
        "sentiment_z": sentiment_z.values,
        "conditions_z": conditions_z.values,
    }).dropna()

# Zone labels (for annotation, not computed):
# divergence > +0.5 → Mercury Rising / Hot Spend Summer
# divergence < -0.5 → Mercury in Retrograde / Vibecession Szn
# between → Dead Calm / Nickel Lachey