"""Prose stage: ctx_lang, ctx_race, ctx_ej fields (NEVER scored).

Reads pre-staged CSVs (acs_prose_axes.csv, acs_race.csv).
EJ2020 data is fetched from MassGIS REST API (returnGeometry=false).

Writes: pipeline/data/interim/prose.parquet
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional
import json

import pandas as pd
import requests

from .config import ACS_PROSE_CSV, ACS_RACE_CSV, INTERIM_DIR

log = logging.getLogger(__name__)

# Staged EJ2020 data (municipality-keyed extract)
EJ2020_STAGED_CSV = Path("/root/hackachusetts-data/civic/ej/data.csv")

# EJ2020 MassGIS REST endpoint (fallback if network available)
EJ2020_URL = (
    "https://gis.massgis.state.ma.us/arcgis/rest/services/MGIS/"
    "EJ2020/MapServer/0/query"
)
EJ2020_PARAMS = {
    "where": "1=1",
    "outFields": "GEOID10,EJ,EJ_CRIT_DESC",
    "returnGeometry": "false",
    "f": "json",
    "resultOffset": 0,
    "resultRecordCount": 2000,
}


def _fetch_ej2020() -> pd.DataFrame:
    """Fetch EJ2020 block groups from MassGIS; return DataFrame with GEOID10, EJ, EJ_CRIT_DESC."""
    rows = []
    offset = 0
    while True:
        params = {**EJ2020_PARAMS, "resultOffset": offset}
        try:
            resp = requests.get(EJ2020_URL, params=params, timeout=30)
            data = resp.json()
        except Exception as exc:
            log.warning("EJ2020 fetch error at offset %d: %s", offset, exc)
            break

        features = data.get("features", [])
        if not features:
            break
        for f in features:
            attrs = f.get("attributes", {})
            rows.append({
                "bg_geoid": str(attrs.get("GEOID10", "")).zfill(12),
                "ej":       attrs.get("EJ"),
                "ej_crit":  attrs.get("EJ_CRIT_DESC"),
            })
        offset += len(features)
        if len(features) < 2000:
            break

    if not rows:
        log.warning("EJ2020: no data fetched")
        return pd.DataFrame(columns=["bg_geoid", "ej", "ej_crit"])

    df = pd.DataFrame(rows)
    log.info("EJ2020: fetched %d block groups", len(df))
    return df


def _load_staged_ej() -> dict[str, dict]:
    """Load municipality-keyed EJ2020 extract → {MUNICIPALITY_UPPER: {...}}."""
    if not EJ2020_STAGED_CSV.exists():
        return {}
    df = pd.read_csv(EJ2020_STAGED_CSV)
    ej_by_town: dict[str, dict] = {}
    for _, row in df.iterrows():
        muni = str(row.get("municipality", "")).strip().upper()
        if not muni:
            continue
        is_ej = str(row.get("ej", "")).strip().lower() in ("yes", "1", "true")
        crit  = str(row.get("criteria", "")).strip()
        rec = ej_by_town.setdefault(muni, {"bg_count": 0, "criteria": set(), "is_ej": False})
        rec["bg_count"] += 1
        if is_ej:
            rec["is_ej"] = True
        if crit:
            rec["criteria"].add(crit)
    log.info("EJ2020: loaded %d municipalities from staged extract", len(ej_by_town))
    return ej_by_town


def run(geoids: set[str], town_map: dict[str, str] | None = None,
        force: bool = False) -> pd.DataFrame:
    """Return DataFrame with GEOID, ctx_lang, ctx_race, ctx_ej (all json-serialisable dicts).

    town_map: optional {GEOID: town_name} to join municipality-keyed EJ data.
    """
    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    out_path = INTERIM_DIR / "prose.parquet"

    if out_path.exists() and not force:
        log.info("Prose: loading cached %s", out_path)
        df = pd.read_parquet(out_path)
        df["GEOID"] = df["GEOID"].astype(str).str.zfill(11)
        return df

    town_map = town_map or {}

    # ── Language (B16001 proxy from prose CSV) ────────────────────────────────
    prose_df = pd.read_csv(ACS_PROSE_CSV, dtype={"GEOID": str})
    prose_df["GEOID"] = prose_df["GEOID"].astype(str).str.strip().str.zfill(11)
    prose_df = prose_df[prose_df["GEOID"].isin(geoids)].copy()

    # ── Race (acs_race.csv) ───────────────────────────────────────────────────
    race_df = pd.read_csv(ACS_RACE_CSV, dtype={"GEOID": str})
    race_df["GEOID"] = race_df["GEOID"].astype(str).str.strip().str.zfill(11)
    race_df = race_df[race_df["GEOID"].isin(geoids)].copy()

    # ── EJ2020 (municipality-level staged extract, joined by town name) ───────
    ej_by_town = _load_staged_ej()
    # Build per-tract EJ via town_map
    ej_by_tract: dict[str, dict] = {}
    for g, town in town_map.items():
        key = str(town).strip().upper()
        if key in ej_by_town:
            rec = ej_by_town[key]
            ej_by_tract[g] = {
                "bg_count": rec["bg_count"],
                "ej":       rec["is_ej"],
                "criteria": set(rec["criteria"]),
            }

    if not ej_by_tract and not ej_by_town:
        # Try live fetch as last resort (block-group level)
        try:
            ej_df = _fetch_ej2020()
            if not ej_df.empty and "bg_geoid" in ej_df.columns:
                ej_df["GEOID"] = ej_df["bg_geoid"].str[:11]
                for _, row in ej_df.iterrows():
                    g = row["GEOID"]
                    rec = ej_by_tract.setdefault(g, {"bg_count": 0, "criteria": set(), "ej": False})
                    rec["bg_count"] += 1
                    if row.get("ej") in (1, "1", True, "true"):
                        rec["ej"] = True
                    if row.get("ej_crit"):
                        rec["criteria"].add(str(row["ej_crit"]))
        except Exception as exc:  # noqa: BLE001
            log.warning("EJ2020 live fetch failed: %s", exc)

    # ── Build output rows ─────────────────────────────────────────────────────
    rows = []
    all_geoids = sorted(geoids)
    prose_idx = prose_df.set_index("GEOID")
    race_idx  = race_df.set_index("GEOID")

    for g in all_geoids:
        # Language context
        if g in prose_idx.index:
            pr = prose_idx.loc[g]
            # B16001_001E = total population 5+
            # B16001_002E = speak English only
            total_lang = _to_num(pr.get("B16001_001E"))
            eng_only   = _to_num(pr.get("B16001_002E"))
            non_eng    = (
                (total_lang - eng_only)
                if total_lang is not None and eng_only is not None
                else None
            )
            pct_non_eng = (
                round(100.0 * non_eng / total_lang, 1)
                if total_lang and non_eng is not None
                else None
            )
            # Top language groups present in staged data
            lang_fields = {
                "B16001_003E": "Spanish",
                "B16001_006E": "French / Haitian Creole",
                "B16001_009E": "Portuguese",
                "B16001_013E": "Chinese (incl. Mandarin, Cantonese)",
            }
            top_langs = {}
            for col, name in lang_fields.items():
                v = _to_num(pr.get(col))
                if v is not None and total_lang and v / total_lang >= 0.02:
                    top_langs[name] = round(v)

            ctx_lang = {
                "total_age5plus": _to_num(pr.get("B16001_001E")),
                "english_only":   eng_only,
                "pct_non_english": pct_non_eng,
                "top_languages":  top_langs,
                "source": "ACS 2020-2024 5-year B16001",
            }
        else:
            ctx_lang = None

        # Race context
        if g in race_idx.index:
            rr = race_idx.loc[g]
            total_pop = _to_num(rr.get("B02001_001E"))
            ctx_race = {
                "total_pop":     total_pop,
                "white_alone":   _to_num(rr.get("B02001_002E")),
                "black_alone":   _to_num(rr.get("B02001_003E")),
                "asian_alone":   _to_num(rr.get("B02001_005E")),
                "hispanic":      _to_num(rr.get("B03002_012E")),
                "source": "ACS 2020-2024 5-year B02001/B03002 (DP05)",
            }
        else:
            ctx_race = None

        # EJ2020 context
        if g in ej_by_tract:
            ej = ej_by_tract[g]
            ctx_ej = {
                "bg_count":  ej["bg_count"],
                "is_ej":     ej["ej"],
                "criteria":  sorted(ej["criteria"]),
                "level":     "municipality",
                "source": "MassGIS EJ2020 (municipality-level extract)",
            }
        else:
            ctx_ej = None

        rows.append({
            "GEOID":    g,
            "ctx_lang": json.dumps(ctx_lang) if ctx_lang else None,
            "ctx_race": json.dumps(ctx_race) if ctx_race else None,
            "ctx_ej":   json.dumps(ctx_ej)   if ctx_ej   else None,
        })

    df = pd.DataFrame(rows)
    df.to_parquet(out_path)
    log.info("Prose: wrote %s (%d rows)", out_path, len(df))
    return df


def _to_num(val) -> float | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        v = float(val)
        return None if v < 0 else v
    except (TypeError, ValueError):
        return None
