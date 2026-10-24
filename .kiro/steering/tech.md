---
inclusion: always
---

# Technology stack

## Architecture (locked)
Python offline precompute → **one static GeoJSON** → vanilla JS + Leaflet frontend → **GitHub Pages**. No backend, no runtime API calls, no billing.

## Pipeline (offline, Python)
- Python ≥ 3.12, managed with **uv** (`uv run …`). System `python3` has no pip (PEP 668) — never `pip install` globally.
- Libraries: pandas, geopandas, shapely, pyproj, requests. Tests: **pytest + hypothesis** (property-based).
- Geometry simplification: **mapshaper** (`npx mapshaper`), target bundle ≤ 2 MB gzip (see Size budget).
- Routing: **self-hosted OSRM** (Docker, `osrm/osrm-backend`) on a Massachusetts OSM extract, three profiles: `foot`, `bicycle`, `car`, queried via the OSRM `table` service. Car times are **free-flow (no traffic)** — label them so.
- Transit: **MBTA GTFS static feed** (stops, stop_times, trips, calendar). Transit time = walk-to-stop + scheduled in-vehicle time (+ transfers), computed by a Connection Scan over a weekday AM-peak window.
- Census: ACS **2020–2024 5-year** API (`api.census.gov/data/2024/acs/acs5`). Key read from env var `CENSUS_API_KEY`; **never committed, never logged, never written to `.kiro/`.**
- Census sentinel/annotation values (large negative codes such as -666666666, -999999999, -888888888, -222222222, -333333333, -555555555) **must be converted to null** before any math (audit H3). Never treat them as numbers.

## Frontend (static)
- Vanilla ES modules, no build step, no framework. **Leaflet** from CDN with `preferCanvas: true` (canvas renderer for ~450+ polygons).
- All scoring is **client-side** in one pure module (`web/js/scoring.js`) with no DOM access, no `Math.random`, no `Date` — so it is deterministic and property-testable in Node.
- Tests: Node built-in test runner (`node --test`) + **fast-check** (property-based).
- Palette: colorblind-safe sequential (viridis/cividis family). Excluded tracts use a pattern fill, never color alone.

## Data sources (locked — no swaps)
| Use | Source |
|---|---|
| Tract geometry + internal point | Census TIGER/Line 2024 tracts (MA, state FIPS 25), `GEOID`, `INTPTLAT`, `INTPTLON` |
| Rent | ACS B25064 (median gross rent) |
| Buy value | ACS B25077 (median owner-estimated value) — rank/quantile classes, top-coded "$2M+" bin |
| Cost burden | ACS B25070 (renter) + B25091 (owner) |
| Language (prose only) | ACS B16001 |
| Demographics/race (prose only) | ACS DP05 (profile endpoint `acs/acs5/profile`) |
| Equity overlay (prose only) | MassGIS EJ2020 block groups (join on first 11 digits of block-group GEOID) |
| Crime | EOPSS NIBRS/UCR, **town-level** rate, uniform across a town; drop non-municipal agencies (MBTA Transit Police, State Police, campus police) |
| Town membership | TIGER county subdivisions (COUSUB) |
| Campuses | MassGIS HigherEd (staged at `/root/hackachusetts-data/civic/colleges/`, lat/long) |
| Stations | MBTA GTFS `stops.txt` (lat/long; avoids state-plane reprojection) |

## Hard constraints
- **No Google APIs, no Redfin, no paid APIs.**
- Raw downloads go to `pipeline/data/raw/` (gitignored). Only the final `web/data/fitmap.geojson` and small verified lookup JSON are committed.
- Every committed number must be reproducible by `uv run python -m fitmap_pipeline build`.

## Size budget
`web/data/fitmap.geojson`: **gzip size ≤ 2,000,000 bytes** (GitHub Pages serves gzip). Raw size soft cap 6 MB. Enforced by the `geojson-check` hook.

## Commands
- Pipeline tests: `cd pipeline && uv run pytest -q`
- Frontend tests: `node --test web/tests/`
- Local preview: `python3 -m http.server -d web 8000`
