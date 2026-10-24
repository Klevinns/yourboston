"""Geography stage: TIGER tracts → 10-mile study area.

Reads:
  - TIGER/Line 2024 MA census tracts shapefile (staged at config.TIGER_TRACTS_SHP)
  - Precomputed tracts_10mi.csv (GEOID + internal-point lat/lon for study area)

Writes:
  - pipeline/data/interim/tracts.parquet  — GeoDataFrame with geometry + town fields

Pure functions at module scope are tested by hypothesis.
"""
from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd

from .config import (
    CENTER,
    INTERIM_DIR,
    RADIUS_KM,
    STAGED_DIR,
    TIGER_TRACTS_SHP,
    TRACTS_10MI_CSV,
)
from .utils import haversine_km, in_radius

log = logging.getLogger(__name__)


def load_tiger_tracts() -> gpd.GeoDataFrame:
    """Load TIGER/Line MA census tracts from staged shapefile (EPSG:4326)."""
    if TIGER_TRACTS_SHP.exists():
        log.info("Loading tracts from %s", TIGER_TRACTS_SHP)
        gdf = gpd.read_file(TIGER_TRACTS_SHP)
    else:
        zip_path = STAGED_DIR / "cb_2023_25_tract_500k.zip"
        if not zip_path.exists():
            raise FileNotFoundError(
                f"TIGER tracts not found at {TIGER_TRACTS_SHP} or {zip_path}"
            )
        log.info("Loading tracts from zip %s", zip_path)
        gdf = gpd.read_file(f"zip://{zip_path}")

    # Normalise to WGS84
    if gdf.crs is None:
        gdf = gdf.set_crs(epsg=4326)
    elif gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(epsg=4326)
    return gdf


def _ensure_geoid(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Guarantee an 11-digit GEOID string column."""
    if "GEOID" in gdf.columns:
        gdf["GEOID"] = gdf["GEOID"].astype(str).str.strip().str.zfill(11)
    elif "GEOID20" in gdf.columns:
        gdf["GEOID"] = gdf["GEOID20"].astype(str).str.zfill(11)
    elif all(c in gdf.columns for c in ["STATEFP", "COUNTYFP", "TRACTCE"]):
        gdf["GEOID"] = (
            gdf["STATEFP"].astype(str).str.zfill(2)
            + gdf["COUNTYFP"].astype(str).str.zfill(3)
            + gdf["TRACTCE"].astype(str).str.zfill(6)
        )
    else:
        raise KeyError("Cannot derive GEOID from TIGER columns: " + str(list(gdf.columns)))
    return gdf


def run(force: bool = False) -> gpd.GeoDataFrame:
    """Build the study-area GeoDataFrame (geometry + coords + town + idx)."""
    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    out_path = INTERIM_DIR / "tracts.parquet"

    if out_path.exists() and not force:
        log.info("Geography: loading cached %s", out_path)
        return gpd.read_parquet(out_path)

    # ── Load full TIGER + derive GEOID ────────────────────────────────────────
    tiger = load_tiger_tracts()
    tiger = _ensure_geoid(tiger)

    # ── Internal-point coordinates ────────────────────────────────────────────
    # TIGER cb_* files have no INTPTLAT/INTPTLON; use precomputed CSV.
    if TRACTS_10MI_CSV.exists():
        log.info("Geography: using precomputed tracts_10mi.csv for study area")
        coord_df = pd.read_csv(TRACTS_10MI_CSV, dtype={"GEOID": str})
        coord_df["GEOID"] = coord_df["GEOID"].astype(str).str.strip().str.zfill(11)
        study_geoids = set(coord_df["GEOID"])

        gdf = tiger[tiger["GEOID"].isin(study_geoids)].copy()
        coord_map = coord_df.set_index("GEOID")
        gdf["INTPTLAT"] = gdf["GEOID"].map(coord_map["lat"])
        gdf["INTPTLON"] = gdf["GEOID"].map(coord_map["lon"])
    else:
        # Fall back: compute internal points from geometry, radius-filter
        log.info("Geography: computing internal points + radius filter")
        reps = tiger.geometry.representative_point()
        tiger["INTPTLAT"] = reps.y
        tiger["INTPTLON"] = reps.x
        mask = tiger.apply(lambda r: in_radius(r["INTPTLAT"], r["INTPTLON"]), axis=1)
        gdf = tiger[mask].copy()

    # ── NAMELSAD ──────────────────────────────────────────────────────────────
    if "NAMELSAD" not in gdf.columns:
        gdf["NAMELSAD"] = gdf.get("NAME", gdf["GEOID"])

    # ── Sort + idx ─────────────────────────────────────────────────────────────
    gdf = gdf.sort_values("GEOID").reset_index(drop=True)
    gdf["idx"] = gdf.index

    # ── Town join ─────────────────────────────────────────────────────────────
    gdf = _join_towns(gdf)

    # Keep only needed columns + geometry to avoid parquet metadata issues
    keep = ["GEOID", "idx", "NAMELSAD", "INTPTLAT", "INTPTLON", "town", "geometry"]
    keep = [c for c in keep if c in gdf.columns]
    gdf = gdf[keep].set_geometry("geometry")
    gdf = gdf.set_crs(epsg=4326, allow_override=True)

    gdf.to_parquet(out_path)
    log.info("Geography: wrote %s (%d tracts)", out_path, len(gdf))
    return gdf


def _join_towns(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Assign each tract's town by point-in-polygon against COUSUB, else fallback."""
    gdf = gdf.copy()

    try:
        cousub_zip = INTERIM_DIR.parent / "raw" / "tl_2024_25_cousub.zip"
        cousub_candidates = (
            list(STAGED_DIR.glob("*COUSUB*.shp"))
            + list(STAGED_DIR.glob("*cousub*.shp"))
            + list((STAGED_DIR / "tiger_tracts").glob("*cousub*.shp"))
        )
        if cousub_zip.exists():
            cousub = gpd.read_file(f"zip://{cousub_zip}")
        elif cousub_candidates:
            cousub = gpd.read_file(cousub_candidates[0])
        else:
            cousub = None

        if cousub is not None:
            geom_name = cousub.geometry.name
            # Rename non-geometry columns to upper, keep geometry col intact
            rename = {c: c.upper() for c in cousub.columns if c != geom_name}
            cousub = cousub.rename(columns=rename)
            name_col = next((c for c in ["NAME", "NAMELSAD"] if c in cousub.columns), None)
            if name_col:
                cousub = cousub[[name_col, geom_name]].rename(
                    columns={name_col: "town", geom_name: "geometry"}
                ).set_geometry("geometry")
                if cousub.crs and cousub.crs.to_epsg() != 4326:
                    cousub = cousub.to_crs(epsg=4326)

                pts = gdf.copy()
                pts = pts.set_geometry(
                    gpd.points_from_xy(pts["INTPTLON"], pts["INTPTLAT"]),
                    crs="EPSG:4326",
                )
                joined = gpd.sjoin(
                    pts[["GEOID", "geometry"]], cousub, how="left", predicate="within"
                )
                joined = joined.drop_duplicates("GEOID").set_index("GEOID")
                gdf["town"] = gdf["GEOID"].map(joined["town"]).fillna("Unknown")
                log.info("Town join: assigned via COUSUB spatial join")
                return gdf
    except Exception as exc:  # noqa: BLE001
        log.warning("COUSUB join failed (%s); using county fallback", exc)

    # Fallback via county FIPS
    county_town_map = {
        "009": "Essex County",
        "017": "Middlesex County",
        "021": "Norfolk County",
        "023": "Plymouth County",
        "025": "Suffolk County",
    }
    gdf["town"] = gdf["GEOID"].str[2:5].map(county_town_map).fillna("Unknown")
    return gdf
