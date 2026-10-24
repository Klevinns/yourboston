"""Stations stage: extract MBTA rapid-transit + commuter-rail stations from GTFS.

route_type ∈ {0, 1, 2} = light-rail/streetcar, heavy rail, commuter rail.
(per design.md D7: bus route_type=3 / Silver Line excluded from accessibility criterion)

Writes:
  - pipeline/data/interim/stations.parquet — stop_id, stop_name, lat, lon
"""
from __future__ import annotations

import io
import logging
import zipfile
from pathlib import Path

import pandas as pd

from .config import GTFS_ZIP, INTERIM_DIR

log = logging.getLogger(__name__)

# GTFS route types for rapid-transit + commuter rail
RAPID_RAIL_TYPES = {0, 1, 2}


def load_stations(force: bool = False) -> pd.DataFrame:
    """Return DataFrame with stop_id, stop_name, lat, lon for rapid/CR stations."""
    out_path = INTERIM_DIR / "stations.parquet"

    if out_path.exists() and not force:
        log.info("Stations: loading cached %s", out_path)
        return pd.read_parquet(out_path)

    INTERIM_DIR.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(GTFS_ZIP) as z:
        stops    = pd.read_csv(io.BytesIO(z.read("stops.txt")),      dtype=str)
        routes   = pd.read_csv(io.BytesIO(z.read("routes.txt")),     dtype=str)
        trips    = pd.read_csv(io.BytesIO(z.read("trips.txt")),      dtype=str)
        stop_times = pd.read_csv(io.BytesIO(z.read("stop_times.txt")), dtype=str)

    # Rapid-transit + commuter-rail routes
    routes["route_type"] = pd.to_numeric(routes["route_type"], errors="coerce")
    rail_routes = routes[routes["route_type"].isin(RAPID_RAIL_TYPES)]["route_id"]

    # Trips on those routes
    rail_trips = trips[trips["route_id"].isin(rail_routes)]["trip_id"]

    # Stops served by those trips
    rail_stops = stop_times[stop_times["trip_id"].isin(rail_trips)]["stop_id"].unique()

    # Filter stops table
    stops_rail = stops[stops["stop_id"].isin(rail_stops)].copy()

    # Prefer parent stations (location_type == "1") where available
    # If a stop has a parent_station, use the parent's coords
    stops_rail["location_type"] = pd.to_numeric(stops_rail.get("location_type", 0), errors="coerce").fillna(0)
    stops_rail["stop_lat"] = pd.to_numeric(stops_rail["stop_lat"], errors="coerce")
    stops_rail["stop_lon"] = pd.to_numeric(stops_rail["stop_lon"], errors="coerce")
    stops_rail = stops_rail.dropna(subset=["stop_lat", "stop_lon"])

    # Deduplicate: for each stop_name keep one entry (lowest stop_id for consistency)
    stops_rail = stops_rail.sort_values("stop_id")

    # Build final table
    df = stops_rail[["stop_id", "stop_name", "stop_lat", "stop_lon"]].copy()
    df = df.rename(columns={"stop_lat": "lat", "stop_lon": "lon"})
    df = df.reset_index(drop=True)

    df.to_parquet(out_path)
    log.info("Stations: %d rapid/CR stops", len(df))
    return df


def run(force: bool = False) -> pd.DataFrame:
    return load_stations(force=force)
