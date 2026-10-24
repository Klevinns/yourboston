"""YourBoston pipeline CLI.

Usage:
    uv run python -m fitmap_pipeline <stage>

Stages:
    geography  — TIGER tracts + 10-mile filter + town join
    census     — ACS scored axes + sentinel nulling
    crime      — BPD/Cambridge PD (tract-level) + FBI NIBRS (town-level) rates
    campus     — nearest campus km
    stations   — MBTA rapid/CR stations
    osrm       — OSRM walk/bike/car matrices + transit access
    transit    — GTFS connection-scan transit matrix
    prose      — ctx_lang / ctx_race / ctx_ej (never scored)
    export     — merge everything → web/data/fitmap.geojson
    build      — run all stages in order (skips cached unless --force)
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("fitmap_pipeline")


def _check_env() -> None:
    """Fail fast if CENSUS_API_KEY is unset (not needed for staged data, but checked)."""
    import os
    key = os.environ.get("CENSUS_API_KEY", "")
    if not key:
        log.warning(
            "CENSUS_API_KEY not set; any live Census API fetches will fail. "
            "Staged CSVs will be used instead."
        )


def cmd_geography(force: bool) -> None:
    from . import geography
    gdf = geography.run(force=force)
    log.info("Geography done: %d tracts", len(gdf))


def cmd_census(force: bool) -> None:
    from . import geography, census
    gdf = geography.run()
    geoids = set(gdf["GEOID"].astype(str).str.zfill(11))
    df = census.run(geoids, force=force)
    log.info("Census done: %d rows", len(df))


def cmd_crime(force: bool) -> None:
    from . import geography, crime
    gdf = geography.run()
    geoids = set(gdf["GEOID"].astype(str).str.zfill(11))
    df = crime.run(geoids, force=force)
    log.info("Crime done: %d rows", len(df))


def cmd_campus(force: bool) -> None:
    from . import geography, campus
    gdf = geography.run()
    df = campus.run(gdf[["GEOID", "INTPTLAT", "INTPTLON"]], force=force)
    log.info("Campus done: %d rows", len(df))


def cmd_stations(force: bool) -> None:
    from . import stations
    df = stations.run(force=force)
    log.info("Stations done: %d stations", len(df))


def cmd_osrm(force: bool) -> None:
    from . import geography, stations, osrm
    gdf      = geography.run()
    stats_df = stations.run()
    result   = osrm.run(gdf[["GEOID", "INTPTLAT", "INTPTLON"]], stats_df, force=force)
    log.info("OSRM done: walk/bike/car matrices + transit access")


def cmd_transit(force: bool) -> None:
    from . import geography, transit
    gdf = geography.run()
    mat = transit.run(gdf[["GEOID", "INTPTLAT", "INTPTLON"]], force=force)
    log.info("Transit done: %dx%d matrix", mat.shape[0], mat.shape[1])


def cmd_prose(force: bool) -> None:
    from . import geography, prose
    gdf    = geography.run()
    geoids = set(gdf["GEOID"].astype(str).str.zfill(11))
    town_map = dict(zip(gdf["GEOID"].astype(str).str.zfill(11), gdf["town"]))
    df     = prose.run(geoids, town_map=town_map, force=force)
    log.info("Prose done: %d rows", len(df))


def cmd_services(force: bool) -> None:
    from . import geography, services
    gdf = geography.run()
    geoids = set(gdf["GEOID"].astype(str).str.zfill(11))
    df = services.run(geoids, force=force)
    log.info("Services done: %d rows", len(df))


def cmd_export(force: bool) -> None:
    import numpy as np
    from . import geography, census, crime, campus, stations, osrm, transit, prose, services, export
    from .config import INTERIM_DIR

    gdf     = geography.run()
    geoids  = set(gdf["GEOID"].astype(str).str.zfill(11))
    tracts  = gdf.sort_values("GEOID").reset_index(drop=True)

    cen_df  = census.run(geoids)
    cri_df  = crime.run(geoids)
    cap_df  = campus.run(tracts[["GEOID", "INTPTLAT", "INTPTLON"]])
    sta_df  = stations.run()
    osrm_d  = osrm.run(tracts[["GEOID", "INTPTLAT", "INTPTLON"]], sta_df)
    town_map = dict(zip(tracts["GEOID"].astype(str).str.zfill(11), tracts["town"]))
    prse_df = prose.run(geoids, town_map=town_map)
    svc_df  = services.run(geoids)

    trans_acc = osrm_d["transit_access"]

    # Load or compute transit matrix
    # export --force should NOT recompute OSRM or transit matrix; it only rebuilds export output
    tmat_path = INTERIM_DIR / "transit_matrix.npy"
    if tmat_path.exists():
        log.info("Export: loading cached transit matrix")
        tmat = np.load(tmat_path)
    else:
        tmat = transit.run(tracts[["GEOID", "INTPTLAT", "INTPTLON"]], force=False)

    # Transit = min(transit, walk) per design.md (walk-only always considered)
    import numpy as _np
    walk_mat = osrm_d["walk"]
    tmat_eff = _np.where(
        _np.isnan(tmat), walk_mat,
        _np.where(_np.isnan(walk_mat), tmat, _np.minimum(tmat, walk_mat)),
    )

    out = export.run(
        tracts, cen_df, cri_df, cap_df, trans_acc, prse_df, svc_df,
        osrm_d["walk"], osrm_d["bike"], osrm_d["car"], tmat_eff,
        force=force,
    )
    log.info("Export done: %s", out)


def cmd_build(force: bool) -> None:
    """Run all stages in order."""
    log.info("=== BUILD: starting all stages ===")
    cmd_geography(force)
    cmd_census(force)
    cmd_crime(force)
    cmd_campus(force)
    cmd_stations(force)
    cmd_osrm(force)
    cmd_transit(force)
    cmd_prose(force)
    cmd_services(force)
    cmd_export(force)
    log.info("=== BUILD: complete ===")


COMMANDS = {
    "geography": cmd_geography,
    "census":    cmd_census,
    "crime":     cmd_crime,
    "campus":    cmd_campus,
    "stations":  cmd_stations,
    "osrm":      cmd_osrm,
    "transit":   cmd_transit,
    "prose":     cmd_prose,
    "services":  cmd_services,
    "export":    cmd_export,
    "build":     cmd_build,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="YourBoston pipeline")
    parser.add_argument(
        "stage",
        choices=list(COMMANDS),
        help="Pipeline stage to run",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Recompute even if cached interim files exist",
    )
    args = parser.parse_args()
    _check_env()
    COMMANDS[args.stage](args.force)
    return 0


if __name__ == "__main__":
    sys.exit(main())
