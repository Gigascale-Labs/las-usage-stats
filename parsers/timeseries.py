"""Shared helpers for turning sparse snapshots into daily-interval time series."""
from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path


def daterange(start: date, end: date):
    """Yield every date from start to end, inclusive."""
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def forward_fill_daily(points: list[tuple[date, dict]], end: date | None = None) -> list[tuple[date, dict]]:
    """Expand sparse (date, value_dict) points into one row per day.

    Each day carries forward the most recent known value for every field
    until a newer point supersedes it. Days before the first point are
    omitted rather than backfilled, since the metric didn't exist yet.
    """
    if not points:
        return []
    points = sorted(points, key=lambda p: p[0])
    last_date = end or points[-1][0]
    out = []
    idx = 0
    current: dict = {}
    for d in daterange(points[0][0], last_date):
        while idx < len(points) and points[idx][0] == d:
            current = {**current, **points[idx][1]}
            idx += 1
        out.append((d, dict(current)))
    return out


def month_bucket(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def append_snapshot_csv(path: str | Path, row: dict, key_field: str | tuple[str, ...] = "date") -> None:
    """Append a dated snapshot row to a CSV, creating it with a header if needed.

    Skips writing if a row matching key_field already exists, so re-running a
    scraper the same day is idempotent. key_field can be a single column name
    (e.g. "date") or a tuple of columns (e.g. ("package", "date")) for files
    that hold more than one series -- otherwise same-day rows for different
    series would collide and shadow each other.
    """
    path = Path(path)
    fieldnames = list(row.keys())
    existing_rows = []
    if path.exists():
        with path.open(newline="") as f:
            reader = csv.DictReader(f)
            existing_rows = list(reader)
            if reader.fieldnames:
                fieldnames = reader.fieldnames

    key_fields = (key_field,) if isinstance(key_field, str) else key_field
    row_key = tuple(str(row.get(f)) for f in key_fields)
    if any(tuple(r.get(f) for f in key_fields) == row_key for r in existing_rows):
        return

    write_header = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow(row)


def write_csv(path: str | Path, rows: list[dict], fieldnames: list[str] | None = None) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    fieldnames = fieldnames or list(rows[0].keys())
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
