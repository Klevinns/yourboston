# FitMap

**Which Greater Boston neighborhoods fit my budget, my commute, and my life?** One map, plain language, and a next step.

FitMap scores every census tract within **10 miles of Boston** from 0 to 100 against *your* priorities: rent or home value, financial comfort (cost burden), commute to *your* workplace (walk, bike, car, transit), access to rapid transit, nearness to a campus, and town-level crime. Click any tract to see why it got its color, and what to do next.

- **Hackachusetts 2026 track:** Civic Action, challenge **V1: Simplified public services** ("One task. Too many sites and agencies." → Plain language. Show the next step.)
- **Live site:** _publish by pushing to `main` (GitHub Pages workflow included)_
- **Demo video (< 3 min):** _TBD_

> Status: Built. The pipeline produces `web/data/fitmap.geojson` (487 tracts, ~848 KB gzip, well under the 2 MB budget) and the static frontend scores and renders it. All pipeline tests (pytest + hypothesis) and frontend tests (node --test + fast-check) pass.

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

### Data notes & honest limitations

- **Study area:** 487 tracts whose TIGER internal point is within 10 statute miles of Boston City Hall (42.3603, −71.0580).
- **Crime:** Boston PD + Cambridge PD incident reports, calendar year 2025 (latest complete year), tract-level rates (not town-level). Other towns are null pending enrichment. Low-population tracts (pop < 100) return null rates.
- **Owner cost burden:** full ACS formula using B25091_008E-011E (with-mortgage 30%+) + B25091_019E-022E (without-mortgage 30%+); renter uses B25070_007E-010E. Documented in `pipeline/data/SOURCES.md`.
- **Language (B16001):** not published at the tract level in the staged 2024 5-year extract, so the language context panel is omitted where empty. Demographics (B02001/B03002) are complete.
- **EJ2020:** joined at the **municipality** level from a staged MassGIS extract (the staged file is keyed by town, not block-group GEOID). Presented as municipality-level context, never scored.
- **Car times** are OSRM free-flow (no traffic) and labeled as such in the UI.

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

- **Surface:** Kiro CLI with the V3 agent engine — one surface drove the whole build from `.kiro/specs/fitmap/tasks.md`.
- **Spec-driven, not vibe-coded:** the locked design became a Requirements-First feature spec (`.kiro/specs/fitmap/`). Requirements use EARS notation. `design.md` fixes the scoring math (rank-order-centroid weights, mid-rank percentile subscores) and lists 14 correctness properties. `tasks.md` ordered the build: pipeline (1–10), then frontend (11–15), then deploy (16–17). Kiro executed each task, ran the tests, and fixed failures before moving on.
- **Property-based testing (the heart of the build):** the scoring rules are written as properties and tested with hypothesis (Python) and fast-check (JS), ≥ 100–300 generated cases each:
  - Score always in [0, 100]; ROC weights positive, strictly decreasing, summing to 1.
  - Deterministic and feature-order-independent.
  - Monotone: improving one criterion never lowers a tract's score.
  - A failed must-have excludes the tract (no score, listed in `failed`).
  - GEOIDs are 11 digits, unique, sorted; `idx` = position.
  - No negative rent/value/MOE/burden/crime/distance; burden ≤ 100.
  - Census sentinels (−666666666 etc.) never survive to arithmetic.
  - Commute base64 Uint8 round-trips (255 = no route / over cap).
  - **Equity property 12:** changing any `ctx_` field never changes a score, and `scoring.js` source contains no `ctx_` reference (grep-enforced in a test).
  - **Bugs caught during the build:** the combined cost-burden field came out all-null because the staged ACS extract lacks the owner B25091 detail columns the design assumed — the property/schema checks surfaced it immediately, and we adapted to the available columns with a documented approximation. Mapshaper silently dropped the top-level `metadata` foreign member; the output schema test caught the missing key and we re-inject it after simplification.
- **Hooks** (`.kiro/hooks/*.json`):
  - `run-tests-on-save` runs the pytest and node test suites on file save / task completion.
  - `geojson-check` enforces the 2 MB gzip budget and the GeoJSON schema whenever the output changes.
  - `pre-commit-guard` blocks a commit if `.kiro/` is gitignored or a secret (including the live `CENSUS_API_KEY`) appears in a staged file.
- **Steering:** `product`, `tech`, `structure`, and `equity` are always included. The equity file makes the fair-housing rule (demographics / language / EJ are never scored, filtered, ranked, or colored) a standing instruction, so the agent can't drift from it — the `ctx_` prefix + the grep test make it mechanically enforceable.
- **MCP / Powers:** none required for this build; the whole pipeline runs on local staged data + self-hosted OSRM, with no runtime API.
