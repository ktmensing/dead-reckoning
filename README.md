# Dead Reckoning Pipeline

Macro-aware indicator dashboard. Fetches federal data (FRED, BLS, EIA, BEA), computes the Dead Reckoning Index (DRI) Price Layer, and writes Datawrapper-ready CSVs to `data/published/`. Charts auto-refresh from those files once Datawrapper is linked to the GitHub raw URLs.

Phase 1 delivers the DRI Price Layer end-to-end. Phases 2–6 (Mercury Reading, Altitude Index, Behavior Layer, GitHub Actions automation, Datawrapper API) are documented in `dead-reckoning-pipeline-roadmap.md`.

---

## Setup

```bash
cp .env.example .env
# Fill in the four API keys — see "API keys" below
source .venv/bin/activate
```

**API keys** (all free, register at the URLs below):

| Key | Register at |
|---|---|
| `FRED_API_KEY` | research.stlouisfed.org/docs/api |
| `BLS_API_KEY` | data.bls.gov/registrationEngine |
| `BEA_API_KEY` | apps.bea.gov/API/signup |
| `EIA_API_KEY` | eia.gov/opendata/register.php |

---

## Running

```bash
make all        # Full pipeline: fetch → validate → transform → publish
make smoke      # Quick live-API check for all four sources
make test       # pytest (no network, fixture data only)
make setup      # Print env-check status

make fetch      # Fetch raw data only (writes data/raw/)
make build      # Transform only (reads data/raw/, writes data/derived/)
make publish    # Publish only (reads data/derived/, writes data/published/)
make clean      # Remove data/derived/ and data/published/ (not data/raw/)
```

After `make all`, commit and push `data/derived/` and `data/published/` to expose the CSVs at:
```
https://raw.githubusercontent.com/<user>/dead-reckoning/main/data/published/dri_vs_cpi.csv
```
Point a Datawrapper chart at that URL and set the refresh interval. The chart updates on every subsequent push.

---

## Adding a series

1. **Fetch**: If the source is new, add a fetcher in `src/fetch/` following the request/parse split in `fred.py`. If the source is BLS, FRED, or EIA, no new code is needed.

2. **Config**: Add an entry to `config/series.yaml` with `id`, `weight`, `source`, `series_id`. Set `deferred: true` to include in the YAML but exclude from the composite until the fetcher is ready.

3. **Validate**: Add a range check to `_RANGE_CHECKS` in `src/validate.py` if you know the plausible range.

4. **Wire up**: In `scripts/run_weekly.py`, ensure the new source's series is fetched in the appropriate section (BLS batch, FRED loop, EIA, etc.). The transform picks it up automatically by component ID.

5. **Test**: Run `make smoke` to confirm the fetcher returns real data, then `make all` to confirm the pipeline produces plausible CSVs.

---

## Schema

**Fetcher output** (canonical for all sources):

| Column | Type | Notes |
|---|---|---|
| `date` | `pd.Timestamp` | Start of reporting period (first of month for monthly series) |
| `value` | `float` | Native units — not rebased |
| `series_id` | `str` | Source series identifier (FRED ID, BLS ID, etc.) |
| `source` | `str` | `fred`, `bls`, `eia`, or `bea` |
| `fetched_at` | `pd.Timestamp` | UTC timestamp of fetch |

**DRI panel** (`data/derived/dri_panel.csv`):

| Column | Notes |
|---|---|
| `date` | Month-start, Jan 2020–present |
| `dri` | DRI composite, Jan 2020 = 100 |
| `cpi` | Official CPI all items, Jan 2020 = 100 |
| `<component_id>` | One column per non-deferred component, Jan 2020 = 100 |

**Published CSVs** (`data/published/`):

| File | Shape | Datawrapper chart type |
|---|---|---|
| `dri_vs_cpi.csv` | Wide: `[Date, Dead Reckoning Index, Official CPI]` | Line chart |
| `dri_components.csv` | Long: `[Date, Component, Value]` | Stacked area |
| `dri_component_table.csv` | Wide: `[Component, Latest, MoM %, YoY %, Weight]` | Table |

Column headers in published CSVs are stable identifiers — changing them breaks live Datawrapper templates.

---

## Failure modes

**Series returns empty or stale data**: `ValidationError` raised before transform runs. Check the BLS/FRED/EIA news release to confirm whether the series is still published. If retired, stop and find the replacement ID — don't guess.

**API key rejected**: `FetchError` with a clear message. Confirm the key in `.env` matches the one from the registration portal.

**BLS batch rate limit** (250 requests/day on v2): The batch call counts as one request regardless of series count, so this is unlikely. If hit, run `make all` the next day or split into two days.

**Datawrapper chart not refreshing**: Open the chart → More → Refresh data. Symptom is that the chart shows old data after a push. This is a Datawrapper caching issue, not a pipeline issue.

---

## What's not in this build

- Mercury Reading divergence index (Phase 2)
- Altitude Index luxury composite (Phase 3)
- DRI-B Behavior Layer (Phase 4)
- GitHub Actions automation (Phase 5)
- Datawrapper PNG snapshot export (Phase 6)
- Zillow ZORI rent fetcher (deferred; marked in `config/series.yaml`)
- Quarterly reserve components (streaming stack, ticket prices, etc.)

See `dead-reckoning-pipeline-roadmap.md` for the full build sequence.
