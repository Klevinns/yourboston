"""FitMap pipeline configuration — locked parameters."""
from __future__ import annotations

from pathlib import Path

# ── Study area ────────────────────────────────────────────────────────────────
# Boston City Hall — chosen as the canonical center (design.md D1).
CENTER = (42.3603, -71.0580)   # (lat, lon) decimal degrees WGS-84
RADIUS_MI = 10.0
RADIUS_KM = 16.0934             # 10 statute miles = 16.0934 km (locked)

# ── Census ─────────────────────────────────────────────────────────────────────
STATE = "25"                    # Massachusetts FIPS
COUNTIES = ["009", "017", "021", "023", "025"]  # Essex,Middlesex,Norfolk,Plymouth,Suffolk
ACS_YEAR = 2024

# B25064 (rent) top-code: $3,500+ per month for 2020-2024 5-year ACS.
# Source: census.gov/programs-surveys/acs/technical-documentation/
#         code-lists/2024/codeList-acs-rent.html (value 3501 is the bin
#         "$3,500 or more" reported as 3501.)
RENT_TOPCODE = 3_501            # ACS B25064 top-code threshold, $/month
VALUE_TOPCODE = 2_000_000       # ACS B25077 top-code threshold, $ (design.md §components)

# ── Paths ─────────────────────────────────────────────────────────────────────
_HERE = Path(__file__).parent
_PIPELINE = _HERE.parent.parent  # pipeline/

# Staged input data (pre-downloaded per environment brief)
STAGED_DIR = Path("/root/hackachusetts/data")

# TIGER shapefiles (staged under tiger_tracts/ or as a zip at top level)
TIGER_TRACTS_SHP = STAGED_DIR / "tiger_tracts" / "cb_2023_25_tract_500k.shp"
TIGER_ZIP        = STAGED_DIR / "cb_2023_25_tract_500k.zip"
TRACTS_10MI_CSV  = STAGED_DIR / "tracts_10mi.csv"

# ACS pre-staged CSVs (scored and prose axes)
ACS_SCORED_CSV   = STAGED_DIR / "acs_scored_axes.csv"
ACS_PROSE_CSV    = STAGED_DIR / "acs_prose_axes.csv"
ACS_RACE_CSV     = STAGED_DIR / "acs_race.csv"

# Crime raw incident files
CRIME_YEAR = 2025               # Latest complete calendar year in both raw files
CRIME_MIN_POP = 100             # Minimum population for meaningful rate
BPD_INCIDENTS_CSV = STAGED_DIR / "bpd_crime_current.csv"
BPD_OFFENSE_CODES_XLSX = STAGED_DIR / "bpd_offense_codes.xlsx"
CAMBRIDGE_INCIDENTS_CSV = STAGED_DIR / "cambridge_crime.csv"

# MBTA GTFS static zip
GTFS_ZIP = STAGED_DIR / "MBTA_GTFS.zip"

# MassGIS HigherEd colleges
COLLEGES_CSV = Path("/root/hackachusetts-data/civic/colleges/data.csv")

# OSRM base paths (pre-built routing graphs)
OSRM_DIR  = STAGED_DIR
OSRM_FOOT = STAGED_DIR / "foot.osrm"
OSRM_BIKE = STAGED_DIR / "bicycle.osrm"
OSRM_CAR  = STAGED_DIR / "car.osrm"

# OSRM ports when servers are running
OSRM_FOOT_PORT    = 5001
OSRM_BIKE_PORT    = 5002
OSRM_CAR_PORT     = 5003

# Pipeline interim artefacts (gitignored)
INTERIM_DIR = _PIPELINE / "data" / "interim"

# Output
OUTPUT_GEOJSON = _PIPELINE.parent / "web" / "data" / "fitmap.geojson"
SOURCES_MD     = _PIPELINE / "data" / "SOURCES.md"
