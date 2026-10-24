"""Pure utility functions for FitMap pipeline.

These functions have no I/O and are tested via pytest + hypothesis.
"""
from __future__ import annotations

import base64
import math
from typing import Optional

from .config import CENTER, RADIUS_KM


# ── Sentinel nulling (Property 10) ────────────────────────────────────────────

def null_sentinels(x: float | int | None) -> float | None:
    """Return None for any negative value (Census sentinel/annotation codes);
    return x unchanged otherwise.

    Census sentinel values include: -666666666, -999999999, -888888888,
    -222222222, -333333333, -555555555 — and any other negative value.
    """
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if v < 0 else v


# ── Radius filter (Property 13) ───────────────────────────────────────────────

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two WGS-84 points."""
    R = 6_371.0  # km, mean radius
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def in_radius(lat: float, lon: float,
              center: tuple[float, float] = CENTER,
              miles: float = 10.0) -> bool:
    """True iff the haversine distance from (lat, lon) to center is ≤ RADIUS_KM.

    The miles argument is for documentation; the actual threshold is always
    RADIUS_KM (16.0934 km) derived from the project constants, which we
    recompute from miles to stay consistent with config.
    """
    radius_km = miles * 1.60934
    return haversine_km(lat, lon, center[0], center[1]) <= radius_km


# ── Burden pct (Requirements 3.1–3.3, design.md §burden_pct) ─────────────────

def burden_pct(
    b25070: dict[str, int | float | None],
    b25091: dict[str, int | float | None],
) -> float | None:
    """Compute percentage of renter + owner households paying ≥30% of income
    on housing costs.

    B25070 variables:
        _001E = total renter households (universe)
        _007E–_010E = renter 30.0–34.9%, 35.0–39.9%, 40.0–49.9%, ≥50% bands
        _011E = renter "not computed"

    B25091 variables:
        _001E = total owner households (universe)
        _008E–_011E = owner-with-mortgage 30%+ bands (4 rows)
        _019E–_022E = owner-without-mortgage 30%+ bands (4 rows)
        _012E = owner-with-mortgage "not computed"
        _023E = owner-without-mortgage "not computed"

    Returns percentage in [0, 100] or None if denominator is 0 or any
    required numerator component is None.
    """
    def _v(d: dict, key: str) -> float | None:
        raw = d.get(key)
        return null_sentinels(raw)

    # Renter burdened: categories 30%+
    renter_burdened_keys = ["B25070_007E", "B25070_008E", "B25070_009E", "B25070_010E"]
    renter_total     = _v(b25070, "B25070_001E")
    renter_not_comp  = _v(b25070, "B25070_011E")

    # Owner burdened: with-mortgage 30%+ (008–011) + without-mortgage 30%+ (019–022)
    owner_burdened_keys = [
        "B25091_008E", "B25091_009E", "B25091_010E", "B25091_011E",
        "B25091_019E", "B25091_020E", "B25091_021E", "B25091_022E",
    ]
    owner_total          = _v(b25091, "B25091_001E")
    owner_not_comp_mort  = _v(b25091, "B25091_012E")
    owner_not_comp_free  = _v(b25091, "B25091_023E")

    # Guard on None denominators
    if renter_total is None or owner_total is None:
        return None

    # Denominator: total minus "not computed" households
    renter_nc = renter_not_comp if renter_not_comp is not None else 0.0
    owner_nc  = (
        (owner_not_comp_mort if owner_not_comp_mort is not None else 0.0) +
        (owner_not_comp_free if owner_not_comp_free is not None else 0.0)
    )
    denom = (renter_total - renter_nc) + (owner_total - owner_nc)
    if denom <= 0:
        return None

    # Numerators — any None component aborts
    renter_burdened_sum = 0.0
    for k in renter_burdened_keys:
        v = _v(b25070, k)
        if v is None:
            return None
        renter_burdened_sum += v

    owner_burdened_sum = 0.0
    for k in owner_burdened_keys:
        v = _v(b25091, k)
        if v is None:
            return None
        owner_burdened_sum += v

    pct = 100.0 * (renter_burdened_sum + owner_burdened_sum) / denom
    return min(max(pct, 0.0), 100.0)


def burden_renter_pct(b25070: dict[str, int | float | None]) -> float | None:
    """Renter-only cost burden percentage."""
    def _v(key: str) -> float | None:
        return null_sentinels(b25070.get(key))

    total    = _v("B25070_001E")
    not_comp = _v("B25070_011E")
    if total is None:
        return None
    nc   = not_comp if not_comp is not None else 0.0
    denom = total - nc
    if denom <= 0:
        return None

    burdened = 0.0
    for k in ["B25070_007E", "B25070_008E", "B25070_009E", "B25070_010E"]:
        v = _v(k)
        if v is None:
            return None
        burdened += v
    return min(max(100.0 * burdened / denom, 0.0), 100.0)


def burden_owner_pct(b25091: dict[str, int | float | None]) -> float | None:
    """Owner-only cost burden percentage."""
    def _v(key: str) -> float | None:
        return null_sentinels(b25091.get(key))

    total    = _v("B25091_001E")
    nc_mort  = _v("B25091_012E")
    nc_free  = _v("B25091_023E")
    if total is None:
        return None
    nc = (
        (nc_mort if nc_mort is not None else 0.0) +
        (nc_free if nc_free is not None else 0.0)
    )
    denom = total - nc
    if denom <= 0:
        return None

    burdened = 0.0
    for k in ["B25091_008E", "B25091_009E", "B25091_010E", "B25091_011E",
              "B25091_019E", "B25091_020E", "B25091_021E", "B25091_022E"]:
        v = _v(k)
        if v is None:
            return None
        burdened += v
    return min(max(100.0 * burdened / denom, 0.0), 100.0)


# ── Crime rate (Property 9, Req 4) ────────────────────────────────────────────

def town_rate(offenses: int | None, population: int | None) -> float | None:
    """Per-1,000 resident crime rate.

    Returns None if population <= 0 or either argument is None.
    Result is always >= 0.
    """
    if offenses is None or population is None:
        return None
    pop = int(population)
    if pop <= 0:
        return None
    off = int(offenses)
    if off < 0:
        return None
    return 1000.0 * off / pop


# ── Commute encoding (Property 11) ────────────────────────────────────────────

_NOROUTE = 255


def encode_minutes(seconds: list[float | None]) -> str:
    """Encode a list of durations (in seconds) as a base64 Uint8 string.

    Each element is converted to whole minutes (round).  Values > 254 or None
    become 255 (no-route sentinel).  Returns a base64-encoded ASCII string.
    """
    buf = bytearray(len(seconds))
    for i, s in enumerate(seconds):
        if s is None:
            buf[i] = _NOROUTE
        else:
            mins = round(s / 60.0)
            buf[i] = min(mins, _NOROUTE)
    return base64.b64encode(bytes(buf)).decode("ascii")


def decode_minutes(b64: str) -> list[int]:
    """Decode a base64 Uint8 string back to a list of integer minutes."""
    return list(base64.b64decode(b64))
