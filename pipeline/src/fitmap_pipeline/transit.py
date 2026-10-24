"""Transit commute stage: tract-to-tract transit times via MBTA GTFS.

Connection Scan Algorithm (CSA) over next-Wednesday weekday service.
Departures at 07:30, 08:00, 08:30.  Result = median of the three.
Access/egress = OSRM foot from tract internal point to stops within 1.2 km.

Writes: pipeline/data/interim/transit_matrix.npy  (NxN seconds)
"""
from __future__ import annotations

import io
import logging
import zipfile
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd

from .config import GTFS_ZIP, INTERIM_DIR
from .utils import haversine_km

log = logging.getLogger(__name__)

EGRESS_RADIUS_KM = 1.2
DEPARTURE_MINUTES = [7 * 60 + 30, 8 * 60 + 0, 8 * 60 + 30]  # 7:30, 8:00, 8:30
MAX_SECONDS = 254 * 60   # hard cap → 255 byte sentinel


def _next_wednesday() -> date:
    """Return the next Wednesday from today."""
    today = date.today()
    days_ahead = (2 - today.weekday()) % 7  # Wednesday = weekday 2
    if days_ahead == 0:
        days_ahead = 7
    return today + timedelta(days=days_ahead)


def _parse_gtfs_time(t: str) -> int:
    """Parse GTFS time string (e.g. '25:10:00') to seconds since midnight.

    GTFS allows times > 24:00 for service past midnight.
    """
    parts = t.strip().split(":")
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])


def load_gtfs() -> dict[str, pd.DataFrame]:
    """Load required GTFS tables from zip."""
    tables = ["stops", "stop_times", "trips", "calendar", "calendar_dates",
              "routes", "transfers"]
    result = {}
    with zipfile.ZipFile(GTFS_ZIP) as z:
        for name in tables:
            fname = f"{name}.txt"
            if fname in z.namelist():
                result[name] = pd.read_csv(io.BytesIO(z.read(fname)), dtype=str)
            else:
                result[name] = pd.DataFrame()
    return result


def get_service_ids_for_date(calendar: pd.DataFrame,
                              cal_dates: pd.DataFrame,
                              service_date: date) -> set[str]:
    """Get set of service_ids running on service_date."""
    day_name = service_date.strftime("%A").lower()  # e.g. "wednesday"
    date_str = service_date.strftime("%Y%m%d")

    # Regular service
    if not calendar.empty and "start_date" in calendar.columns:
        cal = calendar.copy()
        cal["start_date"] = cal["start_date"].astype(str)
        cal["end_date"]   = cal["end_date"].astype(str)
        mask = (
            (cal["start_date"] <= date_str) &
            (cal["end_date"]   >= date_str) &
            (cal[day_name] == "1")
        )
        active = set(cal[mask]["service_id"])
    else:
        active = set()

    # Calendar date exceptions
    if not cal_dates.empty:
        exc = cal_dates[cal_dates["date"] == date_str].copy()
        exc["exception_type"] = pd.to_numeric(exc["exception_type"], errors="coerce")
        active.update(exc[exc["exception_type"] == 1]["service_id"])
        active -= set(exc[exc["exception_type"] == 2]["service_id"])

    return active


def build_transit_matrix(
    tracts_df: pd.DataFrame,
    force: bool = False,
) -> np.ndarray:
    """Build NxN transit-time matrix (seconds) using Connection Scan Algorithm.

    Returns float64 ndarray (N, N).  np.nan = no transit route.
    Capped at MAX_SECONDS.
    """
    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = INTERIM_DIR / "transit_matrix.npy"

    if cache_path.exists() and not force:
        log.info("Transit matrix: loading cached %s", cache_path)
        return np.load(cache_path)

    n_tracts = len(tracts_df)
    log.info("Transit: building %d×%d matrix (CSA + OSRM access/egress)…", n_tracts, n_tracts)

    # ── Load GTFS ─────────────────────────────────────────────────────────────
    gtfs = load_gtfs()
    stops      = gtfs["stops"]
    stop_times = gtfs["stop_times"]
    trips      = gtfs["trips"]
    calendar   = gtfs["calendar"]
    cal_dates  = gtfs["calendar_dates"]
    routes     = gtfs["routes"]
    transfers  = gtfs["transfers"]

    stops["stop_lat"] = pd.to_numeric(stops["stop_lat"], errors="coerce")
    stops["stop_lon"] = pd.to_numeric(stops["stop_lon"], errors="coerce")
    stops = stops.dropna(subset=["stop_lat", "stop_lon"])
    stop_id_to_idx = {sid: i for i, sid in enumerate(stops["stop_id"])}

    # ── Select service date (next Wednesday) ──────────────────────────────────
    service_date = _next_wednesday()
    service_ids  = get_service_ids_for_date(calendar, cal_dates, service_date)
    log.info("Transit: service date %s, %d service IDs", service_date, len(service_ids))

    if not service_ids:
        log.warning("Transit: no service found for %s — using all calendar services", service_date)
        if not calendar.empty:
            service_ids = set(calendar["service_id"])
        else:
            service_ids = set(trips["service_id"])

    # ── Filter trips and stop_times ───────────────────────────────────────────
    active_trips = trips[trips["service_id"].isin(service_ids)]["trip_id"]
    st = stop_times[stop_times["trip_id"].isin(active_trips)].copy()
    st = st[st["stop_id"].isin(stop_id_to_idx)]
    st["arrival_sec"]   = st["arrival_time"].apply(_parse_gtfs_time)
    st["departure_sec"] = st["departure_time"].apply(_parse_gtfs_time)
    st["stop_seq"]      = pd.to_numeric(st["stop_sequence"], errors="coerce")
    st["stop_idx"]      = st["stop_id"].map(stop_id_to_idx)
    st = st.dropna(subset=["stop_idx"]).sort_values(["trip_id", "stop_seq"])

    # Build connections: list of (dep_time, arr_time, from_stop_idx, to_stop_idx, trip_id)
    log.info("Transit: building connections from %d stop_time rows…", len(st))
    st_list = st[["trip_id", "stop_idx", "departure_sec", "arrival_sec", "stop_seq"]].values

    # CSA connections: consecutive stop pairs within same trip
    connections = []
    prev_row = None
    for row in st_list:
        trip, sidx, dep, arr, seq = row[0], int(row[1]), float(row[2]), float(row[3]), float(row[4])
        if prev_row is not None and prev_row[0] == trip:
            # previous stop → current stop
            connections.append((prev_row[2], arr, prev_row[1], sidx, trip))
        prev_row = row

    connections.sort(key=lambda c: c[0])  # sort by departure time
    log.info("Transit: %d connections", len(connections))

    # ── Transfers ─────────────────────────────────────────────────────────────
    # transfer_time in seconds from transfers.txt
    transfer_map: dict[tuple[int, int], float] = {}
    if not transfers.empty and "from_stop_id" in transfers.columns:
        transfers_f = transfers[
            transfers["from_stop_id"].isin(stop_id_to_idx) &
            transfers["to_stop_id"].isin(stop_id_to_idx)
        ].copy()
        transfers_f["min_transfer_time"] = pd.to_numeric(
            transfers_f.get("min_transfer_time", 0), errors="coerce"
        ).fillna(180)
        for _, r in transfers_f.iterrows():
            fi = stop_id_to_idx.get(r["from_stop_id"])
            ti = stop_id_to_idx.get(r["to_stop_id"])
            if fi is not None and ti is not None and fi != ti:
                transfer_map[(fi, ti)] = float(r["min_transfer_time"])

    # ── Tract-to-stop access/egress walk times ───────────────────────────────
    log.info("Transit: computing tract↔stop walk times (straight-line / 5 km/h)…")
    n_stops = len(stops)
    s_lats = stops["stop_lat"].values
    s_lons = stops["stop_lon"].values
    t_lats = tracts_df["INTPTLAT"].astype(float).values
    t_lons = tracts_df["INTPTLON"].astype(float).values

    # For each tract, find stops within EGRESS_RADIUS_KM
    # access_stops[tract_idx] = [(stop_idx, walk_sec), ...]
    access_stops: list[list[tuple[int, float]]] = [[] for _ in range(n_tracts)]
    egress_stops: list[list[tuple[int, float]]] = [[] for _ in range(n_tracts)]

    for ti in range(n_tracts):
        for si in range(n_stops):
            d_km = haversine_km(t_lats[ti], t_lons[ti], float(s_lats[si]), float(s_lons[si]))
            if d_km <= EGRESS_RADIUS_KM:
                walk_sec = d_km / 5.0 * 3600  # 5 km/h walking
                access_stops[ti].append((si, walk_sec))
                egress_stops[ti].append((si, walk_sec))

    # ── CSA for each origin tract ─────────────────────────────────────────────
    INF = float("inf")
    result = np.full((n_tracts, n_tracts), np.nan)

    conn_arr = connections  # list of (dep, arr, from_stop, to_stop, trip)

    # Pre-build walk-only matrix between tracts for fallback
    # (already computed by OSRM walk stage, but we don't load it here;
    #  instead we'll let the export stage take min(transit, walk))

    for orig_ti in range(n_tracts):
        if orig_ti % 50 == 0:
            log.info("  CSA origin %d/%d", orig_ti, n_tracts)

        # For each departure window, run CSA and find earliest arrival at each dest
        median_times = []

        for dep_win in DEPARTURE_MINUTES:
            dep_win_sec = dep_win * 60

            # Earliest arrival time at each stop, indexed by stop_idx
            T = [INF] * n_stops

            # Initialize: board at access stops reachable from origin at departure
            for si, walk_sec in access_stops[orig_ti]:
                board_time = dep_win_sec + walk_sec
                if board_time < T[si]:
                    T[si] = board_time

            # Scan connections (already sorted by departure)
            for (dep, arr, from_si, to_si, trip_id) in conn_arr:
                if dep > dep_win_sec + MAX_SECONDS:
                    break  # beyond search horizon
                if T[from_si] <= dep:
                    if arr < T[to_si]:
                        T[to_si] = arr
                        # Propagate transfers from to_si
                        for (fi, ti2), t_sec in transfer_map.items():
                            if fi == to_si and T[fi] + t_sec < T[ti2]:
                                T[ti2] = T[fi] + t_sec

            # Egress to each destination tract
            dest_times = np.full(n_tracts, INF)
            for dest_ti in range(n_tracts):
                best = INF
                for si, walk_sec in egress_stops[dest_ti]:
                    if T[si] < INF:
                        total = T[si] + walk_sec - dep_win_sec
                        if total < best:
                            best = total
                dest_times[dest_ti] = best

            median_times.append(dest_times)

        # Median over three departure windows
        stacked = np.stack(median_times, axis=0)
        medians = np.median(stacked, axis=0)

        # Cap and store
        for dest_ti in range(n_tracts):
            v = medians[dest_ti]
            if v < INF and v <= MAX_SECONDS:
                result[orig_ti, dest_ti] = v
            elif v < INF:
                result[orig_ti, dest_ti] = MAX_SECONDS + 1  # > cap → will become 255

        # Diagonal = 0
        result[orig_ti, orig_ti] = 0.0

    np.save(cache_path, result)
    log.info("Transit matrix: saved %s (%d×%d)", cache_path, n_tracts, n_tracts)
    return result


def run(tracts_df: pd.DataFrame, force: bool = False) -> np.ndarray:
    return build_transit_matrix(tracts_df, force=force)
