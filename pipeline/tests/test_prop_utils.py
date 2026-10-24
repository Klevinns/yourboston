"""Property-based tests for YourBoston pipeline pure functions.

Tests Properties 8–11, 13 from design.md using pytest + hypothesis.
"""
from __future__ import annotations

import math
import re
import sys
from pathlib import Path

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

# Add src to path for direct import
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fitmap_pipeline.utils import (
    null_sentinels,
    in_radius,
    haversine_km,
    burden_pct,
    burden_renter_pct,
    burden_owner_pct,
    town_rate,
    encode_minutes,
    decode_minutes,
)
from fitmap_pipeline.config import CENTER, RADIUS_KM
from fitmap_pipeline.crime import classify_bpd, classify_cambridge

SENTINEL_VALUES = [-666666666, -999999999, -888888888, -222222222, -333333333, -555555555]

# ── Property 10: Sentinels never survive ─────────────────────────────────────

class TestProp10NullSentinels:
    def test_known_sentinels_become_none(self):
        for s in SENTINEL_VALUES:
            assert null_sentinels(s) is None, f"Sentinel {s} survived"

    def test_none_stays_none(self):
        assert null_sentinels(None) is None

    @given(st.integers(max_value=-1))
    def test_any_negative_becomes_none(self, x):
        assert null_sentinels(x) is None

    @given(st.integers(min_value=0))
    def test_nonneg_integers_unchanged(self, x):
        result = null_sentinels(x)
        assert result is not None
        assert result == float(x)

    @given(st.floats(min_value=0.0, allow_nan=False, allow_infinity=False))
    def test_nonneg_floats_unchanged(self, x):
        result = null_sentinels(x)
        assert result is not None
        assert result == x


# ── Property 13: Radius membership ───────────────────────────────────────────

class TestProp13Radius:
    @given(
        lat=st.floats(min_value=40.0, max_value=44.0),
        lon=st.floats(min_value=-73.0, max_value=-69.0),
    )
    def test_in_radius_iff_haversine_le_threshold(self, lat, lon):
        km = haversine_km(lat, lon, CENTER[0], CENTER[1])
        expected = km <= RADIUS_KM
        assert in_radius(lat, lon) == expected, (
            f"in_radius({lat},{lon}) = {in_radius(lat, lon)} but km={km:.4f}, RADIUS_KM={RADIUS_KM}"
        )

    def test_center_is_in_radius(self):
        assert in_radius(CENTER[0], CENTER[1]) is True

    def test_far_point_is_out(self):
        # Providence RI is ~60 km from Boston
        assert in_radius(41.8239, -71.4128) is False

    def test_nearby_point_is_in(self):
        # Cambridge is ~3 km from center
        assert in_radius(42.3736, -71.1097) is True


# ── Property 9: No negative quantities ───────────────────────────────────────

class TestProp9NoNegatives:
    @given(st.floats(allow_nan=False, allow_infinity=False))
    def test_burden_pct_nonneg_or_none(self, total):
        """burden_pct always in [0,100] or None."""
        b70 = {
            "B25070_001E": abs(total) + 10,
            "B25070_007E": 1.0,
            "B25070_008E": 1.0,
            "B25070_009E": 1.0,
            "B25070_010E": 1.0,
            "B25070_011E": 0.0,
        }
        b91 = {
            "B25091_001E": abs(total) + 10,
            "B25091_008E": 1.0, "B25091_009E": 0.0,
            "B25091_010E": 0.0, "B25091_011E": 0.0,
            "B25091_019E": 1.0, "B25091_020E": 0.0,
            "B25091_021E": 0.0, "B25091_022E": 0.0,
            "B25091_012E": 0.0, "B25091_023E": 0.0,
        }
        result = burden_pct(b70, b91)
        if result is not None:
            assert 0.0 <= result <= 100.0, f"burden_pct={result} out of range"

    @given(st.integers(min_value=0, max_value=100000),
           st.integers(min_value=1, max_value=100000))
    def test_town_rate_nonneg(self, offenses, population):
        result = town_rate(offenses, population)
        if result is not None:
            assert result >= 0.0

    def test_town_rate_zero_pop_is_none(self):
        assert town_rate(10, 0) is None
        assert town_rate(10, -1) is None

    def test_town_rate_none_inputs_are_none(self):
        assert town_rate(None, 1000) is None
        assert town_rate(10, None) is None


# ── Property 11: Commute encoding round-trip ──────────────────────────────────

class TestProp11EncodingRoundTrip:
    @given(
        st.lists(
            st.one_of(st.none(), st.floats(min_value=0, max_value=20000, allow_nan=False)),
            min_size=1,
            max_size=600,
        )
    )
    @settings(max_examples=300)
    def test_roundtrip(self, seconds_list):
        encoded = encode_minutes(seconds_list)
        decoded = decode_minutes(encoded)
        assert len(decoded) == len(seconds_list), "Length mismatch after round-trip"
        for i, (orig, dec) in enumerate(zip(seconds_list, decoded)):
            if orig is None:
                assert dec == 255, f"[{i}] None should encode to 255, got {dec}"
            else:
                expected_min = min(round(orig / 60.0), 255)
                assert dec == expected_min, (
                    f"[{i}] {orig} s → expected {expected_min} min, got {dec}"
                )

    def test_none_encodes_to_255(self):
        enc = encode_minutes([None])
        dec = decode_minutes(enc)
        assert dec[0] == 255

    def test_large_value_caps_at_255(self):
        enc = encode_minutes([255 * 60 + 1])  # 255 min + 1 sec
        dec = decode_minutes(enc)
        assert dec[0] == 255

    def test_zero_encodes_to_zero(self):
        enc = encode_minutes([0.0])
        dec = decode_minutes(enc)
        assert dec[0] == 0


# ── Example test: hand-computed burden ───────────────────────────────────────

class TestBurdenHandComputed:
    def test_example_tract(self):
        """Hand-computed example using real-ish numbers."""
        # 500 renter households total, 0 not-computed
        # 30%+ bands: 50+30+20+10 = 110 burdened renters
        # 300 owner households total, 0 not-computed
        # owner 30%+ with mortgage: 20+15+10+5 = 50
        # owner 30%+ free-clear: 10+8+5+2 = 25
        b70 = {
            "B25070_001E": 500,
            "B25070_007E": 50,
            "B25070_008E": 30,
            "B25070_009E": 20,
            "B25070_010E": 10,
            "B25070_011E": 0,
        }
        b91 = {
            "B25091_001E": 300,
            "B25091_008E": 20, "B25091_009E": 15,
            "B25091_010E": 10, "B25091_011E": 5,
            "B25091_019E": 10, "B25091_020E": 8,
            "B25091_021E": 5,  "B25091_022E": 2,
            "B25091_012E": 0,  "B25091_023E": 0,
        }
        denom = (500 - 0) + (300 - 0)
        numer = (50 + 30 + 20 + 10) + (20 + 15 + 10 + 5 + 10 + 8 + 5 + 2)
        expected = 100.0 * numer / denom
        result = burden_pct(b70, b91)
        assert result is not None
        assert abs(result - expected) < 0.01, f"Expected {expected:.4f}, got {result:.4f}"

    def test_sentinel_row_yields_none(self):
        """Sentinel values in burden inputs must produce None."""
        b70 = {
            "B25070_001E": -666666666,  # sentinel
            "B25070_007E": 10,
            "B25070_008E": 5,
            "B25070_009E": 3,
            "B25070_010E": 2,
            "B25070_011E": 0,
        }
        b91 = {"B25091_001E": 100, "B25091_012E": 0, "B25091_023E": 0}
        for k in ["B25091_008E","B25091_009E","B25091_010E","B25091_011E",
                  "B25091_019E","B25091_020E","B25091_021E","B25091_022E"]:
            b91[k] = 0
        assert burden_pct(b70, b91) is None


# ── GEOID integrity (Property 8) — checked on pipeline output ─────────────────

GEOID_RE = re.compile(r"^\d{11}$")


def test_geoid_format_on_staged_csv():
    r"""Every GEOID in tracts_10mi.csv matches ^\d{11}$."""
    import pandas as pd
    csv_path = Path("/root/hackachusetts/data/tracts_10mi.csv")
    if not csv_path.exists():
        pytest.skip("Staged CSV not available")
    df = pd.read_csv(csv_path, dtype={"GEOID": str})
    df["GEOID"] = df["GEOID"].astype(str).str.zfill(11)
    for g in df["GEOID"]:
        assert GEOID_RE.match(g), f"Bad GEOID: {g!r}"
    assert df["GEOID"].nunique() == len(df), "Duplicate GEOIDs in tracts_10mi.csv"


# ── Crime classification tests ────────────────────────────────────────────────

class TestCrimeClassification:
    """Tests for classify_bpd and classify_cambridge functions."""

    def test_classify_bpd_returns_valid(self):
        """classify_bpd always returns one of serious/petty/noncrime."""
        # Known serious code
        assert classify_bpd("111", {}) == "serious"

        # Test with code that maps to noncrime via name
        name_by_code = {"3115": "INVESTIGATE PERSON"}
        assert classify_bpd("3115", name_by_code) == "noncrime"

    def test_classify_cambridge_returns_valid(self):
        """classify_cambridge always returns one of serious/petty/noncrime."""
        # Known serious crime
        assert classify_cambridge("ROBBERY") == "serious"
        assert classify_cambridge("AGGRAVATED ASSAULT") == "serious"

        # Known non-crime
        assert classify_cambridge("HIT AND RUN") == "noncrime"
        assert classify_cambridge("ACCIDENT") == "noncrime"

        # Default to petty
        assert classify_cambridge("SOME UNKNOWN CRIME") == "petty"

    @given(st.text(min_size=1, max_size=50))
    def test_classify_cambridge_covers_all(self, desc):
        result = classify_cambridge(desc)
        assert result in {"serious", "petty", "noncrime"}

    @given(st.text(min_size=1, max_size=10))
    def test_classify_bpd_covers_all(self, code):
        name_by_code = {}  # No name lookup - should still work
        result = classify_bpd(code, name_by_code)
        assert result in {"serious", "petty", "noncrime"}


# ── Rate rule tests ────────────────────────────────────────────────────────────

class TestRateRule:
    """Tests for rate computation with low-population handling."""

    @given(st.integers(min_value=0, max_value=10000), st.integers(min_value=1))
    def test_rate_with_pop_returns_value(self, count, pop):
        result = town_rate(count, pop)
        assert result is not None
        assert result >= 0.0

    def test_rate_pop_none_returns_none(self):
        assert town_rate(10, None) is None

    def test_rate_count_zero_returns_zero(self):
        """Zero incidents should yield 0.0 rate, not None."""
        result = town_rate(0, 1000)
        assert result is not None
        assert result == 0.0
