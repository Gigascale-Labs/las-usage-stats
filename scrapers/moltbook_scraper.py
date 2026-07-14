"""
Task 3: MoltBook registration stats (human-verified vs. total registered).

Confirmed by hand: moltbook.com is a Next.js SPA. The raw HTML has no
embedded __NEXT_DATA__ payload and the stat numbers are populated client-
side after the JS bundle runs (the brief's suspicion that it needs
dynamic rendering was correct). Plain requests + BeautifulSoup will not
see the numbers -- this uses Playwright to render the page for real.

Real-world numbers as of mid-2026 (confirmed via search, not guessed):
Moltbook quietly added a "human-verified" count after it emerged that
~17,000 humans were controlling the bulk of the ~2.9M registered
"agent" accounts at ~88 accounts/person on average -- the human-verified
number is deliberately much smaller than total registered and has been
*dropping* over time as verification sweeps remove bot-farm accounts.
That's expected behavior, not a scraper bug.

Requires: `pip install playwright && playwright install chromium` once.

Output:
  data_outputs/moltbook_stats_latest.json   (single current snapshot, as the brief specified)
  data_outputs/moltbook_stats_history.jsonl (one line appended per run, for time-series use)
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "data_outputs"
URL = "https://www.moltbook.com/"

# Matches things like "206,839 Human-Verified" / "Human-Verified Agents: 206,839"
# in either order, tolerant of the exact label wording changing slightly.
HUMAN_VERIFIED_PATTERNS = [
    re.compile(r"([\d,]+)\s*\+?\s*Human[- ]Verified", re.IGNORECASE),
    re.compile(r"Human[- ]Verified[^\d]{0,20}([\d,]+)", re.IGNORECASE),
]
TOTAL_REGISTERED_PATTERNS = [
    re.compile(r"([\d,]+)\s*\+?\s*(?:Total\s+)?Registered", re.IGNORECASE),
    re.compile(r"Registered[^\d]{0,20}([\d,]+)", re.IGNORECASE),
]


def _first_match(patterns, text) -> int | None:
    for pat in patterns:
        m = pat.search(text)
        if m:
            return int(m.group(1).replace(",", ""))
    return None


def scrape_with_playwright() -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
                "las-usage-stats-research-bot"
            )
        )
        page.goto(URL, wait_until="networkidle", timeout=30000)
        # Numbers render async after hydration; give it a beat.
        page.wait_for_timeout(3000)
        body_text = page.inner_text("body")
        browser.close()

    human_verified = _first_match(HUMAN_VERIFIED_PATTERNS, body_text)
    total_registered = _first_match(TOTAL_REGISTERED_PATTERNS, body_text)
    return {"human_verified": human_verified, "total_registered": total_registered, "raw_text_sample": body_text[:2000]}


def main() -> None:
    try:
        result = scrape_with_playwright()
    except ImportError:
        print(
            "Playwright is not installed. Run:\n"
            "  pip install playwright && playwright install chromium\n"
            "MoltBook is a client-rendered SPA -- plain requests won't see the stats.",
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to scrape MoltBook: {exc}", file=sys.stderr)
        sys.exit(1)

    if result["human_verified"] is None or result["total_registered"] is None:
        print(
            "Could not find both stats in the rendered page. "
            "MoltBook's markup/labels may have changed -- inspect raw_text_sample below.",
            file=sys.stderr,
        )
        print(result["raw_text_sample"], file=sys.stderr)
        sys.exit(1)

    snapshot = {
        "date": date.today().isoformat(),
        "human_verified": result["human_verified"],
        "total_registered": result["total_registered"],
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "moltbook_stats_latest.json").write_text(json.dumps(snapshot, indent=2))
    with (OUT_DIR / "moltbook_stats_history.jsonl").open("a") as f:
        f.write(json.dumps(snapshot) + "\n")

    print(f"Wrote snapshot: {snapshot}")


if __name__ == "__main__":
    main()
