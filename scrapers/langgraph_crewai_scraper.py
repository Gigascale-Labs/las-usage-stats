"""
Task 6: LangGraph & CrewAI growth proxies (PyPI downloads + GitHub stars).

Two real limitations discovered by testing the live endpoints, not
assumed from the brief:

1. pypistats.org's public API (confirmed working, no key needed) only
   retains a rolling ~180-day window of daily download counts -- it is
   NOT a full-history endpoint. Longer history exists only in Google's
   public BigQuery `pypi-downloads` dataset, which needs a GCP service
   account and is out of scope here. This script pulls what pypistats.org
   actually has and labels it honestly as a recent window, not "since
   inception".

2. GitHub's stargazers-with-timestamp endpoint
   (Accept: application/vnd.github.star+json) now requires authentication
   for every request, even at low volume -- confirmed by testing
   unauthenticated (401 Requires authentication). A GITHUB_TOKEN env var
   is required to get star-history-over-time at all; without one this
   script falls back to just the current total star count. Separately,
   GitHub's stargazer pagination is capped at ~40,000 most-recent stars
   (400 pages x 100/page) -- CrewAI currently has ~55k stars, so even
   with a token its oldest ~15k stars' exact dates aren't retrievable via
   this endpoint. That gap is documented in the output, not hidden.

Output:
  data_outputs/pypi_downloads_daily.csv       package, date, downloads   (last ~180 days only)
  data_outputs/github_stars_daily.csv         package, date, cumulative_stars  (if GITHUB_TOKEN set)
  data_outputs/github_stars_snapshot.csv      package, date, total_stars (always written, one row/run)
"""
from __future__ import annotations

import os
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from http_utils import make_session, polite_get  # noqa: E402
from parsers.timeseries import append_snapshot_csv, write_csv  # noqa: E402

PACKAGES = {
    "langgraph": "langchain-ai/langgraph",
    "crewai": "crewAIInc/crewAI",
}

OUT_DIR = Path(__file__).resolve().parent.parent / "data_outputs"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
STAR_PAGE_CAP = 400  # GitHub's practical pagination ceiling for this endpoint (400 * 100 = 40,000 stars)


def fetch_pypi_downloads(session, package: str) -> list[dict]:
    resp = polite_get(session, f"https://pypistats.org/api/packages/{package}/overall", params={"mirrors": "false"}, sleep=0.5)
    if resp.status_code != 200:
        print(f"  [{package}] pypistats returned {resp.status_code}", file=sys.stderr)
        return []
    data = resp.json().get("data", [])
    return [{"package": package, "date": row["date"], "downloads": row["downloads"]} for row in data]


def fetch_current_star_count(session, repo: str) -> int | None:
    resp = polite_get(session, f"https://api.github.com/repos/{repo}", sleep=0.3)
    if resp.status_code != 200:
        print(f"  [{repo}] repo lookup returned {resp.status_code}", file=sys.stderr)
        return None
    return resp.json().get("stargazers_count")


def fetch_star_history(session, repo: str) -> list[dict]:
    """Returns [{'date': iso_date, 'stars_that_day': n}, ...] from stargazer timestamps.

    Requires GITHUB_TOKEN. Capped at STAR_PAGE_CAP pages -- for repos with
    more stars than that cap allows, the oldest stars are undercounted
    and a warning is printed rather than silently truncating.
    """
    if not GITHUB_TOKEN:
        print(f"  [{repo}] no GITHUB_TOKEN set -- skipping star-history, using snapshot count only")
        return []

    headers = {"Accept": "application/vnd.github.star+json", "Authorization": f"Bearer {GITHUB_TOKEN}"}
    per_day = Counter()
    page = 1
    while page <= STAR_PAGE_CAP:
        resp = polite_get(
            session,
            f"https://api.github.com/repos/{repo}/stargazers",
            params={"per_page": 100, "page": page},
            headers=headers,
            sleep=0.2,
        )
        if resp.status_code == 401:
            print(f"  [{repo}] GITHUB_TOKEN was rejected (401); skipping star-history", file=sys.stderr)
            return []
        if resp.status_code == 403:
            print(f"  [{repo}] rate-limited (403) at page {page}; stopping early", file=sys.stderr)
            break
        if resp.status_code != 200:
            print(f"  [{repo}] stargazers returned {resp.status_code} at page {page}", file=sys.stderr)
            break
        batch = resp.json()
        if not batch:
            break
        for entry in batch:
            starred_at = entry.get("starred_at")
            if starred_at:
                d = datetime.fromisoformat(starred_at.replace("Z", "+00:00")).date().isoformat()
                per_day[d] += 1
        if len(batch) < 100:
            break
        page += 1

    if page > STAR_PAGE_CAP:
        print(
            f"  [{repo}] hit the {STAR_PAGE_CAP}-page pagination cap "
            f"(~{STAR_PAGE_CAP * 100:,} stars) -- older stars beyond this are not represented",
            file=sys.stderr,
        )

    days = sorted(per_day)
    rows = []
    cumulative = 0
    for d in days:
        cumulative += per_day[d]
        rows.append({"date": d, "cumulative_stars": cumulative})
    return rows


def main() -> None:
    session = make_session()
    session.headers.pop("Authorization", None)

    all_download_rows = []
    for package in PACKAGES:
        print(f"Fetching PyPI downloads for {package}...")
        rows = fetch_pypi_downloads(session, package)
        print(f"  {package}: {len(rows)} days (pypistats.org's rolling window)")
        all_download_rows.extend(rows)
    write_csv(OUT_DIR / "pypi_downloads_daily.csv", all_download_rows, fieldnames=["package", "date", "downloads"])

    for package, repo in PACKAGES.items():
        print(f"Fetching GitHub star data for {package} ({repo})...")
        total = fetch_current_star_count(session, repo)
        if total is not None:
            append_snapshot_csv(
                OUT_DIR / "github_stars_snapshot.csv",
                {"package": package, "date": date.today().isoformat(), "total_stars": total},
                key_field="date",
            )
            print(f"  {package}: {total} total stars (snapshot)")

        history = fetch_star_history(session, repo)
        if history:
            tagged = [{"package": package, **row} for row in history]
            existing_path = OUT_DIR / "github_stars_daily.csv"
            # Overwrite per-package rather than append, since this is a full recomputation each run.
            prior = []
            if existing_path.exists():
                import csv

                with existing_path.open() as f:
                    prior = [r for r in csv.DictReader(f) if r["package"] != package]
            write_csv(existing_path, prior + tagged, fieldnames=["package", "date", "cumulative_stars"])
            print(f"  {package}: wrote {len(tagged)} days of star history")


if __name__ == "__main__":
    main()
