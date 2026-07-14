"""
Master aggregator: merges the individual scrapers' outputs into one
daily-interval master CSV tracking growth of the agentic ecosystem.

Design notes, so the merge doesn't quietly mislead anyone reading the CSV:

* Every source in this project is either (a) a true historical time
  series (Olas daily actives, ClawHub's skill-publish-by-month, PyPI's
  ~180-day download window), or (b) a snapshot log that only has data
  from the date each scraper was first run onward (ClawHub installs,
  EvoMap hub stats, GitHub star totals, MoltBook). Type (b) sources are
  forward-filled from their first observed date -- they are NOT
  backfilled with NaN-disguised-as-history before that date, and this
  script does not pretend to know what those values were before it
  started measuring them.

* Task 4 (enterprise adoption research) and Task 7 (MCP tool register)
  are NOT time series -- they're point-in-time research compilations
  with their own publication dates scattered across 2025-2026. Folding
  them into a daily-interval grid would imply a false precision (e.g.
  "48% telecom adoption" isn't a fact about July 14th specifically, it's
  a July 2026 survey result). They're intentionally left as their own
  CSVs and just summarized in the run log, not merged into the daily
  grid.

* Missing fields for dates before a source existed, or for chains/
  packages this project doesn't cover, are written as empty (pandas/csv
  reads these back as NaN).

Run the individual scrapers in scrapers/ first (or via run_all.py) --
this script only reads what's already in data_outputs/.
"""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parsers.timeseries import daterange  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent / "data_outputs"
OUT_PATH = DATA_DIR / "master_agent_stats_daily.csv"

FIELDS = [
    "date",
    "clawhub_cumulative_skills_published",
    "clawhub_total_skills_snapshot",
    "clawhub_total_installs_alltime_snapshot",
    "clawhub_total_downloads_alltime_snapshot",
    "evomap_total_nodes",
    "evomap_total_assets",
    "evomap_promoted_assets",
    "moltbook_human_verified",
    "moltbook_total_registered",
    "olas_gnosis_daily_active_agents",
    "olas_mode_daily_active_agents",
    "olas_total_daily_active_agents",
    "langgraph_pypi_downloads",
    "langgraph_github_stars_cumulative",
    "crewai_pypi_downloads",
    "crewai_github_stars_cumulative",
]


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def read_jsonl(path: Path) -> list[dict]:
    import json

    if not path.exists():
        return []
    out = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def snapshot_series_from_first_date(rows: list[dict], date_field: str, value_fields: list[str]) -> dict[str, dict]:
    """Turn a sparse append-only snapshot log into a {date: {field: value}} dict,
    forward-filled for every day from the first snapshot to today."""
    if not rows:
        return {}
    parsed = sorted(rows, key=lambda r: r[date_field])
    first = datetime.strptime(parsed[0][date_field], "%Y-%m-%d").date()
    today = date.today()

    points = []
    for r in parsed:
        d = datetime.strptime(r[date_field], "%Y-%m-%d").date()
        points.append((d, {f: r.get(f) for f in value_fields}))

    series: dict[str, dict] = {}
    current: dict = {}
    idx = 0
    for d in daterange(first, today):
        while idx < len(points) and points[idx][0] == d:
            current = {**current, **points[idx][1]}
            idx += 1
        series[d.isoformat()] = dict(current)
    return series


def main() -> None:
    print("Reading individual source outputs from data_outputs/ ...")

    # --- ClawHub: two different series with two different meanings ---
    clawhub_monthly = read_csv(DATA_DIR / "clawhub_skills_published_monthly.csv")
    cumulative_by_month = {r["month"]: r["cumulative_skills_published"] for r in clawhub_monthly}

    clawhub_snaps = read_csv(DATA_DIR / "clawhub_installs_snapshots.csv")
    clawhub_snap_series = snapshot_series_from_first_date(
        clawhub_snaps, "date", ["total_skills", "total_installs_alltime", "total_downloads_alltime"]
    )

    # --- EvoMap ---
    evomap_snaps = read_csv(DATA_DIR / "evomap_hub_snapshots.csv")
    evomap_series = snapshot_series_from_first_date(
        evomap_snaps, "date", ["total_nodes", "total_assets", "promoted_assets"]
    )

    # --- MoltBook ---
    moltbook_snaps = read_jsonl(DATA_DIR / "moltbook_stats_history.jsonl")
    moltbook_series = snapshot_series_from_first_date(
        moltbook_snaps, "date", ["human_verified", "total_registered"]
    ) if moltbook_snaps else {}

    # --- Olas: true daily history, no forward-fill needed ---
    olas_rows = read_csv(DATA_DIR / "olas_daily_active_agents.csv")
    olas_by_date = {r["date"]: r for r in olas_rows}

    # --- PyPI downloads: true daily history (bounded ~180-day window) ---
    pypi_rows = read_csv(DATA_DIR / "pypi_downloads_daily.csv")
    pypi_by_pkg_date: dict[tuple[str, str], str] = {(r["package"], r["date"]): r["downloads"] for r in pypi_rows}

    # --- GitHub stars: true daily history if GITHUB_TOKEN was set, else nothing ---
    stars_rows = read_csv(DATA_DIR / "github_stars_daily.csv")
    stars_by_pkg_date: dict[tuple[str, str], str] = {
        (r["package"], r["date"]): r["cumulative_stars"] for r in stars_rows
    }

    # --- Determine overall date range to emit ---
    all_dates = set()
    all_dates.update(cumulative_by_month.keys())  # months, handled separately below
    all_dates.update(clawhub_snap_series.keys())
    all_dates.update(evomap_series.keys())
    all_dates.update(moltbook_series.keys())
    all_dates.update(olas_by_date.keys())
    all_dates.update(d for (_, d) in pypi_by_pkg_date.keys())
    all_dates.update(d for (_, d) in stars_by_pkg_date.keys())
    all_dates.discard(None)

    real_dates = [d for d in all_dates if len(d) == 10]  # filter out any stray month-only keys
    if not real_dates:
        print("No source data found yet -- run the scrapers in scrapers/ first.", file=sys.stderr)
        return

    start = min(datetime.strptime(d, "%Y-%m-%d").date() for d in real_dates)
    end = max(datetime.strptime(d, "%Y-%m-%d").date() for d in real_dates)

    print(f"Merging into daily grid from {start} to {end} ...")

    rows = []
    for d in daterange(start, end):
        iso = d.isoformat()
        month = f"{d.year:04d}-{d.month:02d}"

        # Cumulative skills published: forward-fill from the last completed month bucket.
        clawhub_cum_skills = None
        available_months = sorted(m for m in cumulative_by_month if m <= month)
        if available_months:
            clawhub_cum_skills = cumulative_by_month[available_months[-1]]

        row = {
            "date": iso,
            "clawhub_cumulative_skills_published": clawhub_cum_skills,
            "clawhub_total_skills_snapshot": clawhub_snap_series.get(iso, {}).get("total_skills"),
            "clawhub_total_installs_alltime_snapshot": clawhub_snap_series.get(iso, {}).get("total_installs_alltime"),
            "clawhub_total_downloads_alltime_snapshot": clawhub_snap_series.get(iso, {}).get("total_downloads_alltime"),
            "evomap_total_nodes": evomap_series.get(iso, {}).get("total_nodes"),
            "evomap_total_assets": evomap_series.get(iso, {}).get("total_assets"),
            "evomap_promoted_assets": evomap_series.get(iso, {}).get("promoted_assets"),
            "moltbook_human_verified": moltbook_series.get(iso, {}).get("human_verified"),
            "moltbook_total_registered": moltbook_series.get(iso, {}).get("total_registered"),
            "olas_gnosis_daily_active_agents": olas_by_date.get(iso, {}).get("gnosis_count"),
            "olas_mode_daily_active_agents": olas_by_date.get(iso, {}).get("mode_count"),
            "olas_total_daily_active_agents": olas_by_date.get(iso, {}).get("total_count"),
            "langgraph_pypi_downloads": pypi_by_pkg_date.get(("langgraph", iso)),
            "langgraph_github_stars_cumulative": stars_by_pkg_date.get(("langgraph", iso)),
            "crewai_pypi_downloads": pypi_by_pkg_date.get(("crewai", iso)),
            "crewai_github_stars_cumulative": stars_by_pkg_date.get(("crewai", iso)),
        }
        rows.append(row)

    with OUT_PATH.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, restval="")
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} daily rows to {OUT_PATH}")
    print(
        "\nNote: enterprise_adoption_stats.csv (Task 4) and mcp_enterprise_support.csv (Task 7) "
        "are point-in-time research compilations, not time series -- intentionally left out of "
        "the daily grid. Read them directly."
    )


if __name__ == "__main__":
    main()
