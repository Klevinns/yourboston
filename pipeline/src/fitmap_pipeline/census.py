"""Census stage: ACS ingest via API with full B25070/B25091 columns.

Fetches from https://api.census.gov/data/2024/acs/acs5 with full burden columns
(B25070_001E,007E-011E; B25091_001E,008E-012E,019E-023E) plus B01003_001E (pop).
Falls back to staged CSVs if API fails.

Writes: pipeline/data/interim/census.parquet
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

import pandas as pd
import requests

from .config import (
    ACS_PROSE_CSV,
    ACS_RACE_CSV,
    ACS_SCORED_CSV,
    COUNTIES,
    INTERIM_DIR,
    STATE,
    VALUE_TOPCODE,
)
from .utils import null_sentinels

log = logging.getLogger(__name__)

# Raw API cache path
_RAW_DIR = INTERIM_DIR.parent / "raw"
_ACS_BURDEN_CACHE = _RAW_DIR / "acs_burden_2024.json"


def _ns(val) -> float | None:
    """Null-sentinel wrapper that handles pandas NaN."""
    if pd.isna(val):
        return None
    return null_sentinels(val)


def _fetch_burden_from_api(geoids: set[str]) -> pd.DataFrame | None:
    """Fetch full B25070/B25091 columns from ACS API, one county at a time."""
    api_key = os.environ.get("CENSUS_API_KEY", "")
    if not api_key:
        log.warning("CENSUS_API_KEY not set; falling back to staged data")
        return None

    # Full variable list staying under 50 per call
    # B25070 (renter burden): 001E, 007E-011E
    # B25091 (owner burden): 001E, 008E-012E, 019E-023E
    # B01003_001E (population)
    vars_list = [
        "NAME",
        "B25070_001E", "B25070_007E", "B25070_008E", "B25070_009E", "B25070_010E", "B25070_011E",
        "B25091_001E", "B25091_008E", "B25091_009E", "B25091_010E", "B25091_011E", "B25091_012E",
        "B25091_019E", "B25091_020E", "B25091_021E", "B25091_022E", "B25091_023E",
        "B01003_001E",
    ]
    get_vars = ",".join(vars_list)

    all_rows = []
    for county in COUNTIES:
        url = f"https://api.census.gov/data/2024/acs/acs5"
        params = {
            "get": get_vars,
            "for": "tract:*",
            "in": f"state:{STATE} county:{county}",
            "key": api_key,
        }
        try:
            resp = requests.get(url, params=params, timeout=60)
            resp.raise_for_status()
        except requests.RequestException as exc:
            # Sanitize error: don't include URL with key
            raise RuntimeError(f"Census API request failed: {type(exc).__name__}") from None

        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            log.warning("Failed to parse JSON for county %s: %s", county, exc)
            continue

        if not data or len(data) < 2:
            log.warning("Empty response for county %s", county)
            continue

        headers = data[0]
        for row in data[1:]:
            rec = dict(zip(headers, row))
            # Census API returns tract as string with leading zeros preserved,
            # county as string, state as string. Build proper 11-digit GEOID.
            state = str(rec.get("state", "")).zfill(2)
            county_str = str(rec.get("county", "")).zfill(3)
            tract = str(rec.get("tract", "")).zfill(6)
            geoid = state + county_str + tract
            if geoid not in geoids:
                continue
            # Apply sentinel nulling to every numeric field
            for k, v in rec.items():
                if k == "NAME":
                    continue
                rec[k] = null_sentinels(v)
            rec["GEOID"] = geoid
            all_rows.append(rec)

    if not all_rows:
        return None

    df = pd.DataFrame(all_rows)
    return df


def _load_staged_for_comparison(geoids: set[str]) -> pd.DataFrame | None:
    """Load staged CSV for vintage comparison."""
    if not ACS_SCORED_CSV.exists():
        return None
    df = pd.read_csv(ACS_SCORED_CSV, dtype={"GEOID": str})
    df["GEOID"] = df["GEOID"].astype(str).str.strip().str.zfill(11)
    return df[df["GEOID"].isin(geoids)].copy()


def load_staged_rent_value(geoids: set[str]) -> pd.DataFrame:
    """Load rent (B25064) and value (B25077) from staged CSV."""
    if not ACS_SCORED_CSV.exists():
        return pd.DataFrame()
    df = pd.read_csv(ACS_SCORED_CSV, dtype={"GEOID": str})
    df["GEOID"] = df["GEOID"].astype(str).str.strip().str.zfill(11)
    df = df[df["GEOID"].isin(geoids)].copy()

    # Apply sentinel nulling
    for col in ["B25064_001E", "B25064_001M", "B25077_001E", "B25077_001M"]:
        if col in df.columns:
            df[col] = df[col].apply(_ns)

    # Compute rent and value fields
    df["rent"] = df["B25064_001E"]
    df["rent_moe"] = df["B25064_001M"]
    df["value"] = df["B25077_001E"]
    df["value_moe"] = df["B25077_001M"]
    df["value_topcoded"] = df["value"].apply(
        lambda v: v is not None and v >= VALUE_TOPCODE
    )
    df["value_moe_high"] = df.apply(
        lambda r: (
            r["value"] is not None
            and r["value"] > 0
            and r["value_moe"] is not None
            and r["value_moe"] / r["value"] > 0.30
        ),
        axis=1,
    )

    return df[["GEOID", "rent", "rent_moe", "value", "value_moe",
               "value_topcoded", "value_moe_high"]]


def run(geoids: set[str], force: bool = False) -> pd.DataFrame:
    """Run census stage; return dataframe with GEOID + scored census fields."""
    INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    _RAW_DIR.mkdir(parents=True, exist_ok=True)
    out_path = INTERIM_DIR / "census.parquet"

    if out_path.exists() and not force:
        log.info("Census: loading cached %s", out_path)
        df = pd.read_parquet(out_path)
        df["GEOID"] = df["GEOID"].astype(str).str.zfill(11)
        return df

    # Try API first
    burden_df = None
    if _ACS_BURDEN_CACHE.exists():
        log.info("Census: loading cached API data from %s", _ACS_BURDEN_CACHE)
        with open(_ACS_BURDEN_CACHE) as f:
            burden_df = pd.DataFrame(json.load(f))
        if "GEOID" not in burden_df.columns:
            log.warning("Cache file invalid, re-fetching")
            burden_df = None

    # Fetch from API only (no fallback to staged for burden)
    try:
        burden_df = _fetch_burden_from_api(geoids)
    except RuntimeError as exc:
        raise RuntimeError(
            "Census API fetch failed; burden needs B25070/B25091 detail columns. "
            "Set CENSUS_API_KEY and retry."
        ) from None

    if burden_df is not None and not burden_df.empty:
        # Cache raw API response only when all five counties returned rows
        counties_with_data = burden_df["county"].nunique() if "county" in burden_df.columns else 0
        if counties_with_data == len(COUNTIES):
            with open(_ACS_BURDEN_CACHE, "w") as f:
                json.dump(burden_df.drop(columns=["NAME"], errors="ignore").to_dict(orient="records"), f)
            log.info("Census: cached API data to %s", _ACS_BURDEN_CACHE)
        else:
            log.warning("Census: only %d / %d counties returned data, not caching", 
                        counties_with_data, len(COUNTIES))

    # Fall back to staged for comparison and rent/value
    staged = _load_staged_for_comparison(geoids)

    # Log vintage comparison if we have both
    mismatch_count = 0
    if burden_df is not None and not burden_df.empty and staged is not None and not staged.empty:
        api_set = set(burden_df["GEOID"].astype(str).str.zfill(11))
        staged_set = set(staged["GEOID"])
        common = api_set & staged_set
        if common:
            # Compare B25070_001E totals (renter households)
            api_001 = burden_df.set_index("GEOID")["B25070_001E"].astype(float)
            staged_001 = staged.set_index("GEOID")["B25070_001E"]
            mismatches = 0
            for g in common:
                api_val = api_001.get(g)
                st_val = staged_001.get(g)
                if api_val is not None and st_val is not None and abs(float(api_val) - float(st_val)) > 1:
                    mismatches += 1
            mismatch_count = mismatches
            log.info("Census: vintage comparison: %d / %d tracts have B25070_001E mismatch",
                     mismatches, len(common))

    # Compute burden from API data only (no fallback to staged)
    if burden_df is None or burden_df.empty:
        raise RuntimeError(
            "Census API fetch failed; burden needs B25070/B25091 detail columns. "
            "Set CENSUS_API_KEY and retry."
        )

    from .utils import burden_pct, burden_renter_pct, burden_owner_pct

    burden_df["GEOID"] = burden_df["GEOID"].astype(str).str.zfill(11)

    b70_cols = ["B25070_001E", "B25070_007E", "B25070_008E", "B25070_009E",
                "B25070_010E", "B25070_011E"]
    b91_cols = ["B25091_001E", "B25091_008E", "B25091_009E", "B25091_010E",
                "B25091_011E", "B25091_012E", "B25091_019E", "B25091_020E",
                "B25091_021E", "B25091_022E", "B25091_023E"]

    burden_vals, burden_r_vals, burden_o_vals = [], [], []
    for _, row in burden_df.iterrows():
        b70 = {k: _ns(row.get(k)) for k in b70_cols}
        b91 = {k: _ns(row.get(k)) for k in b91_cols}
        burden_vals.append(burden_pct(b70, b91))
        burden_r_vals.append(burden_renter_pct(b70))
        burden_o_vals.append(burden_owner_pct(b91))

    burden_df["burden"] = burden_vals
    burden_df["burden_renter"] = burden_r_vals
    burden_df["burden_owner"] = burden_o_vals

    # Population (B01003_001E)
    burden_df["pop"] = burden_df["B01003_001E"]

    result = burden_df[["GEOID", "burden", "burden_renter", "burden_owner", "pop"]].copy()

    # Merge rent/value from staged (always available)
    rent_value = load_staged_rent_value(geoids)
    result = result.merge(rent_value, on="GEOID", how="outer")

    # Ensure all geoids have a row
    have = set(result["GEOID"])
    missing = [{"GEOID": g, "burden": None, "burden_renter": None,
                "burden_owner": None, "pop": None} for g in geoids if g not in have]
    if missing:
        result = pd.concat([result, pd.DataFrame(missing)], ignore_index=True)

    result.to_parquet(out_path)
    log.info("Census: wrote %s (%d rows)", out_path, len(result))

    # Report mismatch count
    if mismatch_count > 0:
        log.info("Census: %d tracts have vintage mismatch between API and staged B25070_001E", mismatch_count)

    return result
