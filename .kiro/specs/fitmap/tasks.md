# Implementation Plan — YourBoston

Ordered build plan. Pipeline (1–9) → frontend (10–15) → deploy (16–17). Each task references the requirements it satisfies and the correctness properties (design.md) it must test. Tasks marked `*` are optional. Do not write data you could not actually fetch — if a source is unreachable, stop and report, or apply the documented null fallback.

- [ ] 1. Scaffold pipeline and frontend test harnesses
  - Create `pipeline/pyproject.toml` (uv project `fitmap-pipeline`; deps pandas, geopandas, shapely, pyproj, requests, pyarrow; dev deps pytest, hypothesis) and `pipeline/src/fitmap_pipeline/{__init__,__main__,config}.py`.
  - `config.py`: CENTER=(42.3603, -71.0580), RADIUS_MI=10.0, STATE="25", COUNTIES=["009","017","021","023","025"], ACS_YEAR=2024, paths.
  - Root `package.json` with devDependency `fast-check` and script `"test": "node --test web/tests/"`.
  - Verify `cd pipeline && uv run pytest -q` and `npm test` both run (zero tests OK).
  - _Requirements: 11.1_

- [ ] 2. Geography: TIGER tracts, 10-mile filter, town join
  - [ ] 2.1 Implement `in_radius` (haversine, 16.0934 km) and `geography` stage: download TIGER/Line 2024 MA tracts + COUSUB, keep tracts whose INTPTLAT/INTPTLON is in radius, attach COUSUB town by point-in-polygon of the internal point, sort by GEOID, assign `idx`.
    - _Requirements: 1.1, 1.3, 1.4, 4.3_
  - [ ] 2.2 Property tests: Property 13 (radius membership), Property 8 (GEOID `^\d{11}$`, unique, sorted, `idx` = position) on stage output.
    - _Requirements: 1.1, 1.3, 1.4_

- [ ] 3. Census ACS ingest with sentinel nulling
  - [ ] 3.1 Implement `null_sentinels` and `census` stage: fetch B25064, B25077 (+MOE), B25070, B25091 for study-area tracts from `api.census.gov/data/2024/acs/acs5` using `CENSUS_API_KEY` from env (fail fast if unset; never print it). Null every negative value before any arithmetic.
    - Look up and record the B25064 top-code in `config.py` from Census documentation; set `value_topcoded = value >= 2000000`; `value_moe_high = value_moe / value > 0.30`.
    - _Requirements: 2.1, 2.2, 2.4, 2.5, 2.6_
  - [ ] 3.2 Implement `burden_pct` exactly per design.md (B25070_007–010 + B25091_008–011, 019–022 over computed totals); emit `burden`, `burden_renter`, `burden_owner`.
    - _Requirements: 3.1, 3.2, 3.3_
  - [ ] 3.3 Property tests: Property 10 (sentinels never survive), Property 9 (no negatives; burden ≤ 100) with hypothesis generating sentinel-laden raw rows; one example test with a hand-computed tract.
    - _Requirements: 2.2, 2.3, 3.3_

- [ ] 4. Crime: BPD/Cambridge PD tract-level + FBI NIBRS town-level rates
  - [ ] 4.1 Implement `crime` stage: obtain Boston PD + Cambridge PD incident reports and FBI NIBRS offense counts by agency for the most recent complete year; drop MBTA Transit Police, State Police, and college/university police agencies; compute per-1,000 rates `crime_serious` (crimes against persons) and `crime_property` using town population; map agency → COUSUB town; record URL, year, and download date in `pipeline/data/SOURCES.md`.
    - If the data cannot be obtained, write null crime fields and note it in SOURCES.md — never fabricate.
    - _Requirements: 4.1, 4.2, 4.3_
  - [ ] 4.2 Property test for `town_rate`: result ≥ 0 or None; None when population ≤ 0.
    - _Requirements: 2.3_

- [ ] 5. Campus proximity
  - Implement `campus` stage from `/root/hackachusetts-data/civic/colleges/data.csv` (copy into `pipeline/data/raw/`): nearest campus great-circle km + name per tract internal point. Test: distance ≥ 0.
  - _Requirements: 5.1_

- [ ] 6. Stations and self-hosted OSRM
  - [ ] 6.1 `stations` stage: download MBTA GTFS static zip; select stops (`location_type` 1, or stops with no parent) served by `route_type` ∈ {0,1,2}; save lat/lon/name.
    - _Requirements: 5.2_
  - [ ] 6.2 Add `pipeline/osrm/README.md` + `pipeline/osrm/build.sh`: download Geofabrik Massachusetts `.osm.pbf`, run `osrm-extract`/`osrm-partition`/`osrm-customize` for `foot`, `bicycle`, `car` profiles in Docker `osrm/osrm-backend`, serve on ports 5001/5002/5003. Do not commit `.osrm` artefacts.
    - _Requirements: 6.1, 6.4_
  - [ ] 6.3 `osrm` stage: OSRM `table` client with chunking; compute tract→tract duration matrices for walk/bike/car and tract→nearest-station foot time (`transit`, `transit_station`). Fail loudly if a server is down.
    - _Requirements: 5.2, 6.1_

- [ ] 7. Transit commute matrix (MBTA GTFS)
  - [ ] 7.1 `transit` stage: Connection Scan Algorithm over next-Wednesday weekday service; access/egress by OSRM foot to stops within 1.2 km; transfers via `transfers.txt` and shared `parent_station`; per pair take min(transit, walk); median over 07:30/08:00/08:30 departures.
    - _Requirements: 6.1, 6.2_
  - [ ] 7.2 Tests: diagonal (tract→itself) is 0; transit ≤ walk for every pair; a known Red Line pair (e.g. Harvard Sq tract → Downtown Crossing tract) is under 30 min.
    - _Requirements: 6.2_

- [ ] 8. Prose context fields (never scored)
  - `prose` stage: fetch B16001 (fallback C16001 if tract-level B16001 unavailable — note which), DP05 race/ethnicity shares resolved by variable label from the DP05 groups endpoint, and EJ2020 block groups with `GEOID,EJ,EJ_CRIT_DESC` (returnGeometry=false); aggregate to tract (first 11 digits). Emit only under `ctx_lang`, `ctx_race`, `ctx_ej`.
  - _Requirements: 10.2, 12.1, 12.3_

- [ ] 9. Export the single static GeoJSON
  - [ ] 9.1 Implement `encode_minutes`/`decode_minutes` and `export` stage: merge all stages into one FeatureCollection per design.md Data Models; encode `cm_walk/bike/car/transit`; add `metadata`; simplify with `npx mapshaper -simplify visvalingam keep-shapes` and `-o precision=0.00001`; write `web/data/fitmap.geojson`. Add `build` stage that runs 2→9 in order.
    - _Requirements: 6.3, 11.1, 11.2_
  - [ ] 9.2 Property test: Property 11 (commute round-trip) and Property 8/9 on the final file.
    - _Requirements: 1.3, 2.3, 6.3_
  - [ ] 9.3 Run `python3 scripts/hooks/check_geojson.py web/data/fitmap.geojson` (stdlib-only script) — must pass (≤ 2,000,000 bytes gzip, schema OK). If over budget, increase simplification before touching vectors.
    - _Requirements: 11.2_

- [ ] 10. Checkpoint — pipeline green
  - All pipeline tests pass; GeoJSON check passes; `pipeline/data/SOURCES.md` lists every source URL, vintage, and download date. Ask the user before continuing if any source fell back to null.

- [ ] 11. Pure scoring module
  - [ ] 11.1 Implement `web/js/scoring.js` exactly per design.md (CRITERIA, `rocWeights`, `percentileTables`, `subscore`, `evaluate`, `scoreAll`). No DOM, no Leaflet, no `Math.random`, no `Date`, no `ctx_` references.
    - _Requirements: 7.1–7.9, 8.2, 8.3, 9.1, 12.2_
  - [ ] 11.2 fast-check property tests in `web/tests/prop.scoring.test.js`: Property 1 (bounds), 2 (weights), 3 (determinism), 4 (order independence), 5 (monotonicity), 6 (must-have exclusion), 7 (rank swap), 12 (ctx never scored, incl. source-text grep), 14 (subscore bounds/orientation). ≥ 200 runs each.
    - _Requirements: 7.2, 7.3, 7.4, 7.7, 7.8, 8.2, 12.2_

- [ ] 12. Leaflet canvas choropleth
  - `web/index.html`, `web/css/app.css`, `web/js/data.js` (fetch + base64 decode to Uint8Array per mode), `web/js/map.js`: `L.map` with `preferCanvas: true`, OSM tiles with attribution, 5-class colorblind-safe palette on score, grey diagonal pattern for excluded, grey dots for no-data, legend. Buy view: quintile classes + "$2M+" bin + MOE hatching + verbatim B25077 label.
  - _Requirements: 8.2, 8.3, 9.2, 9.3, 9.4, 9.5_

- [ ] 13. Controls: priorities, must-haves, Rent/Buy, work location
  - `web/js/controls.js`: Rent/Buy switch; per active criterion rank ↑/↓ buttons, must-have checkbox, threshold input (units); defaults (budget + commute must-have, thresholds "no limit", prompt for budget and work location); mode checkboxes; crime scope radio; "Set work location" → click snaps to containing tract (point-in-polygon) or shows out-of-area message; every change re-runs `scoreAll` and restyles without reload; ARIA live region announces passing-tract count. No control bound to any `ctx_` field.
  - _Requirements: 6.5, 6.6, 6.7, 6.8, 8.1, 8.4, 8.5, 8.6, 8.8, 9.1, 10.5, 12.1_

- [ ] 14. Breakdown, context prose, next-step panel, empty state
  - [ ] 14.1 `web/js/panel.js`: on tract click show score or exclusion reason; per-criterion value+units, subscore, weight, contribution, pass/fail, missing flags; town-level crime note; free-flow car note; neutral sourced `ctx_` prose (equity.md wording rules).
    - _Requirements: 4.4, 6.8, 7.5, 7.6, 10.1, 10.2, 12.3_
  - [ ] 14.2 `web/data/next-steps.json` with ≥ 3 plain-language actions (e.g. Mass 211, Boston.gov housing, Mass.gov housing search, MBTA trip planner); verify each URL returns HTTP 200 with `curl -sI` and record `verified` date. Render in the panel.
    - _Requirements: 10.3, 10.4_
  - [ ] 14.3 Empty state: when zero tracts pass, name the must-have excluding the most tracts and suggest relaxing it.
    - _Requirements: 8.7_

- [ ]* 15. Accessibility and polish pass
  - Keyboard-only walkthrough of the demo path; contrast check; reduced-motion; plain-language copy review (~8th grade).
  - _Requirements: 10.5_

- [ ] 16. Deploy to GitHub Pages
  - `.github/workflows/pages.yml`: on push to `main`, run `npm ci && npm test`, run `check_geojson.py`, then `actions/upload-pages-artifact` with path `web` and `actions/deploy-pages`. No secrets needed (data is prebuilt and committed).
  - _Requirements: 11.3_

- [ ] 17. Submission readiness
  - README: live URL, run instructions (pipeline + OSRM + local preview), data sources with vintages, track ("Civic Action — V1"), and the "How we used Kiro" write-up (CLI V3 surface, spec-driven vs vibe, hooks, steering, MCP, PBT bugs caught, powers). Confirm `.kiro/` is committed and not ignored; run `scripts/hooks/check_kiro_committed.sh`.
  - _Requirements: 11.4, 11.5_
