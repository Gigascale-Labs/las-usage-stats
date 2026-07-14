"""
Task 1: ClawHub skills registry.

ClawHub (https://clawhub.ai) is the OpenClaw skill registry: Convex backend,
TanStack Start frontend, public HTTP API under /api/v1/. Confirmed live at
time of writing: GET /api/v1/skills returns paginated skill records shaped
like:

    {
      "slug": ..., "createdAt": <ms epoch>, "updatedAt": <ms epoch>,
      "stats": {"downloads": int, "installs": int, "stars": int, ...}
    }

Two important caveats, discovered by testing the live API rather than
assuming from the brief:

1. There is no field called `installsAllTime` -- the real field is
   `stats.installs` (all-time cumulative installs for that one skill).
2. The API only exposes current totals. There is no historical endpoint,
   so a genuine monthly install time series since inception does not
   exist anywhere and cannot be scraped. What *is* reconstructable from
   history is skill-publish dates (`createdAt`), which lets us build a
   real monthly series of "cumulative skills published". Installs can
   only be tracked as a snapshot from the day this script starts running
   forward -- each run appends one row to clawhub_installs_snapshots.csv.

Speed: the catalog is tens of thousands of skills and the API caps pages
at ~200 items with ~2-3s server-side latency per request no matter what --
a full walk takes several minutes. Two things were tried and ruled out as
faster alternatives (tested directly, not assumed):
  - The authenticated bulk-export endpoint (/api/v1/skills/export) returns
    a ZIP of one JSON file per skill and is rate-limited to 60 req/hour at
    ~250 skills/request -- slower than plain pagination at this catalog size.
  - A GitHub mirror repo (openclaw/skills) that used to archive the whole
    registry as flat files (one git clone away from a full local copy) has
    been taken down (404) -- likely pulled after the "ClawHavoc" malicious-
    skill campaign that hit ClawHub earlier in 2026.

So: plain pagination against the live API, but made incremental. The API
supports `sort=updated&order=desc`, so this script keeps a local cache
(data_outputs/.clawhub_skills_cache.json) and on every run after the first,
walks newest-updated-first and stops as soon as it reaches a skill it has
already cached with the same updatedAt -- everything older is guaranteed
unchanged. The first run still has to walk the entire catalog once (a few
minutes, one-time); every run after that only fetches what's new or
changed since last time, which is fast. This is the intended usage via
run_all.py on a cron, not a one-off interactive script.

Outputs:
  data_outputs/clawhub_skills_published_monthly.csv
      month, cumulative_skills_published   (real historical series)
  data_outputs/clawhub_installs_snapshots.csv
      date, total_skills, total_installs_alltime, total_downloads_alltime
      (grows by one row per run; not retroactive)
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from http_utils import make_session, polite_get  # noqa: E402
from parsers.timeseries import append_snapshot_csv, month_bucket, write_csv  # noqa: E402

BASE_URL = "https://clawhub.ai/api/v1"
OUT_DIR = Path(__file__).resolve().parent.parent / "data_outputs"
CACHE_PATH = OUT_DIR / ".clawhub_skills_cache.json"


def load_cache() -> dict[str, dict]:
    if CACHE_PATH.exists():
        return json.loads(CACHE_PATH.read_text())
    return {}


def save_cache(cache: dict[str, dict]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache))


def fetch_new_and_updated_skills(session, cache: dict[str, dict], page_size: int = 200, max_pages: int = 2000) -> int:
    """Walk /api/v1/skills newest-updated-first, merging into `cache` in place.

    Stops as soon as a page's items are all already in the cache with the
    same updatedAt -- everything before that point in this sort order is
    guaranteed unchanged. Returns the number of new/changed skills fetched.
    """
    fetched = 0
    cursor = None
    seen_cursors = set()
    for page_num in range(max_pages):
        params = {"limit": page_size, "sort": "updated", "order": "desc"}
        if cursor:
            params["cursor"] = cursor
        resp = polite_get(session, f"{BASE_URL}/skills", params=params, sleep=0.05)
        if resp.status_code != 200:
            print(f"  warning: /skills returned {resp.status_code}, stopping", file=sys.stderr)
            break
        payload = resp.json()
        items = payload.get("items", [])
        if not items:
            break

        page_had_new = False
        for item in items:
            slug = item.get("slug")
            if not slug:
                continue
            cached = cache.get(slug)
            if cached is not None and cached.get("updatedAt") == item.get("updatedAt"):
                continue  # unchanged since last run
            cache[slug] = item
            fetched += 1
            page_had_new = True

        print(f"  page {page_num + 1}: {fetched} new/changed so far", flush=True)

        if not page_had_new:
            # Every item on this page was already cached and unchanged --
            # since we're walking newest-updated-first, nothing further back matters.
            break

        cursor = payload.get("nextCursor")
        if not cursor or cursor in seen_cursors:
            break
        seen_cursors.add(cursor)
    return fetched


def build_monthly_publish_series(skills: list[dict]) -> list[dict]:
    counts = Counter()
    for s in skills:
        created_ms = s.get("createdAt")
        if not created_ms:
            continue
        d = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc).date()
        counts[month_bucket(d)] += 1

    months = sorted(counts)
    rows = []
    cumulative = 0
    for m in months:
        cumulative += counts[m]
        rows.append({"month": m, "skills_published_this_month": counts[m], "cumulative_skills_published": cumulative})
    return rows


def main() -> None:
    session = make_session()
    cache = load_cache()
    first_run = not cache

    print(
        "Fetching ClawHub skill catalog "
        + ("(first run -- full walk, several minutes)..." if first_run else "(incremental update since last run)...")
    )
    new_count = fetch_new_and_updated_skills(session, cache)
    print(f"  {new_count} new/changed skills this run; {len(cache)} total in local cache")

    if not cache:
        print("No skills retrieved; ClawHub API may be unreachable or its shape has changed. Aborting.", file=sys.stderr)
        return

    save_cache(cache)
    skills = list(cache.values())

    monthly_rows = build_monthly_publish_series(skills)
    write_csv(OUT_DIR / "clawhub_skills_published_monthly.csv", monthly_rows)
    print(f"  wrote {len(monthly_rows)} months to clawhub_skills_published_monthly.csv")

    total_installs = sum((s.get("stats") or {}).get("installs") or 0 for s in skills)
    total_downloads = sum((s.get("stats") or {}).get("downloads") or 0 for s in skills)

    snapshot = {
        "date": date.today().isoformat(),
        "total_skills": len(skills),
        "total_installs_alltime": total_installs,
        "total_downloads_alltime": total_downloads,
    }
    append_snapshot_csv(OUT_DIR / "clawhub_installs_snapshots.csv", snapshot)
    print(f"  appended snapshot: {snapshot}")


if __name__ == "__main__":
    main()
