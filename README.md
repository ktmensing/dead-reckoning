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

2. **Config**: Add an entry to `config/series.yaml` with all required fields (see Schema below). Set `deferred: true` to include in the YAML but skip entirely until ready.

3. **Wire up**: In `scripts/run_weekly.py`, ensure the source section fetches the new series. The transform picks it up by component ID automatically.

4. **Test**: `make smoke` to confirm live data, then `make all` to confirm the full pipeline.

---

## Schema

### `config/series.yaml` component fields

| Field | Required | Notes |
|---|---|---|
| `id` | ✓ | Component identifier used as column name in the panel |
| `weight` | ✓ | Raw weight (sum to 0.90; normalized over present components) |
| `source` | ✓ | `bls`, `fred`, `eia`, `derived`, or `zillow_zori` |
| `series_id` | most | Source series identifier; omit for `derived` |
| `cadence` | ✓ | `weekly`, `monthly`, `quarterly`, `semiannual`, `annual` |
| `expected_lag_days` | ✓ | Age beyond which the series is considered stale (logs warning/info) |
| `hard_fail_days` | ✓ | Age beyond which the pipeline aborts |
| `carry_forward` | | `true`: hold last value across inter-release gaps. `false` (default): staleness is a real signal |
| `excluded_from_index` | | `true`: fetched, validated, tracked — but not weighted in DRI. Used for `cc_interest`. |
| `deferred` | | `true`: skip entirely — no fetch, no validate, no contribution |

**Freshness model:** Three outcomes per series:
- `fresh`: age ≤ `expected_lag_days` — proceed silently
- `stale_ok`: age between `expected_lag_days` and `hard_fail_days` — log info (if `carry_forward`) or warning; carry last value forward if configured
- `stale_fail`: age > `hard_fail_days` — raise `ValidationError`; pipeline aborts

**Carry-forward limit:** One cadence period (e.g. monthly → 1 month, quarterly → 3 months). `data_as_of` always reflects the last real observation, not the carried-forward date.

### Fetcher output (canonical for all sources)

| Column | Type | Notes |
|---|---|---|
| `date` | `pd.Timestamp` | Start of reporting period (first of month for monthly series) |
| `value` | `float` | Native units — not rebased |
| `series_id` | `str` | Source series identifier |
| `source` | `str` | `fred`, `bls`, `eia`, or `bea` |
| `fetched_at` | `pd.Timestamp` | UTC timestamp of fetch |

### DRI panel (`data/derived/dri_panel.csv`)

| Column | Notes |
|---|---|
| `date` | Month-start, Jan 2020–present |
| `dri` | DRI composite, Jan 2020 = 100 |
| `cpi` | Official CPI all items, Jan 2020 = 100 |
| `<component_id>` | One column per non-deferred component, Jan 2020 = 100 |

**Date range:** Starts at the first month where all in-index components have data. Ends at the last month where all BLS in-index components have data (BLS monthly releases control the current end of the index).

### Published CSVs (`data/published/`)

| File | Columns | Datawrapper chart type |
|---|---|---|
| `dri_vs_cpi.csv` | `Date, Dead Reckoning Index, Official CPI` | Line chart |
| `dri_components.csv` | `Date, <Component Label>, …` (wide) | Stacked area |
| `dri_component_table.csv` | `Component, Data as of, Latest, MoM %, YoY %, Weight` | Table |
| `dri_metadata.csv` | `component_id, series_id, cadence, data_as_of, age_days, status, carried_forward, in_index, weight` | Reference / annotation |

Column headers in `dri_vs_cpi.csv`, `dri_components.csv`, and `dri_component_table.csv` are stable identifiers — changing them breaks live Datawrapper templates. `dri_metadata.csv` is for internal use and future chart annotations.

---

## Failure modes

**Series exceeds `hard_fail_days`**: `ValidationError` raised; pipeline aborts. Check whether the BLS/FRED/EIA source is still published. If retired, find the replacement ID before re-running.

**Series between `expected_lag_days` and `hard_fail_days`**: Warning logged; pipeline continues. If `carry_forward: true`, the last known value is held forward (up to one cadence period). If `carry_forward: false`, the component will be NaN for that month.

**API key rejected**: `FetchError` with a clear message. Confirm the key in `.env`.

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
