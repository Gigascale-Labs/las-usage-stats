# las-usage-stats

Looks at several large agent systems and collects usage data. Used on LargeAgentSystems.org.

Scrapers, parsers, and compiled research tracking growth of the agentic ecosystem: skill/plugin registries, agent social networks, on-chain agent activity, orchestration framework adoption, enterprise MCP support, and enterprise agentic AI adoption research.

## Layout

```
scrapers/        one script per data source, each runnable standalone
parsers/          shared time-series helpers (daily forward-fill, snapshot CSV append)
data_outputs/     generated CSV/JSON files (scraper outputs + two hand-compiled research CSVs)
run_all.py        runs every scraper, then the aggregator
compile_agent_stats.py   merges everything into one daily master CSV
```

## Setup

```
pip install -r requirements.txt
playwright install chromium   # only needed for the MoltBook scraper
```

Optional env vars:
- `GITHUB_TOKEN` -- required to get GitHub star *history* (Task 6). Without it, the scraper still records current star-count snapshots, just no daily history. GitHub's stargazer-timestamp endpoint requires auth even for a single low-volume request (confirmed by testing).

## Running

```
python run_all.py                       # everything, in order
python scrapers/clawhub_scraper.py      # or individually
python compile_agent_stats.py           # re-merge without re-scraping
```

The snapshot-based sources (ClawHub installs, EvoMap hub stats, MoltBook, GitHub star snapshots) only accumulate real history if something runs them repeatedly over time -- a single local run just gives you one data point.

### Daily scrape on GitHub Actions

`.github/workflows/daily_scrape.yml` runs `run_all.py` on a daily schedule
(06:17 UTC, plus manual trigger via the Actions tab) and commits everything
in `data_outputs/` back to the repo -- no server of your own needed. Since
your repo is already wired to `git@github.com:Gigascale-Labs/las-usage-stats.git`,
pushing this workflow file is all that's required for it to start running.

A few things make this practical rather than an expensive job every day:

- **ClawHub's incremental cache** (`data_outputs/.clawhub_skills_cache.json`)
  is persisted between runs via `actions/cache`, not git (committing a
  70k+-skill JSON blob daily would bloat repo history fast, and it's
  regenerable state, not source data). Only the very first run ever does
  the full catalog walk -- confirmed by actually running it: ~68k skills,
  ~19 minutes end to end. Every day after that is an incremental fetch of
  just what changed -- seconds, not minutes.
- **Playwright's Chromium binary** (needed for MoltBook) is cached the same
  way, so it's not re-downloaded every run.
- **GitHub star history** (Task 6) actually works in CI without you creating
  a personal token: the workflow passes its own built-in `secrets.GITHUB_TOKEN`
  through as `GITHUB_TOKEN`, which `langgraph_crewai_scraper.py` picks up
  automatically. Running the same script locally without setting that env
  var yourself only gets you star snapshots, not history -- see the caveat
  above.
- If any individual scraper fails, the others still get committed --
  the workflow only fails loudly (red X) at the end, after committing
  whatever did succeed, so one flaky source doesn't block the rest.
- Task 4 and Task 7's CSVs are hand-compiled research (see below), not
  scraped -- `run_all.py` never touches them, so they're stable across runs
  and won't show up as daily diff noise.

## What was validated before writing any code

The original task brief mixed real systems with details that turned out to be inaccurate once checked against live sources and actual API responses (not assumed from documentation alone). Rather than build scrapers against guessed schemas, every endpoint below was hit directly with `curl` and the real response shape is what the scrapers are written against.

| # | Source | Status | What changed from the brief |
|---|---|---|---|
| 1 | ClawHub | Real, API confirmed live | Field is `stats.installs`, not `installsAllTime`. No historical endpoint exists -- true "installs since inception" data doesn't exist anywhere to scrape. Split into two honest series: a real historical *skills-published* series (from each skill's `createdAt`), and an *installs* series that only starts accumulating from this project's first run onward. An authenticated bulk-export endpoint exists but was tested and ruled out: it returns a ZIP of one JSON file per skill, rate-limited to 60 requests/hour at ~250 skills/request -- slower than plain pagination for a catalog this size. A full-catalog scrape (confirmed by running it) takes ~19 minutes for ~68k skills; that's an accepted one-time tradeoff, not a bug. |
| 2 | EvoMap | Real, but the git-clone design was wrong | The EvoMap Hub is a hosted API service (`evomap.ai`), not a git repository -- there's nothing to `git clone`. `.evolver/gep/events.jsonl` is local runtime state that EvoMap's own tooling git-ignores, so it's never committed even in a project's own repo. Redesigned (per your direction) to poll the Hub's real public stats endpoint, `GET https://evomap.ai/a2a/stats`, directly. Same no-history caveat as ClawHub. |
| 3 | MoltBook | Real, brief was accurate | Confirmed it's a client-rendered Next.js SPA with no exposed JSON API for the homepage stats -- Playwright is genuinely required, not just a nice-to-have. Numbers are in the right ballpark (~194-207k human-verified, ~2.85-2.9M total registered) and have actually been *falling* as Meta/Moltbook purge bot-farm accounts post-acquisition -- that's real platform behavior, not a scraper bug. |
| 4 | Enterprise adoption research | Real research houses, one fabricated figure | NVIDIA's 48% telecom figure, Bain's Agentic AI Benchmark, and Gartner's 40%-of-apps forecast are all real and cited. The brief's "Gartner 15.2% cost savings" figure could not be found anywhere after multiple searches -- flagged as unverified in the output rather than silently included. Per your call, this was hand-compiled via web search rather than a scripted search-API integration (no Serper/Tavily/SearxNG key was available). |
| 5 | Olas subgraphs | Real, GraphQL schema in the brief was exactly right | Verified by running the brief's literal `dailyActiveMultisigs_collection` query against the live Gnosis endpoint -- it worked unmodified. Scope narrowed (per your call) to Gnosis + Mode, the two chains servable through Olas's free proxy (`api.subgraph.autonolas.tech`); Base/Optimism/Celo/Ethereum sit behind The Graph's paid decentralized-network gateway. |
| 6 | LangGraph / CrewAI | Real, two API limitations discovered | pypistats.org only retains a rolling ~180-day download window, not full history since inception (BigQuery's public PyPI dataset would be needed for that, which requires GCP credentials). GitHub's stargazer-timestamp endpoint now requires authentication for every call, and its pagination caps out around ~40,000 stars -- CrewAI (~55.5k stars) exceeds that, so even authenticated, its oldest stars aren't fully recoverable via this endpoint. Both caveats are documented in the script and in the CSV structure, not hidden. |
| 7 | MCP enterprise tool register | Real, hand-compiled | Zapier, Workato, SnapLogic, ServiceNow, Salesforce, Microsoft (Copilot Studio + Power Apps/Dataverse), and n8n all confirmed with real announcement/GA dates and source links. Compiled by hand (same reasoning as Task 4) rather than scripted. |

## Known limitations of the master CSV

`compile_agent_stats.py` merges everything into `data_outputs/master_agent_stats_daily.csv` on a daily grid, but not everything in it is equally "real":

- **True daily history**: Olas active-agent counts, PyPI downloads (bounded to ~180 days), GitHub star history (only if `GITHUB_TOKEN` was set).
- **Real historical monthly series, forward-filled to daily**: ClawHub cumulative skills published.
- **Snapshot-only, forward-filled from first scrape**: ClawHub installs/downloads totals, EvoMap hub totals, MoltBook registration counts, GitHub star snapshots (when no token). These have *no* real data before the date this project started running -- the CSV correctly leaves those cells blank (NaN) rather than inventing history.
- **Deliberately excluded from the daily grid**: Task 4 (enterprise adoption research) and Task 7 (MCP tool register) are point-in-time research compilations with their own scattered publication dates, not daily time series. Forcing them into a daily grid would imply false precision. Read `data_outputs/enterprise_adoption_stats.csv` and `data_outputs/mcp_enterprise_support.csv` directly.
