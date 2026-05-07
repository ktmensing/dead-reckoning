# Dead Reckoning Pipeline — Phase 1 Build (Claude Code Prompt)

> Save this file at the repo root as `BUILD_PROMPT.md`, then paste its contents (or the contents in chunks) into Claude Code in `~/code/dead-reckoning`. Tell Claude Code: *"Read BUILD_PROMPT.md and dead-reckoning-pipeline-roadmap.md, then execute Phase 1 as specified."*

---

## Project context

You're working in `~/code/dead-reckoning`, a fresh repo for the **Dead Reckoning System** — a macro-aware indicator dashboard built around four instruments (Dead Reckoning Index, Behavior Layer, Mercury Reading, Altitude Index) sourced from federal data (FRED, BLS, BEA, EIA) and visualized via Datawrapper.

The architectural keystone is **Datawrapper's external data linking**: chart templates point at CSV URLs hosted on this repo's GitHub `main` branch, and auto-refresh from those URLs. That means the pipeline writes CSVs; charts update themselves. Once chart templates exist, the per-week work is just running the pipeline.

The full architecture is documented in `dead-reckoning-pipeline-roadmap.md` at the repo root. **Read that file before doing any work.** It specifies project layout, fetch/transform/publish architecture, instrument-level data sources, and the full phased build sequence. This prompt covers Phase 1 only; phases 2–6 are out of scope for this session.

## What's already done

- Repo initialized with the structure from the roadmap (`src/`, `data/{raw,derived,published}/`, `charts/`, `scripts/`, `tests/`, `config/` will need to be created).
- Python venv at `.venv/` with: `pandas requests fredapi python-dotenv pyyaml duckdb pytrends pdfplumber`.
- `requirements.txt` written.
- `.gitignore` excludes `.env`, `.venv/`, `data/raw/`, `__pycache__/`, `*.pyc`, `.DS_Store`.
- API keys filled in at `.env` for FRED, BLS, BEA, EIA.
- Fetcher spike already in place under `src/fetch/`:
  - `src/fetch/__init__.py` — env loading, `FetchError`, `require_env`, canonical paths
  - `src/fetch/fred.py` — FRED observations endpoint, hand-rolled with `requests`
  - `src/fetch/bls.py` — BLS v2 with single-series `fetch()` and batch `fetch_batch()`
- `scripts/smoke_test_fetchers.py` exists and exits 0 against live APIs.

**Read these existing files before adding anything.** They establish the conventions every other module should follow.

## Scope of this session — Phase 1 only

You will build the **DRI Price Layer end-to-end** so the pipeline can be run with a single command and produce Datawrapper-ready CSVs in `data/published/`.

Concretely, this session is done when:

1. `config/series.yaml` defines all DRI components with their source IDs, weights, and metadata.
2. `src/fetch/bea.py` and `src/fetch/eia.py` exist, following the same shape as `fred.py` (single fetch entry point, parse/request split, FetchError on failure, CSV cache to `data/raw/`).
3. `src/validate.py` checks freshness, non-empty, range plausibility per series.
4. `src/transform/dri.py` computes the DRI Price Layer composite from fetched components — rebased to Jan 2020 = 100, weighted, with a parallel CPI series for comparison.
5. `src/store.py` provides simple CSV persistence to `data/derived/`.
6. `src/publish/datawrapper_csv.py` writes 3–4 Datawrapper-shaped CSVs to `data/published/`:
   - `dri_vs_cpi.csv` — headline line chart
   - `dri_components.csv` — component contributions (long format suitable for stacked area)
   - `dri_component_table.csv` — current values, MoM, YoY, weight (for the table chart)
7. `scripts/run_weekly.py` orchestrates the full pipeline: fetch → validate → transform → persist → publish. Produces non-empty, plausibly-valued CSVs in `data/published/`.
8. `Makefile` provides `make fetch`, `make build`, `make publish`, `make all`, `make smoke`.
9. `tests/` has fixture-based unit tests for parsers, transforms, and publish-shape checks. `pytest` passes.
10. `README.md` documents how to run the pipeline, how to add a new series, and the canonical schema.

## Hard scope boundaries — do NOT do these in this session

- **Do not** build the Mercury Reading transform (Phase 2).
- **Do not** build the Altitude Index (Phase 3).
- **Do not** build DRI-B Behavior Layer fetchers or PDF scrapers (Phase 4).
- **Do not** set up GitHub Actions automation (Phase 5).
- **Do not** build Datawrapper API integration for snapshot PNGs (Phase 6).
- **Do not** create or modify Datawrapper charts. The roadmap specifies that the human builds chart templates manually once the CSVs exist; that step is the user's, not yours.
- **Do not** push to a remote — the repo may not have a remote configured yet. Commit locally only.

## Code conventions (already established by the spike)

Match these or explain why you're deviating:

- **Type hints** on all public functions.
- **Module docstrings** that explain *why* a choice was made when it's non-obvious (see `src/fetch/bls.py` for the M13 / period-code rationale as the exemplar).
- **Split request from parse** so parsers can be unit-tested without network. Pattern: `_request_x()`, `_parse_x()`, public `fetch()`.
- **Fail loud, not soft.** Raise `FetchError` with a useful message rather than returning empty/partial data. The exception is `fetch_batch(strict=False)` which warns and continues.
- **Canonical DataFrame schema** for all fetcher outputs: `[date, value, series_id, source, fetched_at]`, sorted by date ascending, dates as `pd.Timestamp` at the start of the period.
- **Hand-rolled `requests` over wrapper libraries** where the wrapper would add a layer between us and the API behavior. `fredapi` is installed but not used; follow the pattern in `fred.py`.
- **CSV everywhere downstream of fetchers.** Don't reach for DuckDB or Parquet in Phase 1 — plain CSV is the right tool for both versionability and Datawrapper compatibility. (DuckDB comes in Phase 6 once the analytical surface is real.)
- **No emojis in code, comments, or docs.**

## DRI component series (from the roadmap, for `config/series.yaml`)

These are the DRI Price Layer inputs. Use them as-is unless you find a series ID is wrong (in which case stop and ask). Weights sum to 90%; the remaining 10% is reserved for quarterly inputs that will be added later — handle this in the transform by normalizing weights over what's actually present.

| Component | Weight | Source | Series ID | Notes |
|---|---|---|---|---|
| rent | 0.18 | (deferred) | — | Asking rent. Zillow ZORI or Apartment List. **Defer this component for now** — note in YAML as a placeholder; build the pipeline so it works without it. |
| mortgage_payment | 0.11 | derived | MSPUS + MORTGAGE30US | Compute monthly payment for 30yr fixed at 20% down. |
| food_at_home | 0.13 | bls | CUSR0000SAF11 | CPI food at home, SA |
| gas | 0.10 | eia | PET.EMM_EPMR_PTE_NUS_DPG.W | Weekly retail gasoline. Resample to monthly mean. |
| auto_insurance | 0.06 | bls | CUSR0000SETE | CPI motor vehicle insurance |
| cc_interest | 0.06 | fred | TERMCBCCALLNS | Bank credit card interest rate |
| dining_out | 0.07 | bls | CUSR0000SEFV | CPI food away from home |
| utilities | 0.03 | bls | CUSR0000SEHF | CPI energy services |
| used_cars | 0.04 | bls | CUSR0000SETA02 | CPI used cars and trucks |
| eggs | 0.03 | bls | APU0000708111 | Avg price eggs grade A large |
| home_insurance | 0.03 | bls | CUUR0000SEHD | CPI tenants and household insurance, NSA only |

Also fetch headline CPI for the comparison line: **`CUSR0000SA0`** (CPI all items, SA).

If any series ID returns empty or fails validation when you wire it up, **stop and report which one** — don't guess at a replacement.

## Build sequence with explicit checkpoints

Work in this order. After each checkpoint, run the verification command(s) listed; if they don't pass, fix before moving on.

### Step 1 — Read existing artifacts (no code yet)
- Read `dead-reckoning-pipeline-roadmap.md` end to end.
- Read `src/fetch/__init__.py`, `src/fetch/fred.py`, `src/fetch/bls.py`.
- Read `scripts/smoke_test_fetchers.py`.
- **Verify:** confirm you understand the canonical schema, the request/parse split pattern, and the FetchError convention. Note in your reply if any of those are unclear.

### Step 2 — Run the existing smoke test
- Activate the venv: `source .venv/bin/activate`.
- Run `python scripts/smoke_test_fetchers.py`.
- **Verify:** exit code 0, plausible values printed for FRED MORTGAGE30US, BLS CUSR0000SAF11, and the 3-series batch. If any fail, stop and report — likely an API key issue.

### Step 3 — Build `src/fetch/bea.py` and `src/fetch/eia.py`
- BEA API docs: https://apps.bea.gov/api/signup/index.cfm and https://apps.bea.gov/API/docs/index.htm
- EIA v2 API docs: https://www.eia.gov/opendata/documentation.php
- Match the pattern of `fred.py`. Single `fetch(series_id, ...)` public entry point. Cache to `data/raw/{source}/{series_id}.csv`.
- For EIA, the gas series ID is `PET.EMM_EPMR_PTE_NUS_DPG.W`. Verify it returns weekly data and resample appropriately at the transform layer (not in the fetcher).
- For BEA, no series is needed in Phase 1 — but build the fetcher with the same shape so it's ready for later phases. A trivial smoke test against any BEA series (e.g., NIPA Table 1.1.1, line 1 — real GDP) is sufficient.
- Add corresponding entries in `scripts/smoke_test_fetchers.py`.
- **Verify:** smoke test passes with all four sources (FRED, BLS, BEA, EIA).

### Step 4 — Write `config/series.yaml`
- Create the YAML file with all DRI components from the table above. Include weight, source, series_id, and notes for each.
- Mark `rent` explicitly as `deferred: true` so the transform layer skips it.
- Include a separate top-level `cpi_headline` entry pointing at `CUSR0000SA0`.
- **Verify:** the YAML loads without error and contains the expected keys (`pyyaml.safe_load`).

### Step 5 — Build `src/validate.py`
- Function `validate_series(df, series_id, max_age_days=60)` raising `ValidationError` (a new exception in `src/__init__.py` or `src/validate.py`) on failure.
- Checks: not empty, latest date within max_age_days, not all-null, optional per-series numeric range checks via config.
- **Verify:** unit tests in `tests/test_validate.py` cover each failure mode using fixture DataFrames.

### Step 6 — Build `src/store.py`
- `save_derived(name, df)` writes to `data/derived/{name}.csv`.
- `load_derived(name)` returns a DataFrame.
- Trivial; ~20 lines.
- **Verify:** roundtrip test in `tests/`.

### Step 7 — Build `src/transform/dri.py`
- Reads YAML config, takes a dict of `{component_id: DataFrame}`, returns a DataFrame with columns `[date, dri, cpi, *components]` rebased to Jan 2020 = 100.
- Resample everything to monthly first (use `.resample("MS").mean()` for weekly inputs like gas; `.last()` for monthly inputs already at month start).
- Normalize weights over components actually present — i.e., if `rent` is deferred, the remaining ten components' weights should sum to 1.0 after normalization. Document this behavior in the docstring.
- Compute the mortgage payment as a derived component: monthly payment for a 30-year fixed at the FRED median home price, with 20% down assumed. Standard amortization formula. Verify against a known case (e.g., $400k home at 7% rate ≈ $2,128/mo on the financed $320k).
- **Verify:** unit tests with fixture data confirm rebased values, weighted composite, normalization, and mortgage payment math.

### Step 8 — Build `src/publish/datawrapper_csv.py`
- Three writers, each producing a CSV in `data/published/`:
  - `publish_dri_vs_cpi(panel)` → wide format, columns `[Date, Dead Reckoning Index, Official CPI]`. Date as `YYYY-MM-DD` strings.
  - `publish_dri_components(panel)` → long format, columns `[Date, Component, Value]` (tidy data, easier for Datawrapper stacked area).
  - `publish_dri_component_table(panel, weights)` → wide, columns `[Component, Latest, MoM %, YoY %, Weight]`. One row per component.
- Column headers in the CSV become legend labels in Datawrapper — get these right.
- **Verify:** unit tests confirm the exact column shape of each output. Tests should fail if a column is renamed, because Datawrapper templates depend on these names.

### Step 9 — Build `scripts/run_weekly.py`
- Single entry point that orchestrates: load config → fetch all components (one BLS batch, one FRED loop, one EIA call) → validate each → transform → persist derived → publish published.
- Print a one-line summary at each stage. On failure, exit with non-zero status and a clear message.
- **Verify:** running `python scripts/run_weekly.py` produces non-empty CSVs in `data/published/` with at least 24 monthly rows of history (back to 2020) and plausible values.

### Step 10 — Hand-validate one value
- Open the latest BLS news release for CPI (https://www.bls.gov/news.release/cpi.nr0.htm).
- Compare your fetched `food_at_home` value for the latest month to the released figure. They should match.
- This is the moment that confirms the pipeline is producing real, correct numbers — not just CSV-shaped output.
- **Verify:** report the comparison in your final summary.

### Step 11 — Build the Makefile
- Targets:
  - `setup` — print env-check status (which keys are set, venv active).
  - `fetch` — run fetchers only, populate `data/raw/`.
  - `build` — run transform only, populate `data/derived/`.
  - `publish` — run publish only, populate `data/published/`.
  - `all` — full pipeline (calls `run_weekly.py`).
  - `smoke` — runs `scripts/smoke_test_fetchers.py`.
  - `test` — runs `pytest`.
  - `clean` — removes `data/derived/` and `data/published/` (but not `data/raw/`).
- **Verify:** `make all` runs the full pipeline successfully end-to-end.

### Step 12 — Write the README
- Sections: Setup, Running, Adding a series, Schema, Failure modes, What's not in this build (point at Phase 2+).
- Keep it tight. Code that's documented in docstrings doesn't need re-explanation in README.

### Step 13 — Final verification
- `pytest` passes.
- `python scripts/smoke_test_fetchers.py` exits 0.
- `python scripts/run_weekly.py` exits 0 and writes ≥3 CSVs to `data/published/`.
- `make all` succeeds from a clean state (`make clean && make all`).
- `git status` shows the new files and the populated `data/derived/` and `data/published/`. No `data/raw/` entries (gitignored).
- Hand-validation comparison is documented.
- Commit with a message like: `Phase 1: DRI Price Layer end-to-end`.

## Stop conditions — when to ask the user instead of proceeding

- **A series ID returns empty or unexpected data.** Don't guess at a replacement — report which series, what error, and what you tried.
- **A weight or methodology choice is ambiguous.** The roadmap is the source of truth for weights; if it conflicts with `config/series.yaml`, raise the conflict.
- **An API key is missing or rejected.** Stop, name the key, point at `.env.example`.
- **A test fails and the fix is non-obvious.** Report the failure verbatim. Don't paper over with `try/except`.
- **A scope question arises that touches Phase 2+ work.** Note it in a `TODO.md` and continue Phase 1.
- **Datawrapper integration questions.** Datawrapper chart-building is not your job in this session.

## Tone and reporting

The repo's owner is a peer, not a learner. When you write code comments, docstrings, or the README:

- No "great question," no "I'll be happy to help," no preamble.
- Substantive, opinionated, terse where possible.
- When a choice is non-obvious, explain *why* (one or two sentences). When it's obvious, don't.
- No emojis in code or docs.
- Don't restate what the code does in a comment that paraphrases the code. Comments explain *why*, not *what*.

In your final session summary to the user:

- Confirm each verification step passed (or didn't).
- Quote the hand-validation comparison (your fetched value vs. the BLS release value).
- List the files you created.
- Flag anything you couldn't complete and why.
- Note any TODOs or scope questions you deferred.

## A note on scope creep

The temptation in this kind of build is to add "just one more thing" — a CLI argument parser, a logging framework, a more elegant config schema. Resist. Phase 1's job is to produce CSVs that Datawrapper can consume, end to end, reliably. Polish is Phase 6. If you find yourself building infrastructure that isn't required by the verification checkpoints, stop and ask.

The diagnostic test of whether Phase 1 is complete: after running `make all`, the user can build a Datawrapper chart pointed at `https://raw.githubusercontent.com/<user>/dead-reckoning/main/data/published/dri_vs_cpi.csv`, see real data, and the chart will refresh next week when the user runs `make all` again. If that works, Phase 1 is done.
