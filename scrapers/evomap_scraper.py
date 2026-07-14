"""
Task 2: EvoMap Hub activity snapshot.

The brief originally asked to `git clone --filter=blob:none` an "EvoMap
asset hub" repo and parse commit history for active users. That doesn't
match reality, confirmed by inspecting EvoMap's own docs and repo:

  * The EvoMap Hub (evomap.ai) is a hosted API service, addressed via
    A2A_HUB_URL / A2A_NODE_ID -- not a git repository. There is nothing
    to clone.
  * The GEP event log (.evolver/gep/events.jsonl) is local runtime state
    that EvoMap's own tooling git-ignores by default. It is never
    committed, so even parsing a project's own git history would find
    zero GEP events there.

Per user decision, this script instead queries the Hub's real public API
directly. Confirmed live: GET https://evomap.ai/a2a/stats returns:

    {"total_assets": ..., "promoted_assets": ..., "candidate_assets": ...,
     "promotion_rate": ..., "total_calls": ..., "total_views": ...,
     "today_calls": ..., "last_24h_calls": ..., "total_reuses": ...,
     "total_nodes": ..., "matched_bounties": ...}

`total_nodes` is the closest real proxy to "active users" the Hub
exposes network-wide (it is not scoped to a single "asset hub" repo --
EvoMap doesn't have per-repo hubs, just the one network).

Same caveat as ClawHub: this is a live snapshot with no historical
endpoint, so the time series only starts accumulating from the first
run of this script onward.

Output:
  data_outputs/evomap_hub_snapshots.csv
      date, total_nodes, total_assets, promoted_assets, total_calls, total_reuses
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from http_utils import make_session, polite_get  # noqa: E402
from parsers.timeseries import append_snapshot_csv  # noqa: E402

STATS_URL = "https://evomap.ai/a2a/stats"
OUT_DIR = Path(__file__).resolve().parent.parent / "data_outputs"


def main() -> None:
    session = make_session()
    print(f"Fetching {STATS_URL} ...")
    resp = polite_get(session, STATS_URL, sleep=0.3)
    if resp.status_code != 200:
        print(f"EvoMap Hub stats endpoint returned {resp.status_code}; aborting.", file=sys.stderr)
        return

    data = resp.json()
    snapshot = {
        "date": date.today().isoformat(),
        "total_nodes": data.get("total_nodes"),
        "total_assets": data.get("total_assets"),
        "promoted_assets": data.get("promoted_assets"),
        "total_calls": data.get("total_calls"),
        "total_reuses": data.get("total_reuses"),
    }
    append_snapshot_csv(OUT_DIR / "evomap_hub_snapshots.csv", snapshot)
    print(f"  appended snapshot: {snapshot}")


if __name__ == "__main__":
    main()
