"""
n8n growth proxies: npm downloads + GitHub stars.

n8n is distributed via npm, not PyPI, so it needs its own download
source: npmjs.org's public downloads API (api.npmjs.org/downloads/point/...),
confirmed live and unauthenticated, no key required, no documented rate
limit (unlike pypistats.org). GitHub star counts reuse the same
unauthenticated repo-lookup endpoint as the LangGraph/CrewAI scraper,
and are appended into the same shared github_stars_snapshot.csv (keyed
by package+date, so it coexists safely with other packages' rows).

Observed in production (2026-07-27 through 2026-07-29): npm's last-month
downloads window can silently freeze -- three consecutive daily runs
returned the exact same 30-day window (still ending 2026-07-24) instead
of advancing, with no non-200 status to signal it. The scraper now
detects when the newly-fetched window's max date doesn't advance past
what's already on disk and skips the write with a loud stderr warning,
instead of quietly no-op'ing forever.

Output:
  data_outputs/npm_downloads_daily.csv   package, date, downloads (last ~7 day window per run)
  data_outputs/github_stars_snapshot.csv package, date, total_stars (shared with other scrapers)
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from http_utils import make_session, polite_get  # noqa: E402
from parsers.timeseries import append_snapshot_csv  # noqa: E402

PACKAGE = "n8n"
REPO = "n8n-io/n8n"
OUT_DIR = Path(__file__).resolve().parent.parent / "data_outputs"


def fetch_npm_downloads(session) -> list[dict]:
    resp = polite_get(session, f"https://api.npmjs.org/downloads/range/last-month/{PACKAGE}", sleep=0.3)
    if resp.status_code != 200:
        print(f"  [{PACKAGE}] npm downloads endpoint returned {resp.status_code}", file=sys.stderr)
        return []
    days = resp.json().get("downloads", [])
    return [{"package": PACKAGE, "date": d["day"], "downloads": d["downloads"]} for d in days]


def fetch_current_star_count(session) -> int | None:
    resp = polite_get(session, f"https://api.github.com/repos/{REPO}", sleep=0.3)
    if resp.status_code != 200:
        print(f"  [{REPO}] repo lookup returned {resp.status_code}", file=sys.stderr)
        return None
    return resp.json().get("stargazers_count")


def main() -> None:
    session = make_session()
    session.headers.pop("Authorization", None)

    print(f"Fetching npm downloads for {PACKAGE}...")
    new_rows = fetch_npm_downloads(session)
    print(f"  {PACKAGE}: {len(new_rows)} days from npm's last-month window")
    if new_rows:
        import csv

        path = OUT_DIR / "npm_downloads_daily.csv"
        prior = []
        prior_max_date = None
        if path.exists():
            with path.open(newline="") as f:
                all_prior = list(csv.DictReader(f))
            prior = [r for r in all_prior if r["package"] != PACKAGE]
            prior_pkg_dates = [r["date"] for r in all_prior if r["package"] == PACKAGE]
            if prior_pkg_dates:
                prior_max_date = max(prior_pkg_dates)

        new_max_date = max(r["date"] for r in new_rows)
        if prior_max_date is not None and new_max_date <= prior_max_date:
            print(
                f"  [{PACKAGE}] npm's last-month window hasn't advanced past {prior_max_date} "
                f"(still true as of {new_max_date}) -- npm's downloads API may be serving a "
                f"stale/cached window. Not overwriting existing data.",
                file=sys.stderr,
            )
            new_rows = []
        else:
            combined = prior + new_rows
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=["package", "date", "downloads"])
                writer.writeheader()
                writer.writerows(combined)

    print(f"Fetching GitHub star count for {PACKAGE} ({REPO})...")
    total = fetch_current_star_count(session)
    if total is not None:
        append_snapshot_csv(
            OUT_DIR / "github_stars_snapshot.csv",
            {"package": PACKAGE, "date": date.today().isoformat(), "total_stars": total},
            key_field=("package", "date"),
        )
        print(f"  {PACKAGE}: {total} total stars (snapshot)")


if __name__ == "__main__":
    main()
