"""
backfill_abi_bankruptcies.py
============================
Scrapes U.S. Courts quarterly bankruptcy filing statistics (2007–present)
and writes four nonbusiness (individual consumer) filing series to the DRI-B
manual CSV at data/raw/manual/abi_bankruptcies.csv.

Run once to backfill history. Re-run quarterly to add new data — already-
scraped quarters are skipped via a local PDF cache.

Usage (from project root):
    pip install pdfplumber requests beautifulsoup4
    python scripts/backfill_abi_bankruptcies.py

Output CSV format (matches series.yaml manual_csv contract):
    date,value,series_id
    2024-12-01,122182,abi_bankruptcies_total
    2024-12-01,74460,abi_bankruptcies_ch7
    2024-12-01,117,abi_bankruptcies_ch11
    2024-12-01,47605,abi_bankruptcies_ch13

Four series, same file — one row per series per quarter:

    abi_bankruptcies_total  Nonbusiness All Chapters. Primary DRI-B signal.
    abi_bankruptcies_ch7    Chapter 7 (liquidation). Acute consumer distress —
                            households giving up assets and discharging debt.
    abi_bankruptcies_ch13   Chapter 13 (wage-earner reorganization). Anticipatory
                            distress — households still trying to hold on, often
                            protecting a home or car. Tends to lead Ch.7 in stress
                            cycles; Ch.7/Ch.13 ratio is a stress-intensity read.
    abi_bankruptcies_ch11   Nonbusiness Chapter 11 (individual reorganization).
                            Used by filers above the Ch.13 income/debt thresholds —
                            upper-income households in distress. Tiny absolute count
                            (~100-200/quarter) but percentage-change trend supports
                            K-shape analysis: rising Ch.11 individual while Ch.7/13
                            are flat signals stress moving up the income distribution.

Date convention: first day of the quarter-end month
    Q1 (March 31)    → YYYY-03-01
    Q2 (June 30)     → YYYY-06-01
    Q3 (September 30)→ YYYY-09-01
    Q4 (December 31) → YYYY-12-01

Data source: U.S. Courts Bankruptcy Filing Statistics
    https://www.uscourts.gov/data-news/reports/statistical-reports/bankruptcy-filings-statistics

Update cadence: quarterly (data typically posted 4–6 weeks after quarter end).
Next release windows: ~May, ~Aug, ~Nov, ~Feb.
"""

from __future__ import annotations

import csv
import io
import re
import time
from pathlib import Path

import pdfplumber
import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = "https://www.uscourts.gov"
INDEX_URL = (
    f"{BASE_URL}/data-news/reports/statistical-reports/bankruptcy-filings-statistics"
)
OUTPUT_CSV = Path("data/raw/manual/abi_bankruptcies.csv")
PDF_CACHE_DIR = Path("data/raw/manual/.abi_pdf_cache")
REQUEST_DELAY = 0.5  # seconds between requests — be polite

# Series extracted from Table F-2 Quarterly, "Total" row, Nonbusiness columns
# Column index positions (0-based within the numeric fields on the Total row):
#   10 = Nonbusiness All Chapters
#   11 = Nonbusiness Chapter 7
#   12 = Nonbusiness Chapter 11
#   13 = Nonbusiness Chapter 13
SERIES = [
    ("abi_bankruptcies_total", 10),
    ("abi_bankruptcies_ch7",   11),
    ("abi_bankruptcies_ch11",  12),
    ("abi_bankruptcies_ch13",  13),
]

MONTH_TO_NUM = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def slug_to_date(slug: str) -> str:
    """Convert page slug to first-of-month date string.

    'march-2023-quarterly-bankruptcy-filings' → '2023-03-01'
    """
    parts = slug.split("-")
    month_num = MONTH_TO_NUM.get(parts[0].lower())
    year = parts[1]
    if not month_num or not year.isdigit():
        raise ValueError(f"Cannot parse date from slug: {slug!r}")
    return f"{year}-{month_num}-01"


def get_all_quarter_slugs() -> list[tuple[str, str]]:
    """Scrape the index page and return (slug, full_page_url) for every quarter."""
    r = requests.get(INDEX_URL, timeout=30, headers={"User-Agent": "bankruptcy-dri-b-scraper/1.0"})
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    seen: set[str] = set()
    results: list[tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        href: str = a["href"]
        if "quarterly-bankruptcy-filings" in href and href not in seen:
            seen.add(href)
            slug = href.rstrip("/").split("/")[-1]
            full_url = BASE_URL + href if href.startswith("/") else href
            results.append((slug, full_url))
    return results


def find_quarterly_pdf_url(page_url: str) -> str | None:
    """Fetch a quarterly filing page and return the F-2 Quarterly PDF URL.

    Handles three naming eras:
        2007–2012:  *_f23.pdf    (Three-Month period)
        2012–2016:  *_f2q*.pdf   (Quarterly)
        2016+:      *bf_f2.3_*.pdf
    """
    r = requests.get(page_url, timeout=30, headers={"User-Agent": "bankruptcy-dri-b-scraper/1.0"})
    if r.status_code == 404:
        return None
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    for a in soup.find_all("a", href=True):
        href: str = a["href"]
        fname = href.split("/")[-1].lower()
        if fname.endswith(".pdf") and re.search(r"f2[3q]|f2\.3", fname):
            return BASE_URL + href if href.startswith("/") else href
    return None


def fetch_pdf(pdf_url: str, cache_key: str) -> bytes:
    """Download PDF, caching locally to avoid re-downloading on reruns."""
    PDF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = PDF_CACHE_DIR / f"{cache_key}.pdf"
    if cache_path.exists():
        return cache_path.read_bytes()
    r = requests.get(
        pdf_url, timeout=60, headers={"User-Agent": "bankruptcy-dri-b-scraper/1.0"}
    )
    r.raise_for_status()
    cache_path.write_bytes(r.content)
    return r.content


def parse_all_series(pdf_bytes: bytes) -> dict[str, int]:
    """Extract all four nonbusiness series from the F-2 Quarterly PDF.

    The Total row contains 14 numeric fields:
        [0]  Total All Chapters
        [1]  Total Ch 7
        [2]  Total Ch 11
        [3]  Total Ch 13
        [4]  Total Other
        [5]  Business All Chapters
        [6]  Business Ch 7
        [7]  Business Ch 11
        [8]  Business Ch 13
        [9]  Business Other
        [10] Nonbusiness All Chapters  → abi_bankruptcies_total
        [11] Nonbusiness Ch 7          → abi_bankruptcies_ch7
        [12] Nonbusiness Ch 11         → abi_bankruptcies_ch11
        [13] Nonbusiness Ch 13         → abi_bankruptcies_ch13

    Column structure verified consistent across all PDF naming eras (2007–2026).
    """
    pdf = pdfplumber.open(io.BytesIO(pdf_bytes))
    text = pdf.pages[0].extract_text() or ""
    for line in text.splitlines():
        # Matches "TOTAL..." (2007 era) and "TOTAL " / "Total " (2010+)
        if re.match(r"^TOTAL[\.\s]+[\d,]", line.strip(), re.IGNORECASE):
            nums = [int(n.replace(",", "")) for n in re.findall(r"[\d,]+", line)]
            if len(nums) >= 14:
                return {series_id: nums[idx] for series_id, idx in SERIES}
    raise ValueError("Could not find Total row in PDF page 1")


def load_existing_csv(path: Path) -> dict[tuple[str, str], int]:
    """Load existing CSV into {(date, series_id): value} dict."""
    if not path.exists():
        return {}
    existing: dict[tuple[str, str], int] = {}
    with path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("value") and row["value"].strip():
                try:
                    key = (row["date"], row["series_id"])
                    existing[key] = int(float(row["value"]))
                except (ValueError, KeyError):
                    pass
    return existing


def write_csv(path: Path, data: dict[tuple[str, str], int]) -> None:
    """Write {(date, series_id): value} dict to CSV, sorted chronologically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Sort by date first, then series_id for consistent ordering
    rows = sorted(data.items(), key=lambda x: (x[0][0], x[0][1]))
    with path.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "value", "series_id"])
        for (date, series_id), value in rows:
            writer.writerow([date, value, series_id])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 60)
    print("DRI-B Bankruptcy Backfill — U.S. Courts F-2 Quarterly")
    print("=" * 60)

    # Load what we already have
    existing = load_existing_csv(OUTPUT_CSV)
    print(f"\nExisting rows in CSV: {len(existing)}")

    # Discover all available quarters from the index
    print("\nFetching quarter index from uscourts.gov...")
    quarters = get_all_quarter_slugs()
    quarters_sorted = sorted(quarters, key=lambda x: x[0])  # chronological
    print(f"Found {len(quarters_sorted)} quarters: {quarters_sorted[0][0]} → {quarters_sorted[-1][0]}")

    new_data: dict[tuple[str, str], int] = dict(existing)
    skipped = 0
    errors: list[tuple[str, str]] = []

    print("\nProcessing quarters:")
    for slug, page_url in quarters_sorted:
        try:
            date = slug_to_date(slug)
        except ValueError as e:
            print(f"  SKIP (bad slug): {slug} — {e}")
            continue

        # Skip if all four series are already present for this quarter
        existing_series = {sid for (d, sid), _ in new_data.items() if d == date}
        all_series_ids = {sid for sid, _ in SERIES}
        if existing_series >= all_series_ids:
            skipped += 1
            continue

        # Find the quarterly PDF
        try:
            pdf_url = find_quarterly_pdf_url(page_url)
            time.sleep(REQUEST_DELAY)
        except Exception as e:
            print(f"  ERROR {date}: page fetch failed — {e}")
            errors.append((date, f"page fetch: {e}"))
            continue

        if pdf_url is None:
            print(f"  SKIP {date}: no F-2 quarterly PDF on page")
            errors.append((date, "no PDF link found"))
            continue

        # Download and parse
        try:
            pdf_bytes = fetch_pdf(pdf_url, slug)
            parsed = parse_all_series(pdf_bytes)
            for series_id, value in parsed.items():
                new_data[(date, series_id)] = value
            fname = pdf_url.split("/")[-1]
            total = parsed["abi_bankruptcies_total"]
            ch7   = parsed["abi_bankruptcies_ch7"]
            ch11  = parsed["abi_bankruptcies_ch11"]
            ch13  = parsed["abi_bankruptcies_ch13"]
            print(f"  {date}: total={total:>8,}  ch7={ch7:>8,}  ch11={ch11:>5,}  ch13={ch13:>8,}  [{fname}]")
            time.sleep(REQUEST_DELAY)
        except Exception as e:
            print(f"  ERROR {date}: {e}")
            errors.append((date, str(e)))

    # Write results
    write_csv(OUTPUT_CSV, new_data)
    newly_added = len(new_data) - len(existing)

    print(f"\n{'=' * 60}")
    print(f"Complete.")
    print(f"  Quarters processed: {len(quarters_sorted)}")
    print(f"  Already in CSV (skipped): {skipped}")
    print(f"  Newly added rows: {newly_added}  ({newly_added // len(SERIES)} quarters × {len(SERIES)} series)")
    print(f"  Errors: {len(errors)}")
    print(f"  Total rows in CSV: {len(new_data)}")
    print(f"  Output: {OUTPUT_CSV.resolve()}")

    if errors:
        print(f"\nErrors to investigate:")
        for date, msg in errors:
            print(f"  {date}: {msg}")

    if newly_added == 0 and not errors:
        print("\nCSV is up to date. Run again after next quarter's release.")

    print()


if __name__ == "__main__":
    main()
