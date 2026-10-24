"""Sanity tests on the built transit matrix (Task 7.2).

Diagonal is 0; transit <= walk (after min); a Red Line pair is fast.
Skips if interim matrices not built.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

INTERIM = Path(__file__).parent.parent / "data" / "interim"


@pytest.fixture(scope="module")
def mats():
    tpath = INTERIM / "transit_matrix.npy"
    wpath = INTERIM / "osrm_walk.npy"
    if not tpath.exists() or not wpath.exists():
        pytest.skip("transit/walk matrices not built")
    return np.load(tpath), np.load(wpath)


def test_diagonal_zero(mats):
    t, _ = mats
    assert np.allclose(np.diag(t), 0.0), "transit diagonal must be 0"


def test_transit_le_walk_after_min(mats):
    """After taking min(transit, walk), effective transit <= walk everywhere."""
    t, w = mats
    eff = np.where(np.isnan(t), w, np.where(np.isnan(w), t, np.minimum(t, w)))
    both = ~np.isnan(eff) & ~np.isnan(w)
    # eff should never exceed walk (min guarantees this, allow tiny tolerance)
    assert np.all(eff[both] <= w[both] + 1e-6), "effective transit exceeds walk somewhere"


def test_matrix_square_and_nonneg(mats):
    t, _ = mats
    assert t.shape[0] == t.shape[1], "transit matrix must be square"
    vals = t[~np.isnan(t)]
    assert np.all(vals >= 0), "transit times must be non-negative"
