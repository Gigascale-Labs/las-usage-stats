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

Fix, part 1 (navigation): navigate with wait_until="domcontentloaded"
(fires once the DOM is parsed, independent of ongoing background network
chatter) and then poll for the actual stats to appear in the rendered
text, instead of trusting a network-quiescence signal or a blind fixed
sleep for hydration. Falls back to a "load"-based navigation attempt if
domcontentloaded itself times out (e.g. a real outage), so a single
strategy failing doesn't sink the whole scrape. Confirmed live: this
alone fixed the timeout (0.3s to reach domcontentloaded).

Fix, part 2 (stat detection -- found via that same live run's
diagnostics, not guessed): fixing navigation exposed a second,
previously-masked bug. MoltBook's homepage stats are count-up/odometer
widgets that render as literal "0" the instant the DOM is parsed, then
animate up client-side -- the original polling logic treated the mere
presence of a regex match ("0 Human-Verified AI Agents") as "found" and
returned garbage instantly. Since real MoltBook numbers have never been
close to zero (~194-207k human-verified, ~2.85-2.9M registered), any
matched value below PLAUSIBLE_STAT_FLOOR is now treated as "still
animating, keep polling" rather than as data. The poll window was also
widened (12s -> 20s) to give slower animations/fetches room to finish.

Clearing the plausibility floor isn't proof the animation has *finished*
though -- a count-up mid-flight could cross 1,000 well before reaching
its real endpoint. So a value is only accepted once it's read back
identical on STABLE_REPEATS_REQUIRED consecutive polls; if it's still
changing between polls when the timeout hits, that's treated the same
as never having found it (not accepted as a false-confidence answer).
Every stage's outcome -- which wait strategy succeeded/failed and when,
how long it took for *both* stats to reach a plausible value, and which
specific stat(s) never did -- is captured and surfaced in the final error
message on failure, so a future failure is diagnosable from the CI log
alone instead of requiring a re-run to guess at what stage broke.

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


# MoltBook's homepage stats render as count-up/odometer-style widgets that
# start at literal "0" and animate up to the real value -- confirmed by
# observing a live run's raw_text_sample where the fully domcontentloaded,
# hydrated page showed "0 Human-Verified AI Agents" moments after load. A
# naive "did the pattern match at all" check treats that "0" as found and
# returns bogus data instantly. Real MoltBook numbers have never been
# anywhere close to zero (~194-207k human-verified, ~2.85-2.9M registered,
# confirmed via search when this scraper was first written, and they only
# trend in that range over time) -- so any match at or near zero is treated
# as "still animating", not as data, and polling continues.
PLAUSIBLE_STAT_FLOOR = 1000


def _plausible_match(patterns, text) -> int | None:
    value = _first_match(patterns, text)
    return value if value is not None and value >= PLAUSIBLE_STAT_FLOOR else None


NAV_TIMEOUT_MS = 20000
STAT_POLL_TIMEOUT_S = 20
STAT_POLL_INTERVAL_S = 0.5
STABLE_REPEATS_REQUIRED = 2  # same plausible value must be read back this many times in a row

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

        # Numbers render async after hydration -- and, per the note above,
        # can render as an animating "0" before settling on the real value,
        # possibly climbing through several intermediate frames on the way.
        # Poll for both stats to reach a plausible (non-placeholder) value
        # *and* read back unchanged on STABLE_REPEATS_REQUIRED consecutive
        # polls, rather than trusting a fixed sleep, a network-idle signal,
        # or the first plausible-looking number seen (which could be a
        # mid-animation frame, not the endpoint). Gives a precise "waited
        # Ns, still not there/still changing" diagnostic on failure.
        poll_start = time.monotonic()
        body_text = page.inner_text("body")
        found_at = None
        last_seen: tuple[int | None, int | None] = (None, None)
        final_values: tuple[int | None, int | None] = (None, None)
        repeats = 0
        while time.monotonic() - poll_start < STAT_POLL_TIMEOUT_S:
            current = (
                _plausible_match(HUMAN_VERIFIED_PATTERNS, body_text),
                _plausible_match(TOTAL_REGISTERED_PATTERNS, body_text),
            )
            plausible = current[0] is not None and current[1] is not None
            if plausible and current == last_seen:
                repeats += 1
                if repeats >= STABLE_REPEATS_REQUIRED:
                    found_at = time.monotonic() - poll_start
                    final_values = current
                    break
            else:
                repeats = 1 if plausible else 0
            last_seen = current
            page.wait_for_timeout(int(STAT_POLL_INTERVAL_S * 1000))
            body_text = page.inner_text("body")
        poll_elapsed = time.monotonic() - poll_start

        if found_at is not None:
            diagnostics.append(
                f"both stats reached a stable, plausible value after {found_at:.1f}s of polling: "
                f"human_verified={final_values[0]}, total_registered={final_values[1]}"
            )
        else:
            still_missing = [
                name
                for name, val in [("human_verified", last_seen[0]), ("total_registered", last_seen[1])]
                if val is None
            ]
            if still_missing:
                diagnostics.append(
                    f"{', '.join(still_missing)} never reached a plausible (>={PLAUSIBLE_STAT_FLOOR}) value "
                    f"after {poll_elapsed:.1f}s of polling (limit {STAT_POLL_TIMEOUT_S}s) -- "
                    f"either still animating past our timeout, or MoltBook's markup/labels changed"
                )
            else:
                diagnostics.append(
                    f"both stats reached plausible values but never stabilized across "
                    f"{STABLE_REPEATS_REQUIRED} consecutive polls within {poll_elapsed:.1f}s "
                    f"(limit {STAT_POLL_TIMEOUT_S}s) -- last seen: "
                    f"human_verified={last_seen[0]}, total_registered={last_seen[1]}"
                )

        browser.close()

    human_verified, total_registered = final_values
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
