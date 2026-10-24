"""Services stage: public-services access index from OpenStreetMap (Overpass API).

For each tract, compute the distance from the tract centroid to the nearest
feature of five types (library, fire station, police, supermarket, park),
normalize each type's distances across tracts to [0,1] (0 = closest), and
average them into a `services` index (lower = better access).

Parks are filtered to a minimum area so tiny pocket parks don't make every
downtown tract look like "great park access".

Also records, per tract, the NAME and straight-line distance (meters) of the
nearest feature in each category, plus schools (nearby context only; schools
are NOT part of the services index and carry no rating/quality label).

Writes: pipeline/data/interim/services.parquet
"""
from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request

import geopandas as gpd
import pandas as pd
from pyproj import Transformer
from shapely.geometry import Polygon
from shapely.ops import transform

from . import geography
from .config import INTERIM_DIR, SERVICES_BBOX, SERVICES_PARK_MIN_AREA_M2

log = logging.getLogger(__name__)

CATEGORIES = ["library", "fire_station", "police", "supermarket", "park"]
# Categories reported in the "Nearby" panel (name + distance only).
NEARBY_CATEGORIES = CATEGORIES + ["school"]
# Beyond this the panel says "None within 10 mi" (no fabricated nearest).
NEARBY_MAX_M = 16_093.44  # 10 miles


def _overpass(query: str) -> list[dict]:
    data = urllib.parse.urlencode({"data": query}).encode()
    endpoints = [
        "https://overpass.openstreetmap.fr/api/interpreter",
        "https://overpass-api.de/api/interpreter",
        "https://overpass.osm.ch/api/interpreter",
    ]
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
        "User-Agent": "yourboston-hackathon/1.0",
    }
    last_err = None
    for url in endpoints:
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, data=data, headers=headers)
                with urllib.request.urlopen(req, timeout=180) as resp:
                    return json.loads(resp.read()).get("elements", [])
            except Exception as e:
                last_err = e
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Overpass query failed: {last_err}")


def _point(e: dict):
    """(lat, lon) for a node (lat/lon) or way/relation (center)."""
    if "lat" in e and "lon" in e:
        return (e["lat"], e["lon"])
    c = e.get("center")
    if c and "lat" in c and "lon" in c:
        return (c["lat"], c["lon"])
    return None


def _name(tags: dict) -> str | None:
    n = (tags.get("name") or tags.get("official_name") or "").strip()
    return n or None


def _fetch_points() -> dict[str, list[tuple[float, float, str | None]]]:
    """Fetch OSM features → {category: [(lat, lon, name|None), ...]}."""
    S, W, N, E = SERVICES_BBOX
    points: dict[str, list[tuple[float, float, str | None]]] = {}

    q = f"""[out:json][timeout:120];
(
  nwr["amenity"="library"]({S},{W},{N},{E});
  nwr["amenity"="fire_station"]({S},{W},{N},{E});
  nwr["amenity"="police"]({S},{W},{N},{E});
  nwr["shop"="supermarket"]({S},{W},{N},{E});
  nwr["leisure"="park"]({S},{W},{N},{E});
  nwr["amenity"="school"]({S},{W},{N},{E});
);
out center tags;
"""
    for e in _overpass(q):
        tags = e.get("tags", {})
        etype = e.get("type")
        if tags.get("amenity") == "library":
            cat = "library"
        elif tags.get("amenity") == "fire_station":
            cat = "fire_station"
        elif tags.get("amenity") == "police":
            cat = "police"
        elif tags.get("shop") == "supermarket":
            cat = "supermarket"
        elif tags.get("leisure") == "park":
            # drop point-parks (nodes); keep park areas (ways/relations)
            if etype == "node":
                continue
            cat = "park"
        elif tags.get("amenity") == "school":
            cat = "school"
        else:
            continue
        pt = _point(e)
        if pt:
            points.setdefault(cat, []).append((pt[0], pt[1], _name(tags)))

    for cat, lst in points.items():
        log.info("Services: %s -> %d features", cat, len(lst))
    return points


def run(geoids: set[str], force: bool = False) -> pd.DataFrame:
    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    out_path = INTERIM_DIR / "services.parquet"
    if out_path.exists() and not force:
        log.info("Services: loading cached %s", out_path)
        df = pd.read_parquet(out_path)
        df["GEOID"] = df["GEOID"].astype(str).str.zfill(11)
        if all(f"near_{c}_m" in df.columns for c in NEARBY_CATEGORIES):
            return df
        log.info("Services: cache lacks nearby columns; recomputing")

    tracts = geography.run()
    centroids = tracts[["GEOID", "INTPTLAT", "INTPTLON"]].copy()
    centroids["GEOID"] = centroids["GEOID"].astype(str).str.zfill(11)
    centroids = centroids[centroids["GEOID"].isin(geoids)].reset_index(drop=True)

    points = _fetch_points()

    tract_gdf = gpd.GeoDataFrame(
        centroids,
        geometry=gpd.points_from_xy(centroids["INTPTLON"], centroids["INTPTLAT"]),
        crs="EPSG:4326",
    ).to_crs("EPSG:32619")

    dists: dict[str, pd.Series] = {}
    near_name: dict[str, pd.Series] = {}
    near_m: dict[str, pd.Series] = {}
    for cat in NEARBY_CATEGORIES:
        pts = points.get(cat, [])
        if not pts:
            dists[cat] = pd.Series(float("nan"), index=centroids["GEOID"])
            near_name[cat] = pd.Series(None, index=centroids["GEOID"], dtype=object)
            near_m[cat] = pd.Series(float("nan"), index=centroids["GEOID"])
            continue
        svc = gpd.GeoDataFrame(
            {"svc_name": [p[2] for p in pts]},
            geometry=gpd.points_from_xy([p[1] for p in pts], [p[0] for p in pts]),
            crs="EPSG:4326",
        ).to_crs("EPSG:32619")
        nearest = gpd.sjoin_nearest(tract_gdf, svc, how="left", distance_col="dist")
        # sjoin_nearest can return several rows on exact ties: keep the closest,
        # preferring a named feature, deterministic by name.
        nearest = nearest.assign(_unnamed=nearest["svc_name"].isna())
        nearest = nearest.sort_values(["GEOID", "dist", "_unnamed", "svc_name"])
        first = nearest.drop_duplicates("GEOID").set_index("GEOID").reindex(centroids["GEOID"])
        dists[cat] = first["dist"].astype(float) / 1000.0  # m -> km
        m = first["dist"].astype(float)
        within = m <= NEARBY_MAX_M
        near_m[cat] = m.where(within)
        near_name[cat] = first["svc_name"].where(within)

    df = pd.DataFrame(index=centroids["GEOID"])
    for cat in CATEGORIES:
        df[f"dist_{cat}"] = dists[cat].astype(float)

    # Normalize each category's distances to [0,1] across tracts, then average
    norm = pd.DataFrame(index=df.index)
    for cat in CATEGORIES:
        v = df[f"dist_{cat}"]
        mn, mx = v.min(), v.max()
        norm[cat] = (v - mn) / (mx - mn) if mx > mn else 0.0

    result = pd.DataFrame({"GEOID": df.index, "services": norm.mean(axis=1).round(3)})
    result["GEOID"] = result["GEOID"].astype(str).str.zfill(11)

    # Any tract with no feature in a category would produce NaN → fill with worst (1.0)
    result["services"] = result["services"].fillna(1.0)

    # Nearest-feature name + distance (m) per category — panel context only
    for cat in NEARBY_CATEGORIES:
        result[f"near_{cat}_name"] = near_name[cat].values
        result[f"near_{cat}_m"] = near_m[cat].round(0).values

    result.to_parquet(out_path)
    log.info("Services: wrote %s (%d rows)", out_path, len(result))
    return result
