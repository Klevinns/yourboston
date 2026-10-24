"""Export stage: merge all interim data into a single GeoJSON FeatureCollection.

Applies:
  - mapshaper geometry simplification (Visvalingam, keep-shapes, 5-decimal precision)
  - base64 Uint8 encoding of commute matrices
  - schema validation matching check_geojson.py

Writes: web/data/fitmap.geojson
"""
from __future__ import annotations

import json
import logging
import math
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd

from .config import (
    ACS_YEAR,
    BPD_INCIDENTS_CSV,
    CAMBRIDGE_INCIDENTS_CSV,
    CENTER,
    CRIME_MIN_POP,
    CRIME_YEAR,
    INTERIM_DIR,
    OUTPUT_GEOJSON,
    RADIUS_KM,
    SOURCES_MD,
)
from .utils import decode_minutes, encode_minutes

log = logging.getLogger(__name__)

# Maximum commute matrix seconds before capping to 255-minute sentinel
MAX_SEC = 254 * 60


def _nan_to_none(v: Any) -> Any:
    """Convert float NaN/inf to None for JSON serialisation."""
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def _to_json_val(v: Any) -> Any:
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        f = float(v)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(v, (np.bool_,)):
        return bool(v)
    return v


NEARBY_CATEGORIES = ["library", "fire_station", "police", "supermarket", "park", "school"]


def build_nearby(row: Any) -> dict[str, dict | None]:
    """Per-category {name, m} for the nearest feature, or None if none in range.

    `row` is a services row (Series or dict) with near_<cat>_name / near_<cat>_m.
    Name may be None (unnamed OSM feature); distance is integer meters.
    """
    out: dict[str, dict | None] = {}
    for cat in NEARBY_CATEGORIES:
        m = _to_json_val(row.get(f"near_{cat}_m"))
        if m is None or (isinstance(m, float) and (math.isnan(m) or math.isinf(m))) or m < 0:
            out[cat] = None
            continue
        name = row.get(f"near_{cat}_name")
        if name is None or (isinstance(name, float) and math.isnan(name)) or not str(name).strip():
            name = None
        else:
            name = str(name).strip()
        out[cat] = {"name": name, "m": int(round(float(m)))}
    return out


def encode_matrix_row(row: np.ndarray) -> str:
    """Encode one row of a duration matrix (seconds) to base64 Uint8."""
    secs = [None if np.isnan(v) else float(v) for v in row]
    return encode_minutes(secs)


def build_geojson(
    tracts_gdf: gpd.GeoDataFrame,
    census_df: pd.DataFrame,
    crime_df: pd.DataFrame,
    campus_df: pd.DataFrame,
    transit_acc_df: pd.DataFrame,
    prose_df: pd.DataFrame,
    services_df: pd.DataFrame,
    walk_mat: np.ndarray,
    bike_mat: np.ndarray,
    car_mat: np.ndarray,
    transit_mat: np.ndarray,
    simplify: bool = True,
) -> dict:
    """Assemble the full GeoJSON FeatureCollection."""
    tracts = tracts_gdf.copy()
    tracts["GEOID"] = tracts["GEOID"].astype(str).str.zfill(11)
    tracts = tracts.sort_values("GEOID").reset_index(drop=True)
    tracts["idx"] = tracts.index

    n = len(tracts)
    log.info("Export: assembling %d features…", n)

    # Index all join tables by GEOID
    def _idx(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["GEOID"] = df["GEOID"].astype(str).str.zfill(11)
        return df.set_index("GEOID")

    census_i      = _idx(census_df)
    crime_i       = _idx(crime_df)
    campus_i      = _idx(campus_df)
    transit_acc_i = _idx(transit_acc_df)
    prose_i       = _idx(prose_df)
    services_i    = _idx(services_df)

    features = []
    for i, row in tracts.iterrows():
        g = row["GEOID"]

        # ── Census ─────────────────────────────────────────────────────────────
        c = census_i.loc[g] if g in census_i.index else {}
        rent        = _to_json_val(c.get("rent"))        if len(c) else None
        rent_moe    = _to_json_val(c.get("rent_moe"))    if len(c) else None
        value       = _to_json_val(c.get("value"))       if len(c) else None
        value_moe   = _to_json_val(c.get("value_moe"))   if len(c) else None
        v_topcoded  = bool(c.get("value_topcoded", False)) if len(c) else False
        v_moe_high  = bool(c.get("value_moe_high", False)) if len(c) else False
        burden      = _to_json_val(c.get("burden"))      if len(c) else None
        burden_r    = _to_json_val(c.get("burden_renter")) if len(c) else None
        burden_o    = _to_json_val(c.get("burden_owner"))  if len(c) else None

        # ── Crime ──────────────────────────────────────────────────────────────
        cr = crime_i.loc[g] if g in crime_i.index else {}
        crime_s = _to_json_val(cr.get("crime_serious"))  if len(cr) else None
        crime_p = _to_json_val(cr.get("crime_property")) if len(cr) else None
        _cy = cr.get("crime_year") if len(cr) else None
        crime_y = int(_cy) if _cy is not None and not (isinstance(_cy, float) and math.isnan(_cy)) else None

        # ── Campus ─────────────────────────────────────────────────────────────
        ca = campus_i.loc[g] if g in campus_i.index else {}
        campus_km   = _to_json_val(ca.get("campus"))      if len(ca) else None
        campus_name = str(ca.get("campus_name")) if len(ca) and ca.get("campus_name") is not None else None

        # ── Transit access ─────────────────────────────────────────────────────
        ta = transit_acc_i.loc[g] if g in transit_acc_i.index else {}
        t_secs    = ta.get("transit_secs") if len(ta) else None
        t_station = str(ta.get("transit_station")) if len(ta) and ta.get("transit_station") is not None else None
        transit_min = round(float(t_secs) / 60.0, 1) if t_secs is not None and not math.isnan(float(t_secs)) else None

        # ── Commute vectors ────────────────────────────────────────────────────
        cm_walk    = encode_matrix_row(walk_mat[i])    if walk_mat    is not None else encode_minutes([None] * n)
        cm_bike    = encode_matrix_row(bike_mat[i])    if bike_mat    is not None else encode_minutes([None] * n)
        cm_car     = encode_matrix_row(car_mat[i])     if car_mat     is not None else encode_minutes([None] * n)
        cm_transit = encode_matrix_row(transit_mat[i]) if transit_mat is not None else encode_minutes([None] * n)

        # ── Prose ctx_ ─────────────────────────────────────────────────────────
        pr = prose_i.loc[g] if g in prose_i.index else {}
        ctx_lang = pr.get("ctx_lang") if len(pr) else None
        ctx_race = pr.get("ctx_race") if len(pr) else None
        ctx_ej   = pr.get("ctx_ej")   if len(pr) else None

        # Deserialise JSON strings (stored as strings in parquet)
        def _deser(v):
            if v is None or (isinstance(v, float) and math.isnan(v)):
                return None
            if isinstance(v, str):
                try:
                    return json.loads(v)
                except Exception:
                    return None
            return v

        ctx_lang = _deser(ctx_lang)
        ctx_race = _deser(ctx_race)
        ctx_ej   = _deser(ctx_ej)

        # ── Services (public-services access index) ─────────────────────────────
        sv = services_i.loc[g] if g in services_i.index else {}
        services = _to_json_val(sv.get("services")) if len(sv) else None
        nearby = build_nearby(sv) if len(sv) else {c: None for c in NEARBY_CATEGORIES}

        # ── NAMELSAD / town ────────────────────────────────────────────────────
        name_col = next((c for c in ["NAMELSAD", "NAME", "GEOID"] if c in tracts.columns), "GEOID")
        name = str(row.get(name_col, g))
        town = str(row.get("town", "Unknown"))

        props = {
            "geoid": g,
            "idx":   int(i),
            "name":  name,
            "town":  town,
            "lat":   float(row.get("INTPTLAT", 0)),
            "lon":   float(row.get("INTPTLON", 0)),
            # Census
            "rent":         rent,
            "rent_moe":     rent_moe,
            "value":        value,
            "value_moe":    value_moe,
            "value_topcoded": v_topcoded,
            "value_moe_high": v_moe_high,
            "burden":        burden,
            "burden_renter": burden_r,
            "burden_owner":  burden_o,
            # Crime
            "crime_serious":  crime_s,
            "crime_property": crime_p,
            "crime_year":     crime_y,
            # Campus
            "campus":      campus_km,
            "campus_name": campus_name,
            # Transit
            "transit":         transit_min,
            "transit_station": t_station,
            # Services
            "services":        services,
            # Nearest named feature per category (panel context; never scored)
            "nearby":          nearby,
            # Commute vectors
            "cm_walk":    cm_walk,
            "cm_bike":    cm_bike,
            "cm_car":     cm_car,
            "cm_transit": cm_transit,
            # Context (prose-only; never scored)
            "ctx_lang": ctx_lang,
            "ctx_race": ctx_race,
            "ctx_ej":   ctx_ej,
        }

        # Geometry
        geom = row.geometry.__geo_interface__

        features.append({"type": "Feature", "properties": props, "geometry": geom})

    fc = {
        "type": "FeatureCollection",
        "metadata": {
            "acs_vintage": f"{ACS_YEAR - 4}-{ACS_YEAR} 5-year",
            "center": list(CENTER),
            "radius_mi": 10.0,
            "built_at": datetime.utcnow().isoformat() + "Z",
        },
        "features": features,
    }
    return fc


def write_geojson(fc: dict, path: Path, simplify: bool = True) -> Path:
    """Write GeoJSON to path, optionally simplifying with mapshaper."""
    path.parent.mkdir(parents=True, exist_ok=True)

    # Write raw GeoJSON first
    raw_path = path.with_suffix(".raw.geojson")
    with open(raw_path, "w", encoding="utf-8") as f:
        json.dump(fc, f, separators=(",", ":"))
    log.info("Export: wrote raw GeoJSON %.1f MB", raw_path.stat().st_size / 1e6)

    if simplify:
        ok = _run_mapshaper(raw_path, path)
        if not ok:
            log.warning("Mapshaper failed; using unsimplified GeoJSON")
            import shutil
            shutil.copy2(raw_path, path)
    else:
        import shutil
        shutil.copy2(raw_path, path)

    # Re-inject metadata foreign member (mapshaper drops unknown top-level keys)
    if "metadata" in fc:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
        doc["metadata"] = fc["metadata"]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(doc, f, separators=(",", ":"))

    raw_path.unlink(missing_ok=True)
    log.info("Export: final GeoJSON %.2f MB at %s", path.stat().st_size / 1e6, path)
    return path


def _run_mapshaper(src: Path, dst: Path) -> bool:
    """Run mapshaper simplification. Returns True on success."""
    cmd = [
        "npx", "--yes", "mapshaper",
        str(src),
        "-simplify", "visvalingam", "keep-shapes", "15%",
        "-o", str(dst), "precision=0.00001", "format=geojson",
    ]
    log.info("Mapshaper: %s", " ".join(cmd))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            log.error("Mapshaper stderr: %s", result.stderr[:500])
            return False
        log.info("Mapshaper: OK")
        return True
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        log.error("Mapshaper error: %s", exc)
        return False


def run(
    tracts_gdf: gpd.GeoDataFrame,
    census_df: pd.DataFrame,
    crime_df: pd.DataFrame,
    campus_df: pd.DataFrame,
    transit_acc_df: pd.DataFrame,
    prose_df: pd.DataFrame,
    services_df: pd.DataFrame,
    walk_mat: np.ndarray,
    bike_mat: np.ndarray,
    car_mat: np.ndarray,
    transit_mat: np.ndarray,
    force: bool = False,
) -> Path:
    """Run full export stage."""
    if OUTPUT_GEOJSON.exists() and not force:
        log.info("Export: %s already exists (use force=True to rebuild)", OUTPUT_GEOJSON)
        return OUTPUT_GEOJSON

    fc = build_geojson(
        tracts_gdf, census_df, crime_df, campus_df,
        transit_acc_df, prose_df, services_df,
        walk_mat, bike_mat, car_mat, transit_mat,
    )
    write_geojson(fc, OUTPUT_GEOJSON, simplify=True)
    _write_sources_md()
    return OUTPUT_GEOJSON


def _write_sources_md() -> None:
    """Write pipeline/data/SOURCES.md with provenance."""
    SOURCES_MD.parent.mkdir(parents=True, exist_ok=True)
    content = f"""# YourBoston Data Sources

Generated: {datetime.utcnow().isoformat()}Z

## Geography
- Census Cartographic Boundary (CB) 2023 MA Census Tracts
  - Source: staged at /root/hackachusetts/data/tiger_tracts/cb_2023_25_tract_500k.shp
  - Vintage: 2023 cartographic boundary (uses 2020 Census tract definitions)

## Census (ACS)
- ACS 2020-2024 5-year estimates
  - Source: Census API (https://api.census.gov/data/2024/acs/acs5)
  - Tables: B25064 (rent), B25077 (home value), B25070 (renter burden), B25091 (owner burden)
  - B01003_001E (population), C16001 (language), DP05 (demographics)
  - Vintage: 2020-2024 (ACS 5-year)
  - Sentinel/annotation codes (any negative value) converted to null before arithmetic.
  - Rent: B25064 top-code "$3,500 or more" reported as 3501.
  - Owner cost burden uses full formula: B25091_008E-011E (with-mortgage 30%+) +
    B25091_019E-022E (without-mortgage 30%+), minus B25091_012E/B25091_023E "not computed".
    Renter uses B25070_007E-010E minus B25070_011E.

## Crime
- Raw incident data: Boston PD ({BPD_INCIDENTS_CSV.name}) + Cambridge PD ({CAMBRIDGE_INCIDENTS_CSV.name})
  - Year: {CRIME_YEAR} (calendar year, latest complete year in both files)
  - Classification: serious = violent, rape, robbery, aggravated assault, burglary,
    larceny, auto theft, arson (Part-I-style list); non-crime keywords filtered;
    all other reported offenses → property (petty).
  - Spatial join: incidents within study-area tracts, Boston PD incidents restricted
    to Boston tracts (no geocoding artifacts in other towns), Cambridge to Cambridge.
  - Population: ACS B01003_001E from census stage.
  - Rate: (serious/petty count / pop) × 1000, rounded to 1 decimal.
  - Low-population rule: tracts with pop < {CRIME_MIN_POP} → null rates.
  - Agency: single-agency municipal data (Boston PD, Cambridge PD); MBTA Transit
    Police, State Police, campus police not included.
  - Suburban towns: FBI NIBRS {CRIME_YEAR} "Offense Type by State and Territory by
    Agency" (statesAndFederal.zip), town-level. Serious = the 8 Part-I index crimes
    (murder, rape, robbery, aggravated assault, burglary, larceny, motor vehicle
    theft, arson); petty = total minus serious. Rate per 1,000 residents using
    ACS 2024 5-year town population. Uniform across the town (no block-level
    precision implied). Municipal (city) agencies only; University/State/Tribal
    agencies excluded.

## Campus
- MassGIS HigherEd colleges
  - Source: /root/hackachusetts-data/civic/colleges/data.csv
  - Metric: great-circle distance (km) to nearest campus

## Public services (access index)
- OpenStreetMap via Overpass API (primary https://overpass.openstreetmap.fr/api/interpreter;
  fallbacks overpass-api.de, overpass.osm.ch)
  - Categories: library, fire station, police, supermarket, park
  - Metric: distance from each tract centroid to the nearest feature per category,
    normalized to [0,1] across tracts, averaged into a "services" index (0 = best access).
  - Parks: point-only (node) parks dropped; park areas use their center point.
  - "Nearby" panel: name + straight-line distance of the nearest feature per
    category, plus schools (amenity=school). Schools are context only: not part
    of the index, no ratings. Nothing within 10 mi → shown as "None within 10 mi".

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
"""
    SOURCES_MD.write_text(content)
    log.info("Wrote %s", SOURCES_MD)
