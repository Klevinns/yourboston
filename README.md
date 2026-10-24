# FitMap

**Which Greater Boston neighborhoods fit my budget, my commute, and my life?** One map, plain language, and a next step.

FitMap scores every census tract within **10 miles of Boston** from 0 to 100 against *your* priorities: rent or home value, financial comfort (cost burden), commute to *your* workplace (walk, bike, car, transit), access to rapid transit, nearness to a campus, and town-level crime. Click any tract to see why it got its color, and what to do next.

- **Hackachusetts 2026 track:** Civic Action, challenge **V1: Simplified public services** ("One task. Too many sites and agencies." → Plain language. Show the next step.)
- **Live site:** _TBD (GitHub Pages)_
- **Demo video (< 3 min):** _TBD_

> Status: Kiro workspace (steering, spec, hooks) is complete. The application is built by Kiro from `.kiro/specs/fitmap/tasks.md`.

## Features

- Census-tract choropleth (Leaflet canvas) colored by a weighted 0–100 score
- 7 scored criteria: rent (ACS B25064) · buy value (ACS B25077) · cost burden (ACS B25070/B25091) · commute to work · transit accessibility · campus proximity · crime (town-level, EOPSS)
- Per-criterion **must-have** (hard floor) toggle with your own threshold, plus a reorderable **importance rank**. The map recolors live.
- **Rent / Buy** switch
- Click anywhere to set your **work location** (snapped to its tract). Commute is the fastest of the modes you enable.
- Click a tract for a breakdown (value, subscore, weight, contribution, pass/fail), sourced context, and a **next-step** panel
- **Fair-housing guardrail:** language, demographics/race, and EJ2020 status are shown only as sourced text. They are **never scored, filtered, or colored** (see `.kiro/steering/equity.md`).

Buy-value label: *"Median owner-estimated home value, ACS 2020–2024 5-year (B25077). Self-reported; lags market prices. Hatched tracts have high margin of error."*

## Data sources

| Data | Source |
|---|---|
| Tract geometry | US Census TIGER/Line 2024 |
| Rent, value, cost burden, language, demographics | ACS 2020–2024 5-year (B25064, B25077, B25070, B25091, B16001, DP05) |
| Crime | Massachusetts EOPSS NIBRS/UCR, town-level, non-municipal agencies excluded |
| Campuses | MassGIS Colleges & Universities (HigherEd) |
| Transit | MBTA GTFS static feed |
| Walk / bike / car routing | Self-hosted OSRM on OpenStreetMap (© OpenStreetMap contributors) |
| Equity context | MassGIS Environmental Justice 2020 block groups |

Not used: Redfin, Google Distance Matrix, or any other paid API.

## Run it

Requires: `uv`, Node ≥ 20, Docker (for OSRM), and a free Census API key exported as `CENSUS_API_KEY` (never commit it).

```bash
# 0. one-time: enable the repo's git pre-commit guard
git config core.hooksPath scripts/git-hooks

# 1. routing servers (see pipeline/osrm/README.md)
bash pipeline/osrm/build.sh

# 2. build the data (writes web/data/fitmap.geojson)
cd pipeline && uv run python -m fitmap_pipeline build && cd ..
python3 scripts/hooks/check_geojson.py

# 3. tests
(cd pipeline && uv run pytest -q)
npm ci && npm test

# 4. preview locally
python3 -m http.server -d web 8000   # open http://localhost:8000
```

Deploy: push to `main` and `.github/workflows/pages.yml` publishes `web/` to GitHub Pages.

## Repository layout

```
.kiro/steering/     product, tech, structure, equity (always-included steering)
.kiro/specs/fitmap/ requirements.md (EARS) · design.md (+14 correctness properties) · tasks.md
.kiro/hooks/        run-tests-on-save · geojson-check · pre-commit-guard  (CLI V3 JSON)
scripts/hooks/      helper scripts the hooks call
pipeline/           Python precompute (uv)
web/                static site (GitHub Pages root)
```

## How we used Kiro

_Draft. Finish after the build, with real examples._

- **Surface:** Kiro CLI with the V3 agent engine (`--v3`), one surface for the whole build.
- **Spec-driven, not vibe-coded:** we turned the locked design into a Requirements-First feature spec (`.kiro/specs/fitmap/`). Requirements use EARS notation. `design.md` fixes the scoring math (rank-order-centroid weights, mid-rank percentile subscores) and lists 14 correctness properties. `tasks.md` orders the build: pipeline, then frontend, then deploy.
- **Property-based testing:** the scoring rules are written as properties (score in [0,100]; weights sum to 1; deterministic and order-independent; monotonic; a failed must-have excludes the tract; GEOIDs are 11 digits and unique; no negative rent or value; context fields never affect a score). They are tested with hypothesis (Python) and fast-check (JS). _Bugs caught: TBD._
- **Hooks** (`.kiro/hooks/*.json`):
  - `PostFileSave` and `PostTaskExec` run the test suites.
  - `PostFileSave` and `PostFileCreate` on the GeoJSON enforce the 2 MB gzip budget and the schema.
  - `PreToolUse` on shell tools blocks any `git commit` if `.kiro/` is gitignored or a secret is found.
- **Steering:** `product`, `tech`, `structure`, and `equity` are always included. The equity file makes the fair-housing rule (demographics are never scored) a standing instruction, so the agent can't drift from it.
- **MCP / Powers:** _TBD. None configured yet; record any we add._
