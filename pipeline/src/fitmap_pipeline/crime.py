"""Crime stage: compute tract-level crime rates from raw incident files.

Uses Boston PD and Cambridge PD incident CSVs, classifies each incident as
serious/petty/noncrime, spatial-joins to tracts, and computes rates per 1,000
residents using ACS B01003 population.

Writes: pipeline/data/interim/crime.parquet
"""
from __future__ import annotations

import logging
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from .config import (
    BPD_INCIDENTS_CSV,
    BPD_OFFENSE_CODES_XLSX,
    CAMBRIDGE_INCIDENTS_CSV,
    CRIME_MIN_POP,
    CRIME_YEAR,
    INTERIM_DIR,
)
from .utils import null_sentinels, town_rate

log = logging.getLogger(__name__)


# ── Classification functions (ported from process scripts) ────────────────────

def classify_bpd(code: str, name_by_code: dict[str, str]) -> str:
    """Classify a BPD offense code as serious/petty/noncrime.

    Serious = violent + rape + robbery + agg_assault + burglary + larceny +
              auto_theft + arson per the design spec.
    """
    # Serious offense codes (from process_bpd.py)
    violent = {"111", "112", "114", "121", "122", "123", "124", "125"}
    rape = {str(c) for c in range(211, 272)}
    robbery = {str(c) for c in range(301, 382)}
    agg_assault = {str(c) for c in range(401, 434)}
    burglary = {str(c) for c in range(510, 561)}
    larceny = {str(c) for c in range(611, 640)}
    auto_theft = {"723", "724", "727"}
    arson = {"900", "901", "902", "905", "906", "907", "920", "930"}
    serious = violent | rape | robbery | agg_assault | burglary | larceny | auto_theft | arson

    # Non-crime keywords
    noncrime_kw = [
        "INVESTIGATE", "SICK", "MEDICAL", "ACCIDENT", "TOWED", "MISSING PERSON",
        "LOST", "FOUND", "FIRE REPORT", "REPORT AFFECTING", "SERVICE TO",
        "SAFEKEEPING", "PROTECTIVE CUSTODY", "SUDDEN DEATH", "DEATH INVESTIGATION",
        "LOCKOUT", "ESCORT", "PARKING", "RECOVERED", "BALLISTICS", "ANIMAL",
        "SUICIDE", "DANGEROUS OR HAZARDOUS", "SEARCH WARRANT", "LANDLORD",
        "HARBOR", "AIRCRAFT", "NOISY PARTY", "VERBAL DISPUTE", "TRUANCY",
        "RUNAWAY", "GATHERING", "ACCIDENTAL DAMAGE", "EXPLOSIVES - TURNED",
    ]

    c = str(code).strip()
    if c in serious:
        return "serious"
    name = name_by_code.get(c, "")
    if any(kw in name for kw in noncrime_kw):
        return "noncrime"
    return "petty"


def classify_cambridge(desc: str) -> str:
    """Classify a Cambridge crime description as serious/petty/noncrime."""
    # Serious keywords
    serious_kw = [
        "AGGRAVATED ASSAULT", "ROBBERY", "HOUSEBREAK", "BREAK", "AUTO THEFT",
        "LARCENY", "SHOPLIFTING", "ARSON", "MURDER", "MANSLAUGHTER", "RAPE",
        "HOMICIDE",
    ]
    # Non-crime keywords
    noncrime_kw = [
        "HIT AND RUN", "ACCIDENT", "ADMIN ERROR", "MISSING PERSON", "SUSPICIOUS",
        "PHONE CALLS", "TAXI", "MEDICAL", "NOISE", "CIVIL DISPUTE", "WARRANT ARREST",
    ]

    cu = str(desc).upper()
    if any(k in cu for k in serious_kw):
        return "serious"
    if any(k in cu for k in noncrime_kw):
        return "noncrime"
    return "petty"


def _load_tracts() -> gpd.GeoDataFrame:
    """Load tract geometries for spatial joins."""
    from . import geography
    gdf = geography.run()
    return gdf[["GEOID", "town", "geometry"]].to_crs("EPSG:4326")


def aggregate_counts(incidents: pd.DataFrame, tracts_gdf: gpd.GeoDataFrame, town: str) -> pd.DataFrame:
    """Aggregate crime incidents to tracts for a given town.
    
    Returns one row per tract in the given town, with serious_count and petty_count
    columns (default 0). Tracts with no incidents still get a row.
    
    Parameters
    ----------
    incidents : pd.DataFrame
        DataFrame with 'lat', 'lon', and 'crime_type' columns.
    tracts_gdf : gpd.GeoDataFrame
        GeoDataFrame with 'GEOID', 'town', and 'geometry' columns.
    town : str
        Town name to filter tracts (e.g., "Boston", "Cambridge").
    
    Returns
    -------
    pd.DataFrame
        DataFrame with 'GEOID', 'serious_count', 'petty_count', 'town' columns.
    """
    if incidents.empty:
        # Return all tracts with zero counts
        town_tracts = tracts_gdf[tracts_gdf["town"] == town].copy()
        if town_tracts.empty:
            return pd.DataFrame()
        result = pd.DataFrame({"GEOID": town_tracts["GEOID"]})
        result["serious_count"] = 0
        result["petty_count"] = 0
        result["town"] = town
        return result
    
    # Filter to the target town
    town_tracts = tracts_gdf[tracts_gdf["town"] == town].copy()
    if town_tracts.empty:
        return pd.DataFrame()
    
    # Spatial join
    incidents["geometry"] = [Point(x, y) for x, y in zip(incidents["lon"], incidents["lat"])]
    incidents_gdf = gpd.GeoDataFrame(incidents, geometry="geometry", crs="EPSG:4326")
    joined = gpd.sjoin(incidents_gdf, town_tracts[["GEOID", "geometry"]], how="inner", predicate="within")
    
    # Aggregate
    ser = joined[joined["crime_type"] == "serious"].groupby("GEOID").size() if len(joined) > 0 else pd.Series(dtype=int)
    pet = joined[joined["crime_type"] == "petty"].groupby("GEOID").size() if len(joined) > 0 else pd.Series(dtype=int)
    
    result = pd.DataFrame({"GEOID": town_tracts["GEOID"]})
    result["serious_count"] = result["GEOID"].map(ser).fillna(0).astype(int)
    result["petty_count"] = result["GEOID"].map(lambda g: pet.get(g, 0)).fillna(0).astype(int)
    result["town"] = town
    return result


def _process_boston(tracts_gdf: gpd.GeoDataFrame) -> pd.DataFrame:
    """Process Boston PD incidents for the configured year."""
    if not BPD_INCIDENTS_CSV.exists():
        log.warning("BPD incidents file not found: %s", BPD_INCIDENTS_CSV)
        return pd.DataFrame()

    log.info("Crime: loading BPD incidents from %s", BPD_INCIDENTS_CSV)
    df = pd.read_csv(BPD_INCIDENTS_CSV, dtype=str, low_memory=False)

    # Clean coordinates
    df["Lat"] = pd.to_numeric(df["Lat"], errors="coerce")
    df["Long"] = pd.to_numeric(df["Long"], errors="coerce")
    before = len(df)
    df = df[df["Lat"].notna() & df["Long"].notna()]
    df = df[(df["Lat"] != 0) & (df["Long"] != 0) & (df["Lat"] != -1) & (df["Long"] != -1)]
    log.info("Crime: BPD dropped %d bad coords, remaining %d", before - len(df), len(df))

    # Dedup
    before = len(df)
    df = df.drop_duplicates(subset="INCIDENT_NUMBER")
    log.info("Crime: BPD dedup removed %d", before - len(df))

    # Load offense code names
    if BPD_OFFENSE_CODES_XLSX.exists():
        off = pd.read_excel(BPD_OFFENSE_CODES_XLSX)
        off["CODE"] = off["CODE"].astype(str).str.strip()
        name_by_code = {r["CODE"]: str(r["NAME"]) for _, r in off.iterrows()}
    else:
        name_by_code = {}
        log.warning("BPD offense codes file not found")

    # Year filter
    # OCCURRED_ON_DATE format: "2023-01-27 22:44:00+00"
    df["OCCURRED_ON_DATE"] = pd.to_datetime(df["OCCURRED_ON_DATE"], errors="coerce")
    df["incident_year"] = df["OCCURRED_ON_DATE"].dt.year
    df = df[df["incident_year"] == CRIME_YEAR]
    log.info("Crime: BPD filtered to year %d: %d incidents", CRIME_YEAR, len(df))

    # Classify
    df["crime_type"] = df["OFFENSE_CODE"].apply(lambda c: classify_bpd(c, name_by_code))
    log.info("Crime: BPD classification: %s", df["crime_type"].value_counts().to_dict())

    # Filter non-crime
    crime = df[df["crime_type"] != "noncrime"].copy()
    log.info("Crime: BPD crime rows after filter: %d", len(crime))

    # Get all Boston tracts (not just those with incidents)
    boston_tracts = tracts_gdf[tracts_gdf["town"] == "Boston"].copy()
    if boston_tracts.empty:
        log.warning("No Boston tracts found")
        return pd.DataFrame()

    # Spatial join crime incidents to Boston tracts
    crime["geometry"] = [Point(x, y) for x, y in zip(crime["Long"], crime["Lat"])]
    crime_gdf = gpd.GeoDataFrame(crime, geometry="geometry", crs="EPSG:4326")
    joined = gpd.sjoin(crime_gdf, boston_tracts[["GEOID", "geometry"]], how="inner", predicate="within")
    log.info("Crime: BPD joined to %d / %d Boston tracts", len(joined), len(crime))

    # Aggregate: ONE ROW PER BOSTON TRACT with counts (default 0)
    ser = joined[joined["crime_type"] == "serious"].groupby("GEOID").size()
    pet = joined[joined["crime_type"] == "petty"].groupby("GEOID").size()

    result = pd.DataFrame({"GEOID": boston_tracts["GEOID"]})
    result["serious_count"] = result["GEOID"].map(ser).fillna(0).astype(int)
    result["petty_count"] = result["GEOID"].map(lambda g: pet.get(g, 0)).fillna(0).astype(int)
    result["town"] = "Boston"
    log.info("Crime: BPD %d total Boston tracts", len(result))
    return result


def _process_cambridge(tracts_gdf: gpd.GeoDataFrame) -> pd.DataFrame:
    """Process Cambridge incidents for the configured year."""
    if not CAMBRIDGE_INCIDENTS_CSV.exists():
        log.warning("Cambridge incidents file not found: %s", CAMBRIDGE_INCIDENTS_CSV)
        return pd.DataFrame()

    log.info("Crime: loading Cambridge incidents from %s", CAMBRIDGE_INCIDENTS_CSV)
    df = pd.read_csv(CAMBRIDGE_INCIDENTS_CSV, dtype=str, low_memory=False)

    # Clean coordinates
    df["reporting_area_lat"] = pd.to_numeric(df["reporting_area_lat"], errors="coerce")
    df["reporting_area_lon"] = pd.to_numeric(df["reporting_area_lon"], errors="coerce")
    df = df[df["reporting_area_lat"].notna() & df["reporting_area_lon"].notna()]
    log.info("Crime: Cambridge after coord clean: %d", len(df))

    # Year filter - use date_of_report since crime_date_time has range format
    df["date_of_report"] = pd.to_datetime(df["date_of_report"], errors="coerce")
    df["incident_year"] = df["date_of_report"].dt.year
    df = df[df["incident_year"] == CRIME_YEAR]
    log.info("Crime: Cambridge filtered to year %d: %d incidents", CRIME_YEAR, len(df))

    # Classify
    df["crime_type"] = df["crime"].apply(classify_cambridge)
    log.info("Crime: Cambridge classification: %s", df["crime_type"].value_counts().to_dict())

    # Filter non-crime
    crime = df[df["crime_type"] != "noncrime"].copy()
    log.info("Crime: Cambridge crime rows after filter: %d", len(crime))

    # Get all Cambridge tracts (not just those with incidents)
    cambridge_tracts = tracts_gdf[tracts_gdf["town"] == "Cambridge"].copy()
    if cambridge_tracts.empty:
        log.warning("No Cambridge tracts found")
        return pd.DataFrame()

    # Spatial join crime incidents to Cambridge tracts
    crime["geometry"] = [
        Point(x, y)
        for x, y in zip(crime["reporting_area_lon"], crime["reporting_area_lat"])
    ]
    crime_gdf = gpd.GeoDataFrame(crime, geometry="geometry", crs="EPSG:4326")
    joined = gpd.sjoin(
        crime_gdf, cambridge_tracts[["GEOID", "geometry"]], how="inner", predicate="within"
    )
    log.info("Crime: Cambridge joined to %d / %d tracts", len(joined), len(crime))

    # Aggregate: ONE ROW PER CAMBRIDGE TRACT with counts (default 0)
    ser = joined[joined["crime_type"] == "serious"].groupby("GEOID").size()
    pet = joined[joined["crime_type"] == "petty"].groupby("GEOID").size()

    result = pd.DataFrame({"GEOID": cambridge_tracts["GEOID"]})
    result["serious_count"] = result["GEOID"].map(ser).fillna(0).astype(int)
    result["petty_count"] = result["GEOID"].map(lambda g: pet.get(g, 0)).fillna(0).astype(int)
    result["town"] = "Cambridge"
    log.info("Crime: Cambridge %d total tracts", len(result))
    return result


def _load_population(geoids: set[str]) -> dict[str, int]:
    """Load population from census.parquet."""
    census_path = INTERIM_DIR / "census.parquet"
    if not census_path.exists():
        log.warning("census.parquet not found, population lookup will fail")
        return {}
    df = pd.read_parquet(census_path)
    df["GEOID"] = df["GEOID"].astype(str).str.zfill(11)
    pop = {}
    for _, row in df.iterrows():
        g = row["GEOID"]
        if g in geoids:
            p = row.get("pop")
            if p is not None and not pd.isna(p):
                pop[g] = int(p)
    log.info("Crime: loaded population for %d tracts", len(pop))
    return pop


def run(geoids: set[str], force: bool = False) -> pd.DataFrame:
    """Return DataFrame with GEOID, crime_serious, crime_property, crime_year."""
    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    out_path = INTERIM_DIR / "crime.parquet"

    if out_path.exists() and not force:
        log.info("Crime: loading cached %s", out_path)
        df = pd.read_parquet(out_path)
        df["GEOID"] = df["GEOID"].astype(str).str.zfill(11)
        return df

    # Load tract geometries
    tracts_gdf = _load_tracts()

    # Process Boston and Cambridge
    boston_df = _process_boston(tracts_gdf)
    cambridge_df = _process_cambridge(tracts_gdf)

    # Combine
    combined = pd.concat([boston_df, cambridge_df], ignore_index=True)

    if combined.empty:
        log.warning("Crime: no incident data processed — returning nulls")
        df = pd.DataFrame({
            "GEOID": list(geoids),
            "crime_serious": None,
            "crime_property": None,
            "crime_year": None,
        })
        df.to_parquet(out_path)
        return df

    # Load population
    pop = _load_population(geoids)

    # Compute rates with low-population handling
    result_rows = []
    tracts_with_rates_boston = 0
    tracts_with_rates_cambridge = 0
    tracts_nulled_low_pop = 0

    for _, row in combined.iterrows():
        geoid = row["GEOID"]
        if geoid not in geoids:
            continue

        town = row.get("town", "Unknown")
        pop_val = pop.get(geoid)

        # Low population handling
        if pop_val is None or pop_val < CRIME_MIN_POP:
            result_rows.append({
                "GEOID": geoid,
                "crime_serious": None,
                "crime_property": None,
                "crime_year": None,
            })
            tracts_nulled_low_pop += 1
            continue

        serious_count = int(row.get("serious_count", 0))
        petty_count = int(row.get("petty_count", 0))

        crime_serious = town_rate(serious_count, pop_val)
        crime_property = town_rate(petty_count, pop_val)

        # Round to 1 decimal
        if crime_serious is not None:
            crime_serious = round(crime_serious, 1)
        if crime_property is not None:
            crime_property = round(crime_property, 1)

        result_rows.append({
            "GEOID": geoid,
            "crime_serious": crime_serious,
            "crime_property": crime_property,
            "crime_year": CRIME_YEAR,
        })

        if town == "Boston":
            tracts_with_rates_boston += 1
        elif town == "Cambridge":
            tracts_with_rates_cambridge += 1

    df = pd.DataFrame(result_rows)

    # Log counts
    log.info("Crime: tracts with rates: Boston=%d, Cambridge=%d",
             tracts_with_rates_boston, tracts_with_rates_cambridge)
    log.info("Crime: tracts nulled for low pop (<%d): %d", CRIME_MIN_POP, tracts_nulled_low_pop)

    # Add null rows for tracts with no data
    have = set(df["GEOID"])
    missing_rows = [
        {"GEOID": g, "crime_serious": None, "crime_property": None, "crime_year": None}
        for g in geoids if g not in have
    ]
    if missing_rows:
        df = pd.concat([df, pd.DataFrame(missing_rows)], ignore_index=True)
        log.info("Crime: %d tracts with no crime data (null)", len(missing_rows))

    df.to_parquet(out_path)
    log.info("Crime: wrote %s (%d rows)", out_path, len(df))
    return df
