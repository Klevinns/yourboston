---
inclusion: always
---

# Project structure

```
/                         repo root (GitHub: public)
├── .kiro/                COMMITTED. Never gitignored (hackathon DQ rule).
│   ├── steering/         product.md, tech.md, structure.md, equity.md
│   ├── specs/fitmap/     requirements.md, design.md, tasks.md
│   └── hooks/            *.json (CLI V3 standalone hook files)
├── scripts/
│   ├── hooks/            shell/python helpers invoked by .kiro/hooks/*.json
│   └── git-hooks/        native git hooks (core.hooksPath)
├── pipeline/             offline Python precompute (uv project)
│   ├── pyproject.toml
│   ├── src/fitmap_pipeline/
│   │   ├── __main__.py   CLI: `python -m fitmap_pipeline <stage>`
│   │   ├── config.py     center point, radius, counties, years, paths
│   │   ├── census.py     ACS fetch + sentinel nulling
│   │   ├── geography.py  TIGER tracts, radius filter, COUSUB town join
│   │   ├── crime.py      EOPSS town-level rates
│   │   ├── campus.py     nearest-campus distance
│   │   ├── stations.py   GTFS rapid-transit/commuter-rail stations
│   │   ├── osrm.py       OSRM table client (walk/bike/car)
│   │   ├── transit.py    GTFS connection-scan commute
│   │   ├── prose.py      B16001 / DP05 / EJ2020 context (never scored)
│   │   └── export.py     single GeoJSON writer + encoder
│   ├── tests/            pytest + hypothesis
│   └── data/raw/         downloads (gitignored)
├── web/                  static site root (deployed to GitHub Pages)
│   ├── index.html
│   ├── css/app.css
│   ├── js/
│   │   ├── scoring.js    PURE scoring module (no DOM) — the only place scores are computed
│   │   ├── data.js       load + decode GeoJSON / commute vectors
│   │   ├── map.js        Leaflet canvas choropleth, patterns, legend
│   │   ├── controls.js   rent/buy switch, must-have toggles, rank reorder, thresholds
│   │   └── panel.js      breakdown, prose context, next-step panel
│   ├── data/
│   │   ├── fitmap.geojson
│   │   └── next-steps.json   verified links only
│   └── tests/            node --test + fast-check
├── package.json          dev deps only (fast-check); no bundler
├── .github/workflows/pages.yml
└── README.md             run instructions + "How we used Kiro" write-up
```

## Conventions
- Python: snake_case, type hints, pure functions for every transform so hypothesis can test them; I/O only in `__main__.py` / fetch helpers.
- JS: ES modules, camelCase, `export function`; `scoring.js` imports nothing from the DOM or Leaflet.
- GEOID is always a **string** of 11 digits (never int — leading zeros matter elsewhere and JSON ints lose meaning).
- Feature order in the GeoJSON is sorted by GEOID; property `idx` equals the feature's array position and indexes the commute vectors.
- Criterion keys (exactly seven, used everywhere): `rent`, `value`, `burden`, `commute`, `transit`, `campus`, `crime`.
- Prose-only property keys are prefixed `ctx_` and must never be read by `scoring.js`.
- Tests live beside their package (`pipeline/tests`, `web/tests`); property tests are named `test_prop_*` / `prop.*.test.js`.
