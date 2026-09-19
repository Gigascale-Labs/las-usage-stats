"""
iLands network stats snapshot.

iLands (ilands.ai) is a user-generated agent network -- agents and humans
coexisting in a shared world, with agents publishing content, building
social relationships, and reaching out to external social platforms.

The homepage's "The live network" strip is server-rendered (confirmed by
fetching https://ilands.ai/ with a plain GET -- no JS execution needed,
unlike MoltBook) and states the numbers are "Updated daily". No separate
public JSON API or stats endpoint was found on the page, so this scraper
parses the three metric values straight out of the rendered HTML:

    Active Agents               e.g. 73,971 (73,893 created in iLands + 78 BYOA)
    Agents on external social   e.g. 3,187
    Agent-created content       e.g. 1,815,206 (1,160,900 posts + 654,306 share moments)

Same caveat as ClawHub/EvoMap/Smithery: this is a live snapshot with no
historical endpoint, so the time series only starts accumulating from the
first run of this script onward.

Output:
  data_outputs/ilands_network_snapshots.csv
      date, active_agents, active_agents_created_in_ilands, active_agents_byoa,
      agents_external_social, agent_created_content, agent_created_content_posts,
      agent_created_content_share_moments
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from http_utils import make_session, polite_get  # noqa: E402
from parsers.timeseries import append_snapshot_csv  # noqa: E402

HOMEPAGE_URL = "https://ilands.ai/"
OUT_DIR = Path(__file__).resolve().parent.parent / "data_outputs"

METRIC_RE = re.compile(
    r'<div class="metric-item"><strong class="metric-value">([\d,]+)</strong>'
    r'<span class="metric-label">([^<]+)'
)
DETAIL_RE = re.compile(r'<span class="metric-detail">(.*?)</span>')
COMMENT_RE = re.compile(r'<!--.*?-->')


def _to_int(s: str) -> int:
    return int(s.replace(",", ""))


def _detail_numbers(detail_html: str) -> list[int]:
    """Pull every integer out of a metric-detail span, ignoring the HTML comments
    Next.js inserts between streamed text chunks (e.g. '73,893<!-- --> iLands')."""
    plain = COMMENT_RE.sub("", detail_html)
    return [_to_int(n) for n in re.findall(r'[\d,]+', plain)]


def main() -> None:
    session = make_session()
    print(f"Fetching {HOMEPAGE_URL} ...")
    resp = polite_get(session, HOMEPAGE_URL, sleep=0.3)
    if resp.status_code != 200:
        print(f"iLands homepage returned {resp.status_code}; aborting.", file=sys.stderr)
        return

    metrics = METRIC_RE.findall(resp.text)
    details = DETAIL_RE.findall(resp.text)
    if len(metrics) != 3 or len(details) != 3:
        print(
            f"Expected 3 metric-item/metric-detail pairs on the iLands homepage, "
            f"found {len(metrics)}/{len(details)} -- page layout likely changed; aborting.",
            file=sys.stderr,
        )
        return

    (active_agents, active_label), (social, social_label), (content, content_label) = metrics
    if "Active Agents" not in active_label or "external social" not in social_label or "content" not in content_label:
        print(f"Unexpected metric labels on iLands homepage: {[m[1] for m in metrics]}; aborting.", file=sys.stderr)
        return

    active_created, active_byoa = _detail_numbers(details[0])
    content_posts, content_share_moments = _detail_numbers(details[2])

    snapshot = {
        "date": date.today().isoformat(),
        "active_agents": _to_int(active_agents),
        "active_agents_created_in_ilands": active_created,
        "active_agents_byoa": active_byoa,
        "agents_external_social": _to_int(social),
        "agent_created_content": _to_int(content),
        "agent_created_content_posts": content_posts,
        "agent_created_content_share_moments": content_share_moments,
    }
    append_snapshot_csv(OUT_DIR / "ilands_network_snapshots.csv", snapshot)
    print(f"  appended snapshot: {snapshot}")


if __name__ == "__main__":
    main()
