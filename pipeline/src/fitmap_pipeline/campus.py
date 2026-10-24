"""Campus stage: nearest MassGIS HigherEd campus per tract.

Reads MassGIS colleges CSV (staged at /root/hackachusetts-data/civic/colleges/).
Computes great-circle distance from each tract internal point to every campus.

Writes: pipeline/data/interim/campus.parquet
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

import pandas as pd

from .config import COLLEGES_CSV, INTERIM_DIR
from .utils import haversine_km

log = logging.getLogger(__name__)


def run(tracts_df: pd.DataFrame, force: bool = False) -> pd.DataFrame:
    """Return DataFrame with GEOID, campus (km), campus_name."""
    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    out_path = INTERIM_DIR / "campus.parquet"

    if out_path.exists() and not force:
        log.info("Campus: loading cached %s", out_path)
        df = pd.read_parquet(out_path)
        df["GEOID"] = df["GEOID"].astype(str).str.zfill(11)
        return df

    # ── Load colleges ─────────────────────────────────────────────────────────
    if not COLLEGES_CSV.exists():
        log.error("Colleges CSV not found at %s", COLLEGES_CSV)
        return pd.DataFrame({
            "GEOID": tracts_df["GEOID"].tolist(),
            "campus": None,
            "campus_name": None,
        })

    # Copy to pipeline raw for reproducibility
    raw_copy = INTERIM_DIR.parent / "raw" / "colleges.csv"
    raw_copy.parent.mkdir(parents=True, exist_ok=True)
    if not raw_copy.exists():
        shutil.copy2(COLLEGES_CSV, raw_copy)

    colleges = pd.read_csv(COLLEGES_CSV)
    # Drop rows without valid lat/lon
    colleges = colleges.dropna(subset=["latitude", "longitude"])
    colleges["latitude"]  = pd.to_numeric(colleges["latitude"],  errors="coerce")
    colleges["longitude"] = pd.to_numeric(colleges["longitude"], errors="coerce")
    colleges = colleges.dropna(subset=["latitude", "longitude"])

    # Name column — try common headers
    name_col = next(
        (c for c in ["campus", "college", "name", "school"] if c in colleges.columns),
        colleges.columns[0],
    )
    college_col = "college" if "college" in colleges.columns else name_col

    log.info("Campus: %d colleges loaded", len(colleges))

    # Precompute arrays for speed
    c_lats  = colleges["latitude"].values
    c_lons  = colleges["longitude"].values
    c_names = colleges[college_col].astype(str).values

    rows = []
    tracts_df = tracts_df.copy()
    tracts_df["GEOID"] = tracts_df["GEOID"].astype(str).str.zfill(11)

    for _, tract in tracts_df.iterrows():
        tlat = float(tract.get("INTPTLAT", 0))
        tlon = float(tract.get("INTPTLON", 0))

        best_km   = None
        best_name = None
        for i in range(len(c_lats)):
            d = haversine_km(tlat, tlon, float(c_lats[i]), float(c_lons[i]))
            if best_km is None or d < best_km:
                best_km   = d
                best_name = c_names[i]

        rows.append({
            "GEOID":       tract["GEOID"],
            "campus":      round(best_km, 3) if best_km is not None else None,
            "campus_name": best_name,
        })

    df = pd.DataFrame(rows)
    df.to_parquet(out_path)
    log.info("Campus: wrote %s (%d rows)", out_path, len(df))
    return df
