"""Unit tests for crime aggregation functions."""
from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point, Polygon

from fitmap_pipeline.crime import aggregate_counts


def make_tract(geoid: str, town: str, lat: float, lon: float) -> gpd.GeoDataFrame:
    """Create a simple square tract centered on (lat, lon)."""
    delta = 0.001  # ~100m
    poly = Polygon([
        (lon - delta, lat - delta),
        (lon + delta, lat - delta),
        (lon + delta, lat + delta),
        (lon - delta, lat + delta),
        (lon - delta, lat - delta),
    ])
    return gpd.GeoDataFrame({"GEOID": [geoid], "town": [town]}, geometry=[poly], crs="EPSG:4326")


def make_incident(lat: float, lon: float, crime_type: str) -> pd.DataFrame:
    """Create a single incident DataFrame."""
    return pd.DataFrame({
        "lat": [lat],
        "lon": [lon],
        "crime_type": [crime_type],
    })


def test_aggregate_counts_all_boston_tracts_get_rows():
    """Every Boston tract gets a row, even with zero or only petty incidents."""
    # Two Boston tracts
    boston1 = make_tract("25001020100", "Boston", 42.30, -71.05)
    boston2 = make_tract("25001020200", "Boston", 42.31, -71.06)
    # One Somerville tract (should not appear in results)
    somerville = make_tract("25019010100", "Somerville", 42.40, -71.10)
    tracts = pd.concat([boston1, boston2, somerville], ignore_index=True)

    # Incident in tract 1 (serious)
    inc1 = make_incident(42.30, -71.05, "serious")
    # Incident in tract 1 (petty only)
    inc2 = make_incident(42.3005, -71.0505, "petty")
    # No incidents in tract 2 (should show 0,0)
    incidents = pd.concat([inc1, inc2], ignore_index=True)

    result = aggregate_counts(incidents, tracts, "Boston")

    # Every Boston tract should have a row
    assert len(result) == 2, f"Expected 2 Boston tracts, got {len(result)}"
    geoids = set(result["GEOID"])
    assert "25001020100" in geoids
    assert "25001020200" in geoids

    # Check counts
    row1 = result[result["GEOID"] == "25001020100"].iloc[0]
    assert row1["serious_count"] == 1, f"Expected serious_count=1, got {row1['serious_count']}"
    assert row1["petty_count"] == 1, f"Expected petty_count=1, got {row1['petty_count']}"

    row2 = result[result["GEOID"] == "25001020200"].iloc[0]
    assert row2["serious_count"] == 0, f"Expected serious_count=0, got {row2['serious_count']}"
    assert row2["petty_count"] == 0, f"Expected petty_count=0, got {row2['petty_count']}"


def test_aggregate_counts_somerville_none():
    """Somerville tracts should not appear in Boston results."""
    boston = make_tract("25001020100", "Boston", 42.30, -71.05)
    somerville = make_tract("25019010100", "Somerville", 42.40, -71.10)
    tracts = pd.concat([boston, somerville], ignore_index=True)

    incidents = make_incident(42.40, -71.10, "serious")  # Somerville incident

    result = aggregate_counts(incidents, tracts, "Boston")

    # Only Boston tract should appear
    assert len(result) == 1
    assert result.iloc[0]["GEOID"] == "25001020100"
    assert result.iloc[0]["serious_count"] == 0
    assert result.iloc[0]["petty_count"] == 0


def test_aggregate_counts_petty_only():
    """Tract with only petty incidents should still get a row with serious_count=0."""
    boston = make_tract("25001020100", "Boston", 42.30, -71.05)
    tracts = boston.copy()

    incidents = make_incident(42.30, -71.05, "petty")

    result = aggregate_counts(incidents, tracts, "Boston")

    assert len(result) == 1
    assert result.iloc[0]["serious_count"] == 0
    assert result.iloc[0]["petty_count"] == 1


def test_aggregate_counts_no_incidents():
    """Tract with no incidents should get row with counts=0."""
    boston = make_tract("25001020100", "Boston", 42.30, -71.05)
    tracts = boston.copy()

    incidents = pd.DataFrame(columns=["lat", "lon", "crime_type"])

    result = aggregate_counts(incidents, tracts, "Boston")

    assert len(result) == 1
    assert result.iloc[0]["serious_count"] == 0
    assert result.iloc[0]["petty_count"] == 0