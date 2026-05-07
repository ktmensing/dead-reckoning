# Dead Reckoning Data Pipeline — Build Roadmap

*From raw federal data to populated Datawrapper charts. End state: Sunday morning is a 10-minute operation.*

---

## The shape of the system you're building

There are two things people usually conflate when they describe a "data pipeline for a dashboard." One is the work that happens once: building the templates, wiring up the APIs, deciding the index methodology. The other is the work that happens every week forever: pulling fresh data, refreshing charts, composing the Sunday Territory edition. The architecture below separates them deliberately, so the per-week work shrinks to almost nothing.

The key technical insight that makes this possible is **Datawrapper's external data linking** — every chart can point at an external CSV URL with a refresh cadence, which means once a chart template exists, your job is just to update the CSV. You're not creating new charts each week. You're not even opening Datawrapper most weeks. You run a Python script, it writes CSVs to a hosted location, the charts auto-refresh, and you compose the narrative around them.

The end-state Sunday flow:

1. Run `make territory` (or equivalent CLI command) — pipeline pulls latest data from FRED, BLS, BEA, EIA, computes derived series, writes CSVs, pushes them to GitHub.
2. Charts auto-refresh from the new CSVs (Datawrapper's external linking handles this).
3. You write the Sunday Territory post in markdown, embedding the charts via their stable iframe URLs.
4. Publish to Micro.blog. Done.

Total elapsed time on a normal Sunday, after build: 10–15 minutes. Total elapsed time when something breaks: bounded by the failure modes the pipeline itself surfaces (more on this below).

---

## Architecture in four layers

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 1: SOURCES                                            │
│  FRED · BLS · BEA · EIA · CFPB · Google Trends · Manual CSVs │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 2: PIPELINE  (Python)                                 │
│  fetch → validate → transform → persist                      │
│  Outputs: canonical timeseries store (DuckDB or CSV)         │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 3: PUBLISH  (CSV → hosted URL)                        │
│  Datawrapper-shaped CSVs pushed to GitHub repo               │
│  Stable URLs: raw.githubusercontent.com/...                  │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 4: VISUALIZATION  (Datawrapper)                       │
│  Chart templates linked to CSV URLs · Auto-refresh           │
│  Iframe embeds in Sunday Territory                           │
└─────────────────────────────────────────────────────────────┘
```

Each layer is built once and changes infrequently. Adding a new indicator means: add a fetcher, add a transformation, add a CSV output, build a chart template once. After that, it runs every week with the rest.

---

## Phase 0 — Setup (Week 1)

### API keys you'll need

| Source | URL to register | Cost | Rate limit |
|---|---|---|---|
| FRED | research.stlouisfed.org/docs/api | Free | 120 req/min |
| BLS | data.bls.gov/registrationEngine | Free | v2: 250/day, 50 series/req |
| BEA | apps.bea.gov/API/signup | Free | None published; be polite |
| EIA | eia.gov/opendata/register.php | Free | 5,000 req/hour |

Register all four today. Each takes about two minutes. Drop the keys into a `.env` file at the project root (and put `.env` in your `.gitignore` immediately — the most common way these projects leak credentials is committing the env file).

### Repo and environment setup

```bash
mkdir -p ~/code/dead-reckoning
cd ~/code/dead-reckoning
git init

# Project structure
mkdir -p {src,data/raw,data/derived,data/published,charts,scripts,tests}
touch README.md .env .env.example .gitignore Makefile

# Python env
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install pandas requests fredapi python-dotenv pyyaml duckdb pytrends pdfplumber
pip freeze > requirements.txt
```

`.gitignore` essentials:
```
.env
.venv/
data/raw/
__pycache__/
*.pyc
.DS_Store
```

`data/raw/` is ignored because raw API responses are reproducible from the source; you don't need to version them. `data/derived/` and `data/published/` *are* committed, because that's how the GitHub-hosted CSVs reach Datawrapper, and version-controlling them gives you a free audit trail of every value the dashboard has ever shown.

### Project structure

```
dead-reckoning/
├── README.md                    # Operational notes
├── Makefile                     # CLI orchestration
├── .env                         # Secrets (gitignored)
├── .env.example                 # Template
├── requirements.txt
├── config/
│   ├── series.yaml              # Source IDs, weights, metadata
│   └── charts.yaml              # Datawrapper chart IDs and refresh schedule
├── src/
│   ├── __init__.py
│   ├── fetch/
│   │   ├── fred.py
│   │   ├── bls.py
│   │   ├── bea.py
│   │   ├── eia.py
│   │   └── trends.py
│   ├── transform/
│   │   ├── dri.py               # Price layer composite
│   │   ├── drib.py              # Behavior layer
│   │   ├── mercury.py           # Divergence calc
│   │   └── altitude.py          # Luxury composite
│   ├── publish/
│   │   └── datawrapper_csv.py   # CSV writers
│   ├── validate.py              # Freshness, ranges, completeness
│   └── store.py                 # DuckDB / CSV persistence
├── scripts/
│   ├── run_weekly.py            # Sunday cron entry point
│   └── bootstrap_history.py     # One-time historical backfill
├── data/
│   ├── raw/                     # gitignored
│   ├── derived/                 # canonical timeseries (committed)
│   └── published/               # Datawrapper-shaped CSVs (committed)
├── charts/                      # Documentation of each Datawrapper chart
└── tests/
```

This structure separates concerns cleanly: `fetch` modules know nothing about transformation, `transform` modules know nothing about publishing, `publish` modules know nothing about Datawrapper internals beyond the CSV shape it expects. That separation is what lets you swap a source (e.g., move from EIA gas to AAA gas later) without rewriting downstream code.

---

## Phase 1 — Build the DRI Price Layer end-to-end (Weeks 2–4)

The DRI Price Layer is the right first instrument to build because (a) it's the headline of the whole system, (b) it has the most data sources, so building it exercises every part of the pipeline, and (c) once it works, the other three instruments are smaller increments on the same architecture.

### Step 1: Configure the series (Week 2, day 1)

`config/series.yaml`:

```yaml
dri_components:
  - id: rent
    weight: 0.18
    source: zillow_zori   # or apartment_list_rent_index
    series_id: ZORDSFRR
    fetcher: zillow
    notes: "Asking rent. Salience-weighted above expenditure share."

  - id: mortgage_payment
    weight: 0.11
    source: derived
    inputs:
      - {fetcher: fred, series_id: MSPUS}        # Median home price
      - {fetcher: fred, series_id: MORTGAGE30US} # 30-yr fixed rate
    transform: monthly_payment_30yr
    notes: "Computed from price + rate. 20% down assumed."

  - id: food_at_home
    weight: 0.13
    source: bls
    series_id: CUSR0000SAF11
    notes: "CPI food at home, SA."

  - id: gas
    weight: 0.10
    source: eia
    series_id: PET.EMM_EPMR_PTE_NUS_DPG.W
    notes: "EIA weekly retail gasoline. Resampled monthly."

  - id: auto_insurance
    weight: 0.06
    source: bls
    series_id: CUSR0000SETE
    notes: "CPI motor vehicle insurance."

  - id: cc_interest
    weight: 0.06
    source: fred
    series_id: TERMCBCCALLNS
    notes: "Commercial bank credit card interest rate."

  - id: dining_out
    weight: 0.07
    source: bls
    series_id: CUSR0000SEFV
    notes: "CPI food away from home."

  - id: utilities
    weight: 0.03
    source: bls
    series_id: CUSR0000SEHF
    notes: "CPI energy services."

  - id: used_cars
    weight: 0.04
    source: bls
    series_id: CUSR0000SETA02
    notes: "CPI used cars and trucks."

  - id: eggs
    weight: 0.03
    source: bls
    series_id: APU0000708111
    notes: "Average price, eggs per dozen. Salience-weighted."

  - id: home_renters_insurance
    weight: 0.03
    source: bls
    series_id: CUUR0000SEHD
    notes: "CPI tenants and household insurance, NSA only."

# Reserve 10% for quarterly inputs (handled separately):
# new car payment, streaming stack, shrinkflation proxy, ticket prices
```

Treat this YAML as the contract. When the methodology evolves (you swap a series, change a weight, add a component), the only thing that changes is this file plus the relevant fetcher. Everything else flows downstream.

### Step 2: Build the fetchers (Week 2, days 2–4)

The fetchers are simple. Each one takes a series ID and a date range, returns a clean pandas DataFrame with `[date, value, series_id]` columns. Cache raw responses to `data/raw/{source}/{series_id}.json` so re-runs are fast and you have a record of what the API returned on what date.

Skeleton for `src/fetch/fred.py`:

```python
import os
import pandas as pd
from fredapi import Fred
from datetime import date
import json
from pathlib import Path

_fred = Fred(api_key=os.environ["FRED_API_KEY"])

def fetch(series_id: str, start: date | None = None) -> pd.DataFrame:
    series = _fred.get_series(series_id, observation_start=start)
    df = series.rename("value").reset_index()
    df.columns = ["date", "value"]
    df["series_id"] = series_id
    df["source"] = "fred"
    df["fetched_at"] = pd.Timestamp.utcnow()

    # Cache the raw payload
    cache_path = Path(f"data/raw/fred/{series_id}.csv")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache_path, index=False)

    return df
```

For BLS, the v2 API is JSON with batched series support — write your fetcher to accept a list of series IDs and batch up to 50 per request. For EIA, it's JSON with a slightly arcane series ID grammar (the dotted notation is what trips people up). For BEA, it's a JSON API with a more complex parameter structure but well-documented.

Each fetcher should handle the same failure modes uniformly: 401 (bad key), 429 (rate limit), 5xx (server side), empty response. A consistent `FetchError` exception lets the orchestrator decide what to do (retry, skip, alert).

### Step 3: Validate (Week 2, day 5)

`src/validate.py`:

```python
def validate_series(df, series_id: str, max_age_days: int = 60):
    """Check that series is fresh, not all-null, in plausible range."""
    if df.empty:
        raise ValidationError(f"{series_id}: empty result")
    age = (pd.Timestamp.now() - df["date"].max()).days
    if age > max_age_days:
        raise ValidationError(f"{series_id}: latest obs is {age} days old")
    if df["value"].isna().all():
        raise ValidationError(f"{series_id}: all values null")
    # Add range checks per series in config if you want
```

Validation is what turns "the script ran" into "the data is good." It also turns silent failures (a series stops updating, an API quietly returns yesterday's data forever) into noisy ones, which is what you want at 8am Sunday.

### Step 4: Transform (Week 3)

`src/transform/dri.py`:

```python
import pandas as pd
import yaml

def build_dri(timeseries: dict[str, pd.DataFrame], config_path="config/series.yaml") -> pd.DataFrame:
    """
    Build the Dead Reckoning Index Price Layer.
    timeseries is a dict of {component_id: DataFrame[date, value]}.
    Returns a DataFrame with [date, dri, cpi, *components, *contributions].
    """
    cfg = yaml.safe_load(open(config_path))
    components = cfg["dri_components"]

    # Reindex everything to monthly, end-of-month
    monthly = {}
    for comp in components:
        df = timeseries[comp["id"]].copy()
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").resample("M").last()
        monthly[comp["id"]] = df["value"]

    panel = pd.DataFrame(monthly)

    # Rebase each component to Jan 2020 = 100
    base = panel.loc["2020-01-31"]
    rebased = (panel / base) * 100

    # Weighted composite
    weights = pd.Series({c["id"]: c["weight"] for c in components})
    weights = weights / weights.sum()  # normalize (handles the 10% reserve)
    dri = (rebased * weights).sum(axis=1)

    # Add official CPI for comparison
    cpi = timeseries["cpi_headline"].set_index("date")["value"]
    cpi_monthly = cpi.resample("M").last()
    cpi_rebased = (cpi_monthly / cpi_monthly.loc["2020-01-31"]) * 100

    out = rebased.copy()
    out["dri"] = dri
    out["cpi"] = cpi_rebased
    return out.reset_index()
```

The transform stage is where the methodology lives. Keep it separate from fetching so methodology changes don't touch source code. When you add salience weighting beyond expenditure share (the SESSION-HANDOFF references this for gas, eggs, and other high-visibility items), it's a change in this file only.

### Step 5: Persist (Week 3)

DuckDB is the right tool here for time-series analytical work. It's a single binary file, no server, fast, supports SQL, and Pandas integrates natively. Alternative is plain CSVs in `data/derived/` — simpler, version-controllable, slower for analysis. For Phase 1, **plain CSVs are fine**. Move to DuckDB when you find yourself doing complex queries across instruments.

```python
# src/store.py
def save_derived(name: str, df: pd.DataFrame):
    path = Path(f"data/derived/{name}.csv")
    df.to_csv(path, index=False)
```

### Step 6: Publish CSVs in Datawrapper shape (Week 3–4)

Datawrapper expects clean, columnar CSVs. Each chart gets its own CSV. The shape depends on chart type — for a line chart with multiple lines, it's `[date, line1, line2, ...]`. For a stacked area, same. For a bar chart, `[category, value]`. Below is the canonical shape for the headline DRI vs CPI chart:

```python
# src/publish/datawrapper_csv.py
def publish_dri_vs_cpi(dri_panel: pd.DataFrame):
    out = dri_panel[["date", "dri", "cpi"]].copy()
    out.columns = ["Date", "Dead Reckoning Index", "Official CPI"]
    out["Date"] = pd.to_datetime(out["Date"]).dt.strftime("%Y-%m-%d")
    out.to_csv("data/published/dri_vs_cpi.csv", index=False)

def publish_dri_components(dri_panel: pd.DataFrame, contributions: pd.DataFrame):
    # Stacked area of contribution to DRI
    contributions["Date"] = pd.to_datetime(contributions["Date"]).dt.strftime("%Y-%m-%d")
    contributions.to_csv("data/published/dri_components.csv", index=False)
```

The naming convention matters: column headers in the CSV become the legend in Datawrapper. Get the names right here and you don't fight the chart later.

### Step 7: Push to GitHub (Week 4)

```bash
cd ~/code/dead-reckoning
git add data/derived data/published
git commit -m "Weekly refresh: $(date +%Y-%m-%d)"
git push origin main
```

Wrap that in a `Makefile` target:

```makefile
publish:
	git add data/derived data/published
	@if git diff --staged --quiet; then \
	  echo "No data changes to publish"; \
	else \
	  git commit -m "Weekly refresh: $$(date +%Y-%m-%d)"; \
	  git push origin main; \
	fi
```

The CSV is now reachable at:

```
https://raw.githubusercontent.com/<your-username>/dead-reckoning/main/data/published/dri_vs_cpi.csv
```

That URL is what Datawrapper will point at.

### Step 8: Build the Datawrapper chart template once (Week 4)

In Datawrapper:

1. Create new chart → Line Chart.
2. **Upload data → "Link external dataset"** → paste the GitHub raw URL.
3. Set refresh interval. For weekly Territory, "every hour" is fine and means you never have to think about it. (Datawrapper offers "every 5 minutes" up to "weekly"; pick what matches your cadence.)
4. Configure axes, titles, color. Establish the visual identity here — it propagates to every refresh.
5. Publish. Capture the embed iframe URL.
6. Document the chart in `charts/dri_vs_cpi.md` — chart ID, embed URL, source CSV, refresh cadence, last-known-good visual config.

Repeat for each chart you need on the headline. The DRI alone wants probably four charts: headline (DRI vs CPI), component contributions (stacked area), component table (current values + YoY), and a small visualization of the salience-weighting (e.g., budget share vs. DRI weight bar chart for transparency).

### What "done with Phase 1" looks like

You can run a single command and get fresh DRI charts published. The Sunday Territory edition can use any of those charts via iframe. The whole pipeline runs in 30–90 seconds. Adding a new component to the DRI is a matter of editing `series.yaml` and (if it's a new source) writing one fetcher.

---

## Phase 2 — Mercury Reading (Weeks 5–6)

Mercury is computationally light because it's derivative of work you've already done. It needs:

- **Sentiment composite:** University of Michigan Index of Consumer Expectations (FRED: `MICH`) and Conference Board Consumer Confidence Expectations (FRED: `CONCCONF`). Z-score each over a rolling window, average. (Note: Conference Board licensing — confirm your usage rights, or fall back to U-Mich alone.)
- **Conditions composite:** Inverted DRI (you already have this from Phase 1), z-scored.
- **Divergence:** sentiment z-score minus conditions z-score. Positive = sentiment warmer than conditions = "Mercury Rising / Hot Spend Summer." Negative = sentiment colder than conditions = "Mercury in Retrograde / Vibecession Szn." Near zero = "Dead Calm / Nickel Lachey."
- **Media anxiety overlay:** Google Trends basket (e.g., "recession," "layoffs," "stagflation," "vibecession"). Pull via `pytrends`, normalize, plot offset by 2–4 weeks ahead of survey sentiment to demonstrate the lead/lag relationship.

`src/transform/mercury.py` is short — maybe 60 lines. The bigger work is the chart, because the zone-shading visualization is what makes Mercury legible. Datawrapper's line chart supports background shading; configure it to show three horizontal bands for the three zones, with the current divergence line on top.

The Cultural Navigator's signature read ("America's 250th is 8 weeks out…") depends on Mercury's zone being at-a-glance readable. Spend an extra hour on the chart styling here. It's the visual the rest of the system will be judged by.

---

## Phase 3 — Altitude Index baseline (Months 3–4)

The SESSION-HANDOFF specifies Altitude launches at Month 6–9 of the Dead Reckoning roadmap, after DRI baseline is established. The Phase 3 work is to *start collecting* baseline data so when launch arrives, you have history, not a blank chart.

Components and sources:

| Component | Weight | Source | Series / approach |
|---|---|---|---|
| S&P 500 | 25% | FRED | `SP500` |
| Luxury home prices | 20% | FHFA HPI top decile, or Zillow top-tier ZHVI | `ATNHPIUS00000Q` (FRED) as proxy |
| Premium travel | 12% | BTS / industry reports | Manual quarterly until automatable |
| Luxury goods | 10% | LVMH / Richemont / Kering quarterly reports | Manual quarterly |
| Premium car leases | 8% | Industry data | Manual; consider Cox Automotive |
| Fine dining | 8% | OpenTable State of the Industry | Manual quarterly |
| Art market | 7% | Artnet / Artprice indices | Manual quarterly |
| Private school tuition | 5% | NCES | Annual update |
| High-end hotel ADR | 5% | STR/CoStar (paid) or industry estimates | Manual quarterly |

Most of Altitude's components are not API-accessible, which means the pipeline pattern needs a **manual input layer**. Build it explicitly: `data/raw/manual/altitude_quarterly.csv` that you update by hand each quarter with the latest values from each non-API source. The transform layer reads from both API fetchers and the manual CSV, treating them identically downstream. Explicit beats implicit — pretending the manual sources are automated invites silent staleness.

The K-Shape Contour visualization (Altitude minus DRI spread) is a single computed series and a simple line chart, but it's the most cite-able artifact in the whole system. Make sure the chart annotation calls out the recession bands and has a clear interpretation key.

---

## Phase 4 — DRI-B Behavior Layer (Months 4–5)

This is the hardest layer to automate because most of the data isn't API-accessible.

| Component | Source | Automation status |
|---|---|---|
| Private label market share | Circana / NielsenIQ | Paid subscription; manual until you have one |
| Paycheck-to-paycheck rate | LendingClub / PYMNTS report | PDF scrape (`pdfplumber`) |
| Multiple job holders | BLS | API: `LNS12026620` |
| BNPL delinquency | CFPB report | Manual; CFPB releases periodically |
| Dupe economy search index | Google Trends | `pytrends` |
| Shrinkflation acceptance | Manual sentiment scan | Quarterly summary |

DRI-B is described in the SESSION-HANDOFF as "not a composite — a dashboard of directional readings." That's important architecturally: don't build a composite. Build a small-multiples chart in Datawrapper (or six tiny charts arranged on a page) where each component is its own line with a current-direction indicator (up arrow / down arrow / flat). The visual frame is "pressure gauge dashboard," not "single index."

Build the BLS-API and Google Trends components first (they're free and reliable), establish the visual template, and add the manual sources as ongoing operational discipline. Don't wait until everything is automated to ship — DRI-B is more useful with five-of-six components than with none.

---

## Phase 5 — Automation: GitHub Actions (Month 5)

By Month 5 you've been running the pipeline manually each Sunday. The pattern is stable. Now automate.

`.github/workflows/weekly_refresh.yml`:

```yaml
name: Weekly data refresh
on:
  schedule:
    - cron: '0 11 * * 0'  # Sundays 7am ET
  workflow_dispatch:       # Manual trigger button

jobs:
  refresh:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.12' }
      - run: pip install -r requirements.txt
      - run: python scripts/run_weekly.py
        env:
          FRED_API_KEY: ${{ secrets.FRED_API_KEY }}
          BLS_API_KEY: ${{ secrets.BLS_API_KEY }}
          BEA_API_KEY: ${{ secrets.BEA_API_KEY }}
          EIA_API_KEY: ${{ secrets.EIA_API_KEY }}
      - name: Commit and push
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
          git add data/derived data/published
          git diff --staged --quiet || git commit -m "Weekly refresh: $(date +%Y-%m-%d)"
          git push
```

Once this runs successfully twice in a row, your Sunday operation is reduced to: open laptop, check the GitHub Action status, write the Sunday Territory narrative around the already-fresh charts.

The action should also handle failure visibly. Add a step that, on failure, opens a GitHub issue with the error trace. That's your alerting layer — the issue notification email becomes the Sunday-morning "something broke" signal.

---

## Phase 6 — Polish (Month 6)

Things to add once the system is humming:

- **Snapshot images for the newsletter.** Datawrapper iframes work great for the web, but the Hermetic Order newsletter on Buttondown is better with PNG snapshots. Datawrapper exports PNGs via API; add a step that pulls fresh PNGs each Sunday and saves them to `data/published/snapshots/` for newsletter use.
- **Methodology page renderer.** The methodology page on curiousmarketers.club should be partly auto-generated from `config/series.yaml` so it can never drift from the actual pipeline. A short script reads the YAML and emits a markdown file with the current weights, sources, and notes.
- **Field Note triggers.** Add validation rules that flag when a component moves more than a configurable threshold (e.g., gas prices up 10% week-over-week). When triggered, the pipeline writes a `triggers.json` that your Sunday narrative process reads — or, more aggressively, the GitHub Action posts a Slack/Discord message, which becomes the prompt for an event-driven Field Note.
- **Q4 2022 retrospective infrastructure.** Run the pipeline against historical data with weights set as designed. Compare DRI to official CPI for Q4 2022. The retrospective becomes a single notebook and the data is already in your store; the writing is the only remaining work.
- **DuckDB analytical layer.** Once you have a few years of derived data, DuckDB queries become useful for cross-instrument analysis. ("Show me every month where Mercury was in Retrograde and the K-Shape Contour widened simultaneously.") That's the analytical workspace where Cultural Navigator research happens.

---

## The Sunday operating procedure (post-Phase 5)

```
06:00 ET  GitHub Action fires automatically
06:05     Pipeline complete; charts auto-refresh
07:30     You wake up, open laptop
07:32     Check GitHub Action status (success / triage)
07:35     Open Sunday Territory draft template
07:40     Write narrative around the fresh data:
            - One paragraph: where DRI is now, vs CPI, vs last week
            - Mercury zone callout
            - Component pressure highlights (top 3 movers)
            - Early Warning Layer one-liner
            - One link to a relevant essay or Field Note from the week
08:15     Embed chart iframes
08:30     Publish to Micro.blog
08:35     Done
```

The whole thing is bounded. When something breaks, the failure is in one of three places (data fetch, transformation, publish) and the validate.py contract tells you which one.

---

## Failure modes and what to watch for

A short list, because Sunday morning is not when you want to debug an API change.

**API response shape changes.** BLS in particular has a habit of altering response structure quietly. The fetcher should fail loud (raise) rather than fail soft (return partial data). Tests in `tests/` should fixture-check the response shape and run on every commit.

**Series discontinuation.** Federal data series occasionally get retired or renumbered. The validation layer's max-age check is your detector — if a series stops updating, the pipeline raises before it produces a misleading chart.

**Datawrapper external link not refreshing.** Rare, but possible. Symptom: chart shows old data even though the CSV updated. Fix: open the chart in Datawrapper, click "refresh data" manually, re-publish. Document the chart IDs in `charts/` so you can find them quickly.

**Methodology drift.** As you tune weights or add components, version your methodology. Tag the repo at each methodology change (`v1.0`, `v1.1`, etc.) so the citation trail in Layer 2 (Dead Reckoning Roadmap's "professional credential" tier) is unambiguous.

**Quarterly inputs going stale.** The 10% reserve in DRI for quarterly inputs (new car payment, streaming stack, shrinkflation, ticket prices) needs a calendar reminder. Add a recurring quarterly task to the same calendar that holds the Sunday Territory block.

**Manual inputs (DRI-B, Altitude) becoming the bottleneck.** Track this. If the manual layer is what makes the pipeline feel like work, the next investment is finding API-or-scrape replacements for the heaviest manual sources. Fine dining (OpenTable's State of the Industry) and luxury goods quarterly reports are the most likely candidates for a more elegant solution.

---

## What to build in Week 1

If you want a single concrete next-week list:

1. Register API keys for FRED, BLS, BEA, EIA. Drop them in `.env`.
2. Create the repo with the structure above. Set up the Python env. Add `.gitignore` and confirm `.env` is excluded.
3. Write `src/fetch/fred.py` and confirm it can pull `MORTGAGE30US` and `MSPUS` for the mortgage-payment derived series.
4. Write `src/fetch/bls.py` and confirm it can pull `CUSR0000SAF11` (food at home).
5. Hand-validate the values against a recent BLS news release so you trust the fetcher.
6. Stop. That's the foundation. The rest of Phase 1 builds on it cleanly over the next two weeks.

The mistake to avoid in Week 1 is trying to scaffold the whole pipeline before any single fetch works. Build the smallest thing that pulls real data from a real API, validate it by hand, and *only then* generalize. The architecture above is the destination, not the order.
