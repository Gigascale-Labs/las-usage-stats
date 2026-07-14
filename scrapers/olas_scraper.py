"""
Task 5: Olas (Autonolas) daily active agent counts.

Confirmed live against the real subgraph proxy endpoints (tested by hand
before writing this): the GraphQL schema in the original brief is
accurate --

    dailyActiveMultisigs_collection(
      where: { and: [{dayTimestamp_gt: $t0}, {dayTimestamp_lt: $t1}] }
      orderBy: dayTimestamp, orderDirection: desc
    ) { id count dayTimestamp }

Chain scope (per user decision): Gnosis and Mode only. Those two are
served through Olas's own free, no-key-required proxy at
api.subgraph.autonolas.tech/api/proxy/<name>. Base, Optimism, Celo and
Ethereum are only reachable through The Graph's decentralized network,
which requires a paid API key -- out of scope for now.

Output:
  data_outputs/olas_daily_active_agents.csv
      date, gnosis_count, mode_count, total_count
"""
from __future__ import annotations

import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from http_utils import make_session, polite_post  # noqa: E402
from parsers.timeseries import write_csv  # noqa: E402

ENDPOINTS = {
    "gnosis": "https://api.subgraph.autonolas.tech/api/proxy/service-registry-gnosis",
    "mode": "https://api.subgraph.autonolas.tech/api/proxy/service-registry-mode",
}

QUERY = """
query DailyActiveMultisigs($timestamp_gt: Int!, $timestamp_lt: Int!, $first: Int!, $skip: Int!) {
  dailyActiveMultisigs_collection(
    where: { and: [{ dayTimestamp_gt: $timestamp_gt }, { dayTimestamp_lt: $timestamp_lt }] }
    orderBy: dayTimestamp
    orderDirection: desc
    first: $first
    skip: $skip
  ) {
    id
    count
    dayTimestamp
  }
}
"""

OUT_DIR = Path(__file__).resolve().parent.parent / "data_outputs"
PAGE_SIZE = 1000


def fetch_chain_series(session, chain: str, url: str, since: date) -> dict[str, int]:
    """Fetch the full daily-active-multisig history for one chain, keyed by ISO date."""
    t0 = int(datetime.combine(since, datetime.min.time(), tzinfo=timezone.utc).timestamp())
    t1 = int(datetime.now(timezone.utc).timestamp()) + 86400
    results: dict[str, int] = {}
    skip = 0
    while True:
        variables = {"timestamp_gt": t0, "timestamp_lt": t1, "first": PAGE_SIZE, "skip": skip}
        resp = polite_post(session, url, json={"query": QUERY, "variables": variables}, sleep=0.3)
        if resp.status_code != 200:
            print(f"  [{chain}] HTTP {resp.status_code}, stopping", file=sys.stderr)
            break
        payload = resp.json()
        if "errors" in payload:
            print(f"  [{chain}] GraphQL errors: {payload['errors']}", file=sys.stderr)
            break
        rows = payload.get("data", {}).get("dailyActiveMultisigs_collection", [])
        if not rows:
            break
        for row in rows:
            d = datetime.fromtimestamp(int(row["dayTimestamp"]), tz=timezone.utc).date().isoformat()
            results[d] = row["count"]
        if len(rows) < PAGE_SIZE:
            break
        skip += PAGE_SIZE
        time.sleep(0.2)
    return results


def main() -> None:
    session = make_session()
    since = date.today() - timedelta(days=365 * 3)  # subgraph history is bounded anyway; grab a generous window

    per_chain: dict[str, dict[str, int]] = {}
    for chain, url in ENDPOINTS.items():
        print(f"Fetching Olas daily active agents for {chain}...")
        series = fetch_chain_series(session, chain, url, since)
        print(f"  {chain}: {len(series)} days")
        per_chain[chain] = series

    all_dates = sorted(set().union(*[set(s.keys()) for s in per_chain.values()])) if per_chain else []
    rows = []
    for d in all_dates:
        gnosis = per_chain.get("gnosis", {}).get(d, 0)
        mode = per_chain.get("mode", {}).get(d, 0)
        rows.append({"date": d, "gnosis_count": gnosis, "mode_count": mode, "total_count": gnosis + mode})

    write_csv(OUT_DIR / "olas_daily_active_agents.csv", rows)
    print(f"Wrote {len(rows)} daily rows to olas_daily_active_agents.csv")


if __name__ == "__main__":
    main()
