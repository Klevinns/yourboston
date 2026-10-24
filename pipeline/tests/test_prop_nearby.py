"""Property tests for export.build_nearby (Nearby panel data, never scored)."""
from __future__ import annotations

import math
import sys
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from fitmap_pipeline.export import NEARBY_CATEGORIES, build_nearby

_dist = st.one_of(st.none(), st.just(float("nan")), st.floats(min_value=0, max_value=20000))
_name = st.one_of(st.none(), st.just(float("nan")), st.just("  "), st.text(min_size=1, max_size=20))


@given(st.fixed_dictionaries({
    **{f"near_{c}_m": _dist for c in NEARBY_CATEGORIES},
    **{f"near_{c}_name": _name for c in NEARBY_CATEGORIES},
}))
def test_prop_nearby_shape(row):
    out = build_nearby(row)
    assert set(out) == set(NEARBY_CATEGORIES)
    for c, entry in out.items():
        m = row[f"near_{c}_m"]
        if m is None or math.isnan(m):
            # No feature in range → null, never a fabricated entry
            assert entry is None
            continue
        assert set(entry) == {"name", "m"}
        assert isinstance(entry["m"], int) and entry["m"] >= 0
        assert entry["name"] is None or (isinstance(entry["name"], str) and entry["name"].strip())


def test_nearby_has_no_rating_fields():
    row = {f"near_{c}_m": 100.0 for c in NEARBY_CATEGORIES}
    row.update({f"near_{c}_name": "X" for c in NEARBY_CATEGORIES})
    out = build_nearby(row)
    assert all(set(v) == {"name", "m"} for v in out.values())
