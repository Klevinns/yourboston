#!/usr/bin/env python3
"""YourBoston GeoJSON gate: size budget + schema invariants (stdlib only).

Usage: python3 scripts/hooks/check_geojson.py [path]   (default web/data/fitmap.geojson)
Exit 0 = pass (or file absent yet), 1 = violations (printed to stderr).
Encodes design.md Data Models + Properties 8, 9, 11 (shape), and Req 11.2, 12.
"""
from __future__ import annotations

import base64
import gzip
import json
import re
import sys
from pathlib import Path

GZIP_BUDGET = 2_000_000          # Req 11.2
RAW_SOFT_CAP = 6_000_000         # tech.md soft cap (warning only)
GEOID_RE = re.compile(r"^\d{11}$")
NONNEG = ["rent", "rent_moe", "value", "value_moe", "crime_serious",
          "crime_property", "campus", "transit"]
PCT = ["burden", "burden_renter", "burden_owner"]
MODES = ["cm_walk", "cm_bike", "cm_car", "cm_transit"]
ALLOWED_CTX = {"ctx_lang", "ctx_race", "ctx_ej"}


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "web/data/fitmap.geojson")
    if not path.exists():
        print(f"check_geojson: {path} not built yet - skipping")
        return 0

    raw = path.read_bytes()
    errs: list[str] = []
    gz = len(gzip.compress(raw, compresslevel=9))
    if gz > GZIP_BUDGET:
        errs.append(f"gzip size {gz:,} B exceeds budget {GZIP_BUDGET:,} B")
    if len(raw) > RAW_SOFT_CAP:
        print(f"check_geojson: WARNING raw size {len(raw):,} B > soft cap {RAW_SOFT_CAP:,} B",
              file=sys.stderr)

    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"check_geojson: invalid JSON: {e}", file=sys.stderr)
        return 1

    if doc.get("type") != "FeatureCollection":
        errs.append("top-level type must be FeatureCollection")
    feats = doc.get("features") or []
    n = len(feats)
    if n == 0:
        errs.append("no features")

    seen: set[str] = set()
    prev = ""
    for i, f in enumerate(feats):
        p = f.get("properties") or {}
        g = p.get("geoid")
        tag = f"feature[{i}] geoid={g!r}"
        if not isinstance(g, str) or not GEOID_RE.match(g):
            errs.append(f"{tag}: geoid must be an 11-digit string")
        elif g in seen:
            errs.append(f"{tag}: duplicate geoid")
        else:
            if g < prev:
                errs.append(f"{tag}: features not sorted by geoid")
            seen.add(g)
            prev = g
        if p.get("idx") != i:
            errs.append(f"{tag}: idx {p.get('idx')!r} != position {i}")
        if not f.get("geometry"):
            errs.append(f"{tag}: missing geometry")
        for k in NONNEG:
            v = p.get(k)
            if v is not None and (not isinstance(v, (int, float)) or v < 0):
                errs.append(f"{tag}: {k}={v!r} must be null or >= 0")
        for k in PCT:
            v = p.get(k)
            if v is not None and (not isinstance(v, (int, float)) or not 0 <= v <= 100):
                errs.append(f"{tag}: {k}={v!r} must be null or in [0,100]")
        for m in MODES:
            s = p.get(m)
            try:
                if len(base64.b64decode(s or "", validate=True)) != n:
                    errs.append(f"{tag}: {m} must decode to {n} bytes")
            except Exception:
                errs.append(f"{tag}: {m} is not valid base64")
        extra_ctx = {k for k in p if k.startswith("ctx_")} - ALLOWED_CTX
        if extra_ctx:
            errs.append(f"{tag}: unexpected ctx_ fields {sorted(extra_ctx)}")
        if len(errs) > 50:
            errs.append("... (truncated)")
            break

    if errs:
        print(f"check_geojson: FAIL {path} ({n} features, {gz:,} B gzip)", file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        return 1
    print(f"check_geojson: OK {path} ({n} features, {gz:,} B gzip <= {GZIP_BUDGET:,})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
