"""Schema + property tests on the final web/data/fitmap.geojson.

Properties 8 (GEOID integrity), 9 (no negatives), 11 (commute vectors decode).
Skips gracefully if the file has not been built yet.
"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import pytest

GEOJSON = Path(__file__).parent.parent.parent / "web" / "data" / "fitmap.geojson"
GEOID_RE = re.compile(r"^\d{11}$")

NONNEG = ["rent", "rent_moe", "value", "value_moe", "crime_serious",
          "crime_property", "campus", "transit"]
PCT = ["burden", "burden_renter", "burden_owner"]
MODES = ["cm_walk", "cm_bike", "cm_car", "cm_transit"]


@pytest.fixture(scope="module")
def doc():
    if not GEOJSON.exists():
        pytest.skip("fitmap.geojson not built")
    return json.loads(GEOJSON.read_text())


def test_feature_collection(doc):
    assert doc["type"] == "FeatureCollection"
    assert len(doc["features"]) > 0


# ── Property 8: GEOID integrity ───────────────────────────────────────────────

def test_prop8_geoid_integrity(doc):
    feats = doc["features"]
    seen = set()
    prev = ""
    for i, f in enumerate(feats):
        p = f["properties"]
        g = p["geoid"]
        assert GEOID_RE.match(g), f"Bad GEOID {g!r}"
        assert g not in seen, f"Duplicate GEOID {g}"
        assert g >= prev, f"Not sorted at {i}: {g} < {prev}"
        assert p["idx"] == i, f"idx {p['idx']} != position {i}"
        seen.add(g)
        prev = g


# ── Property 9: No negative quantities ────────────────────────────────────────

def test_prop9_no_negatives(doc):
    for f in doc["features"]:
        p = f["properties"]
        for k in NONNEG:
            v = p.get(k)
            if v is not None:
                assert isinstance(v, (int, float)) and v >= 0, f"{k}={v} negative"
        for k in PCT:
            v = p.get(k)
            if v is not None:
                assert 0 <= v <= 100, f"{k}={v} out of [0,100]"


# ── Property 11: Commute vectors decode to N bytes ────────────────────────────

def test_prop11_commute_vectors(doc):
    n = len(doc["features"])
    for f in doc["features"]:
        p = f["properties"]
        for m in MODES:
            raw = base64.b64decode(p[m], validate=True)
            assert len(raw) == n, f"{m} decodes to {len(raw)} bytes, expected {n}"
        # Self commute should be 0
        idx = p["idx"]
        for m in MODES:
            raw = base64.b64decode(p[m])
            assert raw[idx] == 0, f"{m} self-commute at idx {idx} = {raw[idx]} != 0"


# ── Equity: only allowed ctx_ fields, scoring never sees them ─────────────────

def test_only_allowed_ctx_fields(doc):
    allowed = {"ctx_lang", "ctx_race", "ctx_ej"}
    for f in doc["features"]:
        p = f["properties"]
        ctx = {k for k in p if k.startswith("ctx_")}
        assert ctx <= allowed, f"Unexpected ctx_ fields: {ctx - allowed}"


# ── New output assertions per bug fixes ────────────────────────────────────────

def test_crime_year_is_2025(doc):
    """crime_year must be 2025 for all non-null crime tracts."""
    for f in doc["features"]:
        p = f["properties"]
        cs = p.get("crime_serious")
        if cs is not None:
            assert p.get("crime_year") == 2025, f"GEOID {p['geoid']} has crime_year {p.get('crime_year')}"


def test_crime_year_null_where_crime_null(doc):
    """crime_year must be null wherever crime_serious is null."""
    for f in doc["features"]:
        p = f["properties"]
        cs = p.get("crime_serious")
        cy = p.get("crime_year")
        if cs is None:
            assert cy is None, f"GEOID {p['geoid']} has crime_serious null but crime_year={cy}"


def test_crime_tracts_valid_and_suburban_uniform(doc):
    """Non-null crime: Boston/Cambridge are tract-level; suburbs are town-level (uniform rate)."""
    suburban_rates = {}
    for f in doc["features"]:
        p = f["properties"]
        cs = p.get("crime_serious")
        if cs is None:
            continue
        town = p.get("town", "")
        assert town, f"GEOID {p['geoid']} has crime but no town"
        if town in {"Boston", "Cambridge"}:
            continue
        suburban_rates.setdefault(town, set()).add(cs)
    for town, rates in suburban_rates.items():
        assert len(rates) == 1, f"suburban town {town!r} has non-uniform crime_serious: {sorted(rates)}"


def test_burden_owner_median_above_15(doc):
    """Sanity check: burden_owner median should be > 15% with full formula."""
    vals = [f["properties"]["burden_owner"] for f in doc["features"]
            if f["properties"].get("burden_owner") is not None]
    if vals:
        import statistics
        median = statistics.median(vals)
        assert median > 15, f"burden_owner median {median} is suspiciously low (<15)"


def test_rent_max_leq_3501(doc):
    """Rent max must be ≤ 3501 (ACS top-code)."""
    rents = [f["properties"]["rent"] for f in doc["features"]
             if f["properties"].get("rent") is not None]
    if rents:
        max_rent = max(rents)
        assert max_rent <= 3501, f"Rent max {max_rent} exceeds top-code 3501"


def test_boston_cambridge_crime_not_null(doc):
    """All Boston/Cambridge tracts should have non-null crime_serious except low-pop."""
    boston_cambridge_tracts = [
        f for f in doc["features"]
        if f["properties"].get("town") in {"Boston", "Cambridge"}
    ]
    # Some tracts may be nulled for low pop (<100), but should be <= 15
    null_count = sum(1 for f in boston_cambridge_tracts
                     if f["properties"].get("crime_serious") is None)
    # Allow up to 15 nulled tracts (low population tracts)
    assert null_count <= 15, f"Too many null Boston/Cambridge crime tracts: {null_count}"
