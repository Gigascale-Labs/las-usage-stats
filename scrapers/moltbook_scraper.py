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

Observed in production (2026-07-28): a daily CI run failed with
"Page.goto: Timeout 30000ms exceeded" waiting for wait_until="networkidle".
Root cause, not just a flaky network blip: "networkidle" requires zero
in-flight network requests for 500ms, and Next.js SPAs commonly run
background polling/analytics/websocket traffic that never fully quiesces
-- so networkidle can time out even once the stats have long since
rendered. That's the same class of issue as generic Playwright docs warn
about for networkidle on modern SPAs (see playwright.dev's own caution
against relying on it). This run was otherwise a total fluke in isolation
(the only failure across the last 12+ daily runs), but the wait strategy
was fragile by construction, not just unlucky.

Fix: navigate with wait_until="domcontentloaded" (fires once the DOM is
parsed, independent of ongoing background network chatter) and then poll
for the actual stats to appear in the rendered text, instead of trusting
a network-quiescence signal or a blind fixed sleep for hydration. Falls
back to a "load"-based navigation attempt if domcontentloaded itself times
out (e.g. a real outage), so a single strategy failing doesn't sink the
whole scrape. Every stage's outcome (which wait strategy, how long it took
or when it timed out, how long stat-polling took) is captured and
surfaced in the final error message on failure, so a future timeout is
diagnosable from the CI log alone instead of requiring a re-run to guess
at what stage broke.

Requires: `pip install playwright && playwright install chromium` once.

Output:
  data_outputs/moltbook_stats_latest.json   (single current snapshot, as the brief specified)
  data_outputs/moltbook_stats_history.jsonl (one line appended per run, for time-series use)
"""
from __future__ import annotations

import json
import re
import sys
import time
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


NAV_TIMEOUT_MS = 20000
STAT_POLL_TIMEOUT_S = 12
STAT_POLL_INTERVAL_S = 0.5

# Tried in order; the first one that completes without timing out wins.
# "domcontentloaded" is the primary strategy (see module docstring for why
# networkidle is unreliable here); "load" is a fallback for the rarer case
# where domcontentloaded itself doesn't fire in time.
NAV_STRATEGIES = ["domcontentloaded", "load"]


def scrape_with_playwright() -> dict:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    diagnostics: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 "
                "las-usage-stats-research-bot"
            )
        )

        navigated = False
        for wait_until in NAV_STRATEGIES:
            start = time.monotonic()
            try:
                page.goto(URL, wait_until=wait_until, timeout=NAV_TIMEOUT_MS)
            except PlaywrightTimeoutError as exc:
                elapsed = time.monotonic() - start
                diagnostics.append(
                    f"goto(wait_until={wait_until!r}) timed out after {elapsed:.1f}s "
                    f"(limit {NAV_TIMEOUT_MS / 1000:.0f}s): {exc}"
                )
                continue
            elapsed = time.monotonic() - start
            diagnostics.append(f"goto(wait_until={wait_until!r}) succeeded in {elapsed:.1f}s")
            navigated = True
            break

        if not navigated:
            # Even a failed goto() often leaves a partially-loaded page behind
            # (the timeout is on reaching the wait_until state, not on getting
            # a response at all) -- grab whatever's there for the failure
            # message rather than raising blind.
            try:
                partial_len = len(page.content())
                diagnostics.append(f"page.content() after failed navigation: {partial_len} chars present")
            except Exception as exc:  # noqa: BLE001
                diagnostics.append(f"page.content() after failed navigation also failed: {exc}")
            browser.close()
            raise RuntimeError("All navigation strategies failed:\n  " + "\n  ".join(diagnostics))

        # Numbers render async after hydration. Poll for them to actually
        # show up in the text rather than trusting a fixed sleep or a
        # network-idle signal -- more robust to variable render time, and
        # gives a precise "waited Ns, still not there" diagnostic on failure.
        poll_start = time.monotonic()
        body_text = page.inner_text("body")
        found_at = None
        while time.monotonic() - poll_start < STAT_POLL_TIMEOUT_S:
            if _first_match(HUMAN_VERIFIED_PATTERNS, body_text) is not None:
                found_at = time.monotonic() - poll_start
                break
            page.wait_for_timeout(int(STAT_POLL_INTERVAL_S * 1000))
            body_text = page.inner_text("body")
        poll_elapsed = time.monotonic() - poll_start

        if found_at is not None:
            diagnostics.append(f"stats appeared in rendered text after {found_at:.1f}s of polling")
        else:
            diagnostics.append(
                f"stats never appeared after {poll_elapsed:.1f}s of polling "
                f"(limit {STAT_POLL_TIMEOUT_S}s) -- page may have hydrated with different markup"
            )

        browser.close()

    human_verified = _first_match(HUMAN_VERIFIED_PATTERNS, body_text)
    total_registered = _first_match(TOTAL_REGISTERED_PATTERNS, body_text)
    return {
        "human_verified": human_verified,
        "total_registered": total_registered,
        "raw_text_sample": body_text[:2000],
        "diagnostics": diagnostics,
    }


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
        print("Diagnostics:\n  " + "\n  ".join(result["diagnostics"]), file=sys.stderr)
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
