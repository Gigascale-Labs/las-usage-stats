"""
Robinhood Chain daily transactions + address/transaction totals.

Robinhood Chain is Robinhood's own Layer 2 (Arbitrum Orbit, settling on
Ethereum), live on mainnet since 2026-07-01. It runs the standard
Blockscout explorer stack at https://robinhoodchain.blockscout.com, whose
public v2 REST API needs no key:

    GET /api/v2/stats/charts/transactions
        -> true daily history of transaction counts (per Blockscout's
           documented v2 chart schema: {"chart_data": [{"date", "tx_count"}, ...]})
    GET /api/v2/stats
        -> current chain-wide totals (total_addresses, total_transactions,
           total_blocks, per Blockscout's documented v2 stats schema)

Caveat: this sandbox's network policy blocks Blockscout instances entirely
(confirmed against unrelated instances too, e.g. eth.blockscout.com -- not
Robinhood-specific), so the exact live payload shape could not be hand-
verified against curl before writing this, unlike the other scrapers in
this repo. Field names below follow Blockscout's documented v2 API. Both
fetch functions are defensive about missing/renamed fields and abort with
a clear stderr message rather than crashing run_all.py, so if the live
schema differs slightly this fails loud on the first real (CI) run instead
of writing bad data.

Blockscout's public API has no historical endpoint for daily active
addresses, only current totals -- so total/active address counts are
recorded as a snapshot (like Smithery), while transaction counts get a
true daily history (like Olas).

Output:
  data_outputs/robinhood_chain_daily_transactions.csv
      date, transactions_count
  data_outputs/robinhood_chain_snapshots.csv
      date, total_addresses, total_transactions, total_blocks
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from http_utils import make_session, polite_get  # noqa: E402
from parsers.timeseries import append_snapshot_csv, write_csv  # noqa: E402

BASE_URL = "https://robinhoodchain.blockscout.com"
STATS_URL = f"{BASE_URL}/api/v2/stats"
TX_CHART_URL = f"{BASE_URL}/api/v2/stats/charts/transactions"
OUT_DIR = Path(__file__).resolve().parent.parent / "data_outputs"


def fetch_daily_transactions(session) -> list[dict]:
    """Fetch the true daily transaction-count history."""
    resp = polite_get(session, TX_CHART_URL, sleep=0.3)
    if resp.status_code != 200:
        print(f"Robinhood Chain tx chart endpoint returned {resp.status_code}; skipping.", file=sys.stderr)
        return []

    payload = resp.json()
    chart_data = payload.get("chart_data")
    if chart_data is None:
        print(
            f"Robinhood Chain tx chart response had no 'chart_data' field; "
            f"Blockscout schema may have changed. Top-level keys: {sorted(payload.keys())}. Skipping.",
            file=sys.stderr,
        )
        return []

    rows = []
    skipped = 0
    for point in chart_data:
        d = point.get("date")
        count = point.get("tx_count")
        if d is None or count is None:
            skipped += 1
            continue
        rows.append({"date": d, "transactions_count": count})
    rows.sort(key=lambda r: r["date"])

    if not rows and chart_data:
        print(
            f"Robinhood Chain tx chart returned {len(chart_data)} points but none had both "
            f"'date' and 'tx_count'; Blockscout schema may have changed. Sample point: "
            f"{chart_data[0]!r}. Skipping.",
            file=sys.stderr,
        )
    elif skipped:
        print(f"Robinhood Chain tx chart: skipped {skipped} malformed point(s).", file=sys.stderr)

    return rows


def fetch_snapshot(session) -> dict | None:
    """Fetch current chain-wide totals as a single dated snapshot."""
    resp = polite_get(session, STATS_URL, sleep=0.3)
    if resp.status_code != 200:
        print(f"Robinhood Chain stats endpoint returned {resp.status_code}; skipping.", file=sys.stderr)
        return None

    data = resp.json()
    fields = ("total_addresses", "total_transactions", "total_blocks")
    if any(data.get(f) is None for f in fields):
        print(
            f"Robinhood Chain stats response missing one of {fields}; "
            "Blockscout schema may have changed. Skipping.",
            file=sys.stderr,
        )
        return None

    return {"date": date.today().isoformat(), **{f: data[f] for f in fields}}


def main() -> None:
    session = make_session()

    print(f"Fetching {TX_CHART_URL} ...")
    tx_rows = fetch_daily_transactions(session)
    if tx_rows:
        write_csv(OUT_DIR / "robinhood_chain_daily_transactions.csv", tx_rows)
        print(f"  wrote {len(tx_rows)} daily rows to robinhood_chain_daily_transactions.csv")

    print(f"Fetching {STATS_URL} ...")
    snapshot = fetch_snapshot(session)
    if snapshot:
        append_snapshot_csv(OUT_DIR / "robinhood_chain_snapshots.csv", snapshot)
        print(f"  appended snapshot: {snapshot}")


if __name__ == "__main__":
    main()
