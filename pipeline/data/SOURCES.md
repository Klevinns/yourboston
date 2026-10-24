# FitMap Data Sources

Generated: 2026-10-24T20:26:45.792657Z

## Geography
- Census Cartographic Boundary (CB) 2023 MA Census Tracts
  - Source: staged at /root/hackachusetts/data/tiger_tracts/cb_2023_25_tract_500k.shp
  - Vintage: 2023 cartographic boundary (uses 2020 Census tract definitions)

## Census (ACS)
- ACS 2020-2024 5-year estimates
  - Source: Census API (https://api.census.gov/data/2024/acs/acs5)
  - Tables: B25064 (rent), B25077 (home value), B25070 (renter burden), B25091 (owner burden)
  - B01003_001E (population), B16001 (language), DP05 (demographics)
  - Vintage: 2020-2024 (ACS 5-year)
  - Sentinel/annotation codes (any negative value) converted to null before arithmetic.
  - Rent: B25064 top-code "$3,500 or more" reported as 3501.
  - Owner cost burden uses full formula: B25091_008E-011E (with-mortgage 30%+) +
    B25091_019E-022E (without-mortgage 30%+), minus B25091_012E/B25091_023E "not computed".
    Renter uses B25070_007E-010E minus B25070_011E.

## Crime
- Raw incident data: Boston PD (bpd_crime_current.csv) + Cambridge PD (cambridge_crime.csv)
  - Year: 2025 (calendar year, latest complete year in both files)
  - Classification: serious = violent, rape, robbery, aggravated assault, burglary,
    larceny, auto theft, arson (Part-I-style list); non-crime keywords filtered;
    all other reported offenses → property (petty).
  - Spatial join: incidents within study-area tracts, Boston PD incidents restricted
    to Boston tracts (no geocoding artifacts in other towns), Cambridge to Cambridge.
  - Population: ACS B01003_001E from census stage.
  - Rate: (serious/petty count / pop) × 1000, rounded to 1 decimal.
  - Low-population rule: tracts with pop < 100 → null rates.
  - Agency: single-agency municipal data (Boston PD, Cambridge PD); MBTA Transit
    Police, State Police, campus police not included.
  - Other towns: null pending future enrichment.

## Campus
- MassGIS HigherEd colleges
  - Source: /root/hackachusetts-data/civic/colleges/data.csv
  - Metric: great-circle distance (km) to nearest campus

## Transit Accessibility
- MBTA GTFS static feed
  - Source: /root/hackachusetts/data/MBTA_GTFS.zip
  - Stations: route_type 0 (light rail), 1 (heavy rail), 2 (commuter rail)
  - Metric: OSRM foot-profile walk time to nearest station

## Routing
- Self-hosted OSRM (Massachusetts OSM extract)
  - Profiles: foot, bicycle, car (free-flow, no traffic)
  - Pre-built routing graphs at /root/hackachusetts/data/*.osrm
  - Car times are free-flow only; labeled as such in the UI.

## Environmental Justice
- MassGIS EJ2020
  - Staged extract: /root/hackachusetts-data/civic/ej/data.csv (municipality-keyed)
  - Live fallback (not reachable in this environment):
    https://gis.massgis.state.ma.us/arcgis/rest/services/MGIS/EJ2020/MapServer/0
  - Joined to tracts at the MUNICIPALITY level (the staged file lacks block-group GEOID).
  - Used for context prose only; never scored, filtered, ranked, or colored.
