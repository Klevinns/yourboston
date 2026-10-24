"""OSRM stage: walk/bike/car commute matrices + transit walk-to-station times.

Queries self-hosted OSRM table service on ports 5001 (foot), 5002 (bicycle),
5003 (car).  Chunks requests to stay under OSRM max-table-size.

Pure function encode_minutes / decode_minutes live in utils.py.

Writes:
  - pipeline/data/interim/osrm_walk.parquet   — NxN float seconds
  - pipeline/data/interim/osrm_bike.parquet
  - pipeline/data/interim/osrm_car.parquet
  - pipeline/data/interim/transit_access.parquet — per-tract: transit(s), transit_station
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import numpy as np
import pandas as pd
import requests

from .config import (
    INTERIM_DIR,
    OSRM_BIKE_PORT,
    OSRM_CAR_PORT,
    OSRM_FOOT_PORT,
)
from .utils import haversine_km

log = logging.getLogger(__name__)

CHUNK = 100          # sources per OSRM table request
MAX_SECONDS = 254 * 60  # 254 min cap → 255 byte sentinel

# Timeout for individual requests (generous for large table queries)
REQ_TIMEOUT = 120


def _osrm_table(
    port: int,
    src_coords: list[tuple[float, float]],
    dst_coords: list[tuple[float, float]],
) -> list[list[float | None]]:
    """Query OSRM table service.

    Returns durations[n_src][n_dst] in seconds (float or None for no route).
    Raises RuntimeError if the server is unreachable.
    """
    # Build coordinate string: lon,lat (OSRM order is lon,lat)
    all_coords = src_coords + dst_coords
    coord_str = ";".join(f"{lon},{lat}" for lat, lon in all_coords)
    sources_str = ";".join(str(i) for i in range(len(src_coords)))
    dests_str   = ";".join(str(i + len(src_coords)) for i in range(len(dst_coords)))

    url = (
        f"http://localhost:{port}/table/v1/driving/"
        f"{coord_str}?sources={sources_str}&destinations={dests_str}&annotations=duration"
    )

    try:
        resp = requests.get(url, timeout=REQ_TIMEOUT)
    except requests.exceptions.ConnectionError as exc:
        raise RuntimeError(
            f"OSRM port {port} is unreachable. Start it before running the pipeline. ({exc})"
        ) from exc

    if resp.status_code != 200:
        raise RuntimeError(f"OSRM returned HTTP {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    if data.get("code") != "Ok":
        raise RuntimeError(f"OSRM error: {data.get('code')} — {data.get('message')}")

    return data["durations"]  # list[list[float|None]]


def build_matrix(
    port: int,
    coords: list[tuple[float, float]],
    label: str,
    force: bool = False,
) -> np.ndarray:
    """Build full NxN duration matrix (seconds) using chunked table queries.

    Returns float64 ndarray, shape (N, N).  None/no-route → np.nan.
    Cached as parquet in interim dir.
    """
    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = INTERIM_DIR / f"osrm_{label}.npy"

    if cache_path.exists() and not force:
        log.info("OSRM %s: loading cached matrix %s", label, cache_path)
        return np.load(cache_path)

    n = len(coords)
    matrix = np.full((n, n), np.nan, dtype=np.float64)

    log.info("OSRM %s: building %dx%d matrix (port %d)…", label, n, n, port)

    for src_start in range(0, n, CHUNK):
        src_end   = min(src_start + CHUNK, n)
        src_batch = coords[src_start:src_end]

        for dst_start in range(0, n, CHUNK):
            dst_end   = min(dst_start + CHUNK, n)
            dst_batch = coords[dst_start:dst_end]

            rows = _osrm_table(port, src_batch, dst_batch)

            for si, row in enumerate(rows):
                for di, dur in enumerate(row):
                    if dur is not None and not (isinstance(dur, float) and dur != dur):
                        matrix[src_start + si, dst_start + di] = dur

        if src_start % (CHUNK * 5) == 0:
            log.info("  … %d/%d source rows done", src_start, n)

    np.save(cache_path, matrix)
    log.info("OSRM %s: matrix saved (%d×%d)", label, n, n)
    return matrix


def build_transit_access(
    tracts_df: pd.DataFrame,
    stations_df: pd.DataFrame,
    force: bool = False,
) -> pd.DataFrame:
    """Compute walk time from each tract internal point to nearest rapid/CR station.

    Returns DataFrame with GEOID, transit (seconds), transit_station.
    """
    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = INTERIM_DIR / "transit_access.parquet"

    if cache_path.exists() and not force:
        log.info("Transit access: loading cached %s", cache_path)
        df = pd.read_parquet(cache_path)
        df["GEOID"] = df["GEOID"].astype(str).str.zfill(11)
        return df

    # Build OSRM foot matrix: tract → nearest station
    tract_coords    = list(zip(tracts_df["INTPTLAT"].astype(float),
                               tracts_df["INTPTLON"].astype(float)))
    station_coords  = list(zip(stations_df["lat"].astype(float),
                               stations_df["lon"].astype(float)))
    station_names   = stations_df["stop_name"].tolist()

    log.info("Transit access: %d tracts × %d stations via OSRM foot", len(tract_coords), len(station_coords))

    rows = []
    for i, (tlat, tlon) in enumerate(tract_coords):
        best_secs = None
        best_name = None

        # Find closest stations by straight-line first (candidate pruning)
        s_lats = stations_df["lat"].values
        s_lons = stations_df["lon"].values
        dists  = [haversine_km(tlat, tlon, float(s_lats[j]), float(s_lons[j]))
                  for j in range(len(s_lats))]
        cands  = sorted(range(len(dists)), key=lambda j: dists[j])[:20]

        # Query OSRM for these candidates
        try:
            cand_coords = [station_coords[j] for j in cands]
            durations   = _osrm_table(OSRM_FOOT_PORT, [(tlat, tlon)], cand_coords)
            row_durs    = durations[0] if durations else []
            for ci, dur in enumerate(row_durs):
                if dur is not None and (best_secs is None or dur < best_secs):
                    best_secs = dur
                    best_name = station_names[cands[ci]]
        except RuntimeError as exc:
            log.warning("OSRM foot unreachable for tract %d: %s", i, exc)
            # Fallback: straight-line distance → estimated walk time at 5 km/h
            j    = cands[0]
            d_km = dists[j]
            best_secs = d_km / 5.0 * 3600
            best_name = station_names[j]

        rows.append({
            "GEOID":           tracts_df.iloc[i]["GEOID"],
            "transit_secs":    round(best_secs) if best_secs is not None else None,
            "transit_station": best_name,
        })

    df = pd.DataFrame(rows)
    df.to_parquet(cache_path)
    log.info("Transit access: wrote %s", cache_path)
    return df


def run(
    tracts_df: pd.DataFrame,
    stations_df: pd.DataFrame,
    force: bool = False,
) -> dict:
    """Run OSRM stage; return dict with matrices and transit access df."""
    coords = list(zip(
        tracts_df["INTPTLAT"].astype(float),
        tracts_df["INTPTLON"].astype(float),
    ))

    walk_mat  = build_matrix(OSRM_FOOT_PORT, coords, "walk",  force=force)
    bike_mat  = build_matrix(OSRM_BIKE_PORT, coords, "bike",  force=force)
    car_mat   = build_matrix(OSRM_CAR_PORT,  coords, "car",   force=force)
    transit_acc = build_transit_access(tracts_df, stations_df, force=force)

    return {
        "walk":  walk_mat,
        "bike":  bike_mat,
        "car":   car_mat,
        "transit_access": transit_acc,
    }
