#!/usr/bin/env python3
"""Builds dashboard/data.js from data_outputs/*.csv (+ moltbook jsonl).

Every series is emitted as {date_iso: value} at whatever resolution it was
actually recorded at (daily for scraped snapshots, monthly for the
hand-compiled ClawHub publish history). The dashboard buckets these into
day/month/year views and computes EWMA-based projections client-side, so
here we just need real recorded points plus a "kind" describing how to
aggregate them: cumulative (snapshot -> take last value in a bucket),
flow (take sum), or average (take mean).

Run this after each scrape to refresh dashboard/data.js:
    python3 dashboard/generate_data.py
"""
import csv
import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data_outputs"
OUT_FILE = Path(__file__).resolve().parent / "data.js"


def read_csv(name):
    path = DATA_DIR / name
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def read_jsonl(name):
    with (DATA_DIR / name).open() as f:
        return [json.loads(line) for line in f if line.strip()]


def points(rows, date_field, value_field):
    """{iso_date: float} for every row with a non-empty value."""
    out = {}
    for row in rows:
        v = row.get(value_field, "")
        if v in ("", None):
            continue
        out[row[date_field]] = float(v)
    return dict(sorted(out.items()))


def build():
    series = {}
    kinds = {}

    master = read_csv("master_agent_stats_daily.csv")
    olas = read_csv("olas_daily_active_agents.csv")
    pypi = read_csv("pypi_downloads_daily.csv")
    moltbook_history = read_jsonl("moltbook_stats_history.jsonl")
    github_stars = read_csv("github_stars_snapshot.csv")
    npm_downloads = read_csv("npm_downloads_daily.csv")
    smithery = read_csv("smithery_registry_snapshots.csv")
    robinhood_tx = read_csv("robinhood_chain_daily_transactions.csv")
    robinhood_snaps = read_csv("robinhood_chain_snapshots.csv")

    def add(key, kind, data):
        series[key] = data
        kinds[key] = kind

    # ClawHub cumulative snapshots (from daily scraper snapshots)
    add("clawhub_total_skills", "cumulative", points(master, "date", "clawhub_total_skills_snapshot"))
    add("clawhub_total_installs", "cumulative", points(master, "date", "clawhub_total_installs_alltime_snapshot"))
    add("clawhub_total_downloads", "cumulative", points(master, "date", "clawhub_total_downloads_alltime_snapshot"))

    # ClawHub skills published: native monthly hand-compiled history (dated to the 1st of month)
    monthly_pub = read_csv("clawhub_skills_published_monthly.csv")
    add("clawhub_skills_published_flow", "flow", {
        f"{r['month']}-01": float(r["skills_published_this_month"]) for r in monthly_pub
    })
    add("clawhub_cumulative_skills_published", "cumulative", {
        f"{r['month']}-01": float(r["cumulative_skills_published"]) for r in monthly_pub
    })

    # EvoMap cumulative snapshots
    add("evomap_total_nodes", "cumulative", points(master, "date", "evomap_total_nodes"))
    add("evomap_total_assets", "cumulative", points(master, "date", "evomap_total_assets"))
    add("evomap_promoted_assets", "cumulative", points(master, "date", "evomap_promoted_assets"))

    # Moltbook cumulative user counts -- from the dedicated history file, not the (currently
    # unpopulated) master CSV columns of the same name.
    add("moltbook_human_verified", "cumulative", points(moltbook_history, "date", "human_verified"))
    add("moltbook_total_registered", "cumulative", points(moltbook_history, "date", "total_registered"))

    # OLAS daily active agents (gauge-like -> average per bucket)
    add("olas_gnosis_daily_active", "average", points(olas, "date", "gnosis_count"))
    add("olas_mode_daily_active", "average", points(olas, "date", "mode_count"))
    add("olas_total_daily_active", "average", points(olas, "date", "total_count"))

    # PyPI downloads (flow, sum per bucket)
    for pkg in sorted({r["package"] for r in pypi}):
        rows = [r for r in pypi if r["package"] == pkg]
        add(f"pypi_downloads_{pkg}", "flow", points(rows, "date", "downloads"))

    # GitHub stars -- from the dedicated snapshot file, not the (currently unpopulated)
    # master CSV columns of the same name.
    for pkg in sorted({r["package"] for r in github_stars}):
        rows = [r for r in github_stars if r["package"] == pkg]
        add(f"github_stars_{pkg}", "cumulative", points(rows, "date", "total_stars"))
    # Ensure every tracked package has a series even if a snapshot row hasn't landed yet,
    # so the UI can render an explicit "no data" note instead of silently omitting the chart.
    for pkg in ("langgraph", "crewai", "agent-framework", "n8n"):
        series.setdefault(f"github_stars_{pkg}", {})
        kinds.setdefault(f"github_stars_{pkg}", "cumulative")

    # npm downloads (flow, sum per bucket) -- n8n is npm-distributed, not PyPI
    for pkg in sorted({r["package"] for r in npm_downloads}):
        rows = [r for r in npm_downloads if r["package"] == pkg]
        add(f"npm_downloads_{pkg}", "flow", points(rows, "date", "downloads"))

    # Smithery MCP server registry (cumulative snapshot)
    add("smithery_total_servers", "cumulative", points(smithery, "date", "total_servers"))

    # Robinhood Chain: true daily tx history (flow) + address/tx/block totals (cumulative snapshots)
    add("robinhood_chain_transactions", "flow", points(robinhood_tx, "date", "transactions_count"))
    add("robinhood_chain_total_addresses", "cumulative", points(robinhood_snaps, "date", "total_addresses"))
    add("robinhood_chain_total_transactions", "cumulative", points(robinhood_snaps, "date", "total_transactions"))
    add("robinhood_chain_total_blocks", "cumulative", points(robinhood_snaps, "date", "total_blocks"))

    metadata = {
        "generated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "kinds": kinds,
        "groups": [
            {
                "name": "ClawHub",
                "url": "https://clawhub.ai",
                "description": "OpenClaw's skill/plugin registry -- tracks published skills, installs, and downloads.",
                "metrics": [
                    {"key": "clawhub_total_skills", "label": "Total Skills (cumulative)"},
                    {"key": "clawhub_total_installs", "label": "Total Installs (all-time)"},
                    {"key": "clawhub_total_downloads", "label": "Total Downloads (all-time)"},
                    {"key": "clawhub_skills_published_flow", "label": "Skills Published (per period)"},
                ],
            },
            {
                "name": "EvoMap",
                "url": "https://evomap.ai",
                "description": "An agent-to-agent (A2A) capability graph -- tracks nodes, assets, and asset reuse across the ecosystem.",
                "metrics": [
                    {"key": "evomap_total_nodes", "label": "Total Nodes"},
                    {"key": "evomap_total_assets", "label": "Total Assets"},
                    {"key": "evomap_promoted_assets", "label": "Promoted Assets"},
                ],
            },
            {
                "name": "Moltbook",
                "url": "https://www.moltbook.com/",
                "description": "A social network for AI agents -- tracks registered and human-verified users.",
                "metrics": [
                    {"key": "moltbook_human_verified", "label": "Human Verified Users"},
                    {"key": "moltbook_total_registered", "label": "Total Registered Users"},
                ],
            },
            {
                "name": "OLAS Daily Active Agents",
                "url": "https://olas.network",
                "description": "Autonolas (OLAS) is a network of on-chain autonomous agent services -- this tracks daily active agents per chain.",
                "metrics": [
                    {"key": "olas_total_daily_active", "label": "Total Daily Active Agents"},
                    {"key": "olas_gnosis_daily_active", "label": "Gnosis Chain"},
                    {"key": "olas_mode_daily_active", "label": "Mode Chain"},
                ],
                "stacked_volume": {
                    "title": "Daily Active Agents by Chain (volume)",
                    "components": [
                        {"key": "olas_gnosis_daily_active", "label": "Gnosis"},
                        {"key": "olas_mode_daily_active", "label": "Mode"},
                    ],
                },
            },
            {
                "name": "PyPI Downloads",
                "url": "https://pypi.org",
                "description": "Package download counts for agent orchestration frameworks distributed via PyPI, via pypistats.org.",
                "metrics": [
                    {"key": "pypi_downloads_langgraph", "label": "LangGraph downloads", "url": "https://pypi.org/project/langgraph/"},
                    {"key": "pypi_downloads_crewai", "label": "CrewAI downloads", "url": "https://pypi.org/project/crewai/"},
                    {"key": "pypi_downloads_agent-framework", "label": "Microsoft Agent Framework downloads", "url": "https://pypi.org/project/agent-framework/"},
                ],
            },
            {
                "name": "npm Downloads",
                "url": "https://www.npmjs.com",
                "description": "Package download counts for agent tooling distributed via npm, via the npm registry's public downloads API.",
                "metrics": [
                    {"key": "npm_downloads_n8n", "label": "n8n downloads", "url": "https://www.npmjs.com/package/n8n"},
                ],
            },
            {
                "name": "GitHub Stars",
                "url": "https://github.com",
                "description": "Cumulative GitHub stars for tracked agent frameworks and tools, as a proxy for adoption.",
                "metrics": [
                    {"key": "github_stars_langgraph", "label": "LangGraph Stars", "url": "https://github.com/langchain-ai/langgraph"},
                    {"key": "github_stars_crewai", "label": "CrewAI Stars", "url": "https://github.com/crewAIInc/crewAI"},
                    {"key": "github_stars_agent-framework", "label": "Microsoft Agent Framework Stars", "url": "https://github.com/microsoft/agent-framework"},
                    {"key": "github_stars_n8n", "label": "n8n Stars", "url": "https://github.com/n8n-io/n8n"},
                ],
            },
            {
                "name": "Smithery",
                "url": "https://smithery.ai",
                "description": "A registry and hosting hub for Model Context Protocol (MCP) servers -- tracks the total number of servers listed.",
                "metrics": [
                    {"key": "smithery_total_servers", "label": "Total MCP Servers Listed"},
                ],
            },
            {
                "name": "Robinhood Chain",
                "url": "https://robinhoodchain.blockscout.com",
                "description": "Robinhood's own Layer 2 (Arbitrum Orbit, settling on Ethereum) -- tracks daily transaction counts plus total addresses, transactions, and blocks.",
                "metrics": [
                    {"key": "robinhood_chain_transactions", "label": "Daily Transactions"},
                    {"key": "robinhood_chain_total_addresses", "label": "Total Addresses"},
                    {"key": "robinhood_chain_total_transactions", "label": "Total Transactions (all-time)"},
                    {"key": "robinhood_chain_total_blocks", "label": "Total Blocks"},
                ],
            },
        ],
    }

    payload = {"series": series, "metadata": metadata}
    OUT_FILE.write_text("window.DASHBOARD_DATA = " + json.dumps(payload, indent=2) + ";\n")
    print(f"Wrote {OUT_FILE} ({sum(len(v) for v in series.values())} data points across {len(series)} series)")


if __name__ == "__main__":
    build()
