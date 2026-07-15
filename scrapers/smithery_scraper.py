"""
Smithery MCP server registry snapshot.

Smithery (smithery.ai) is one of the main MCP server registries, similar
in spirit to ClawHub but for MCP servers rather than OpenClaw skills.
Its registry API is public and needs no API key for a basic paginated
read: GET https://api.smithery.ai/servers?page=1&pageSize=1 returns a
`pagination.totalCount` field with the live server count (confirmed by
testing -- 7,083 servers as of 2026-07-15).

Like ClawHub/EvoMap, this is a live snapshot with no historical
endpoint, so the time series only starts accumulating from the first
run of this script onward.

Output:
  data_outputs/smithery_registry_snapshots.csv
      date, total_servers
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from http_utils import make_session, polite_get  # noqa: E402
from parsers.timeseries import append_snapshot_csv  # noqa: E402

REGISTRY_URL = "https://api.smithery.ai/servers"
OUT_DIR = Path(__file__).resolve().parent.parent / "data_outputs"


def main() -> None:
    session = make_session()
    print(f"Fetching {REGISTRY_URL} ...")
    resp = polite_get(session, REGISTRY_URL, params={"page": 1, "pageSize": 1}, sleep=0.3)
    if resp.status_code != 200:
        print(f"Smithery registry endpoint returned {resp.status_code}; aborting.", file=sys.stderr)
        return

    data = resp.json()
    total = data.get("pagination", {}).get("totalCount")
    if total is None:
        print("Smithery response had no pagination.totalCount; aborting.", file=sys.stderr)
        return

    snapshot = {"date": date.today().isoformat(), "total_servers": total}
    append_snapshot_csv(OUT_DIR / "smithery_registry_snapshots.csv", snapshot)
    print(f"  appended snapshot: {snapshot}")


if __name__ == "__main__":
    main()
