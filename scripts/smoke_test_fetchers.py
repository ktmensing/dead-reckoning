"""
Smoke test for all four data fetchers.

Runs against live APIs with real keys. Exits 0 if all pass, non-zero on failure.
Not a pytest suite — intended for quick manual verification and Make targets.
"""

import sys
from pathlib import Path

# Allow running from repo root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.fetch import fred, bls, bea, eia


def check(label: str, df, expected_series_id: str = None) -> None:
    assert not df.empty, f"{label}: empty DataFrame"
    assert df["value"].notna().any(), f"{label}: all values null"
    if expected_series_id:
        assert df["series_id"].iloc[0] == expected_series_id, (
            f"{label}: series_id mismatch"
        )
    latest = df.sort_values("date").iloc[-1]
    print(f"  {label}: {len(df)} rows, latest {latest['date'].date()} = {latest['value']:.4f}")


def run_fred_tests() -> None:
    print("FRED:")
    df = fred.fetch("MORTGAGE30US")
    check("MORTGAGE30US (30yr mortgage rate)", df, "MORTGAGE30US")

    df = fred.fetch("MSPUS")
    check("MSPUS (median home price)", df, "MSPUS")

    df = fred.fetch("TERMCBCCALLNS")
    check("TERMCBCCALLNS (CC interest rate)", df, "TERMCBCCALLNS")


def run_bls_tests() -> None:
    print("BLS:")
    df = bls.fetch("CUSR0000SAF11")
    check("CUSR0000SAF11 (food at home CPI)", df, "CUSR0000SAF11")

    print("  BLS batch:")
    batch_ids = [
        "CUSR0000SETE",    # auto insurance
        "CUSR0000SEFV",    # dining out
        "CUSR0000SEHF",    # utilities
    ]
    results = bls.fetch_batch(batch_ids)
    assert len(results) == 3, f"BLS batch: expected 3 series, got {len(results)}"
    for sid, df in results.items():
        check(f"  {sid}", df, sid)


def run_bea_tests() -> None:
    print("BEA:")
    # NIPA Table 1.1.1, line 1 = Real GDP (annual percent change)
    df = bea.fetch("NIPA:T10101:1:A")
    check("NIPA:T10101:1:A (real GDP % change, annual)", df, "NIPA:T10101:1:A")


def run_eia_tests() -> None:
    print("EIA:")
    df = eia.fetch("PET.EMM_EPMR_PTE_NUS_DPG.W")
    check("PET.EMM_EPMR_PTE_NUS_DPG.W (weekly gas price)", df, "PET.EMM_EPMR_PTE_NUS_DPG.W")
    # Sanity check: gas prices should be between $0.50 and $10
    latest_val = df.sort_values("date").iloc[-1]["value"]
    assert 0.5 < latest_val < 10.0, f"EIA gas price implausible: {latest_val}"


def main() -> None:
    failed = []
    for name, fn in [
        ("FRED", run_fred_tests),
        ("BLS", run_bls_tests),
        ("BEA", run_bea_tests),
        ("EIA", run_eia_tests),
    ]:
        try:
            fn()
        except Exception as exc:
            print(f"  FAIL ({name}): {exc}")
            failed.append(name)

    if failed:
        print(f"\nFAILED: {', '.join(failed)}")
        sys.exit(1)
    else:
        print("\nAll fetchers OK.")
        sys.exit(0)


if __name__ == "__main__":
    main()
