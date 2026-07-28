"""Runs every scraper once, then the master aggregator. Meant to be cron'd
(e.g. daily/weekly) so the snapshot-based sources build up real history
over time instead of only ever having one data point.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

STEPS = [
    ("ClawHub", ROOT / "scrapers" / "clawhub_scraper.py"),
    ("EvoMap Hub", ROOT / "scrapers" / "evomap_scraper.py"),
    ("MoltBook", ROOT / "scrapers" / "moltbook_scraper.py"),
    ("Olas", ROOT / "scrapers" / "olas_scraper.py"),
    ("LangGraph / CrewAI / Agent Framework", ROOT / "scrapers" / "langgraph_crewai_scraper.py"),
    ("n8n", ROOT / "scrapers" / "n8n_scraper.py"),
    ("Smithery", ROOT / "scrapers" / "smithery_scraper.py"),
    ("Robinhood Chain", ROOT / "scrapers" / "robinhood_scraper.py"),
]


def main() -> None:
    failures = []
    for name, script in STEPS:
        print(f"\n=== {name} ===")
        result = subprocess.run([sys.executable, str(script)])
        if result.returncode != 0:
            print(f"  -> {name} exited with code {result.returncode}", file=sys.stderr)
            failures.append(name)

    print("\n=== Compiling master CSV ===")
    subprocess.run([sys.executable, str(ROOT / "compile_agent_stats.py")])

    if failures:
        print(f"\nCompleted with failures in: {', '.join(failures)}", file=sys.stderr)
        sys.exit(1)
    print("\nAll scrapers ran successfully.")


if __name__ == "__main__":
    main()
