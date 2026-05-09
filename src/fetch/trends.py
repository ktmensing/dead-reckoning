from pytrends.request import TrendReq
import pandas as pd

ANXIETY_BASKET = ["recession", "layoffs", "stagflation", "vibecession"]

def fetch_anxiety_index(window_years: int = 5) -> pd.DataFrame:
    pt = TrendReq(hl="en-US", tz=300)
    pt.build_payload(ANXIETY_BASKET, timeframe=f"today {window_years}-y", geo="US")
    df = pt.interest_over_time().drop(columns=["isPartial"], errors="ignore")
    df["anxiety_index"] = df[ANXIETY_BASKET].mean(axis=1)
    return df[["anxiety_index"]].reset_index().rename(columns={"date": "date"})