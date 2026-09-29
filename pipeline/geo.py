"""Geodesic distance helpers.

Distances use the haversine formula on a spherical Earth (mean radius
6,371 km). That is accurate to a few meters at the 800 m buffers this
pipeline uses, which is enough for venue-scale density while staying
in-memory and file-based.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

EARTH_RADIUS_M = 6_371_000.0
DEFAULT_BUFFER_RADIUS_M = 800.0

# Slightly short of the true meters-per-degree so the prefilter box is a
# little larger than the circle and cannot clip points inside the radius.
_LAT_M_PER_DEG = 110_574.0
_LON_M_PER_DEG = 111_320.0


def haversine_m(lat1, lon1, lat2, lon2) -> np.ndarray:
    """Great-circle distance in meters.

    Inputs may be scalars or arrays and are broadcast together.
    """
    phi1 = np.radians(np.asarray(lat1, dtype=np.float64))
    phi2 = np.radians(np.asarray(lat2, dtype=np.float64))
    dphi = np.radians(np.asarray(lat2, dtype=np.float64) - np.asarray(lat1, dtype=np.float64))
    dlambda = np.radians(np.asarray(lon2, dtype=np.float64) - np.asarray(lon1, dtype=np.float64))

    a = (
        np.sin(dphi / 2.0) ** 2
        + np.cos(phi1) * np.cos(phi2) * np.sin(dlambda / 2.0) ** 2
    )
    a = np.clip(a, 0.0, 1.0)
    return EARTH_RADIUS_M * 2.0 * np.arctan2(np.sqrt(a), np.sqrt(1.0 - a))


def buffer_zone(
    points: pd.DataFrame,
    origin_lat: float,
    origin_lon: float,
    radius_m: float = DEFAULT_BUFFER_RADIUS_M,
    lat_col: str = "latitude",
    lon_col: str = "longitude",
) -> pd.DataFrame:
    """Rows of ``points`` whose coordinates fall within ``radius_m`` of the origin.

    A degree bounding box drops far-away rows first, then haversine distance
    decides membership. The returned frame is a copy and includes ``distance_m``.
    """
    empty = points.iloc[0:0].copy()
    empty["distance_m"] = pd.Series(dtype="float64")
    if points.empty or radius_m < 0:
        return empty

    lat = points[lat_col].to_numpy(dtype=np.float64, copy=False)
    lon = points[lon_col].to_numpy(dtype=np.float64, copy=False)
    lat_pad = (radius_m * 1.01) / _LAT_M_PER_DEG
    lon_scale = max(np.cos(np.radians(origin_lat)), 0.2)
    lon_pad = (radius_m * 1.01) / (_LON_M_PER_DEG * lon_scale)
    in_box = (
        (lat >= origin_lat - lat_pad)
        & (lat <= origin_lat + lat_pad)
        & (lon >= origin_lon - lon_pad)
        & (lon <= origin_lon + lon_pad)
    )
    if not in_box.any():
        return empty

    nearby = points.iloc[np.flatnonzero(in_box)].copy()
    distances = haversine_m(
        origin_lat,
        origin_lon,
        nearby[lat_col].to_numpy(dtype=np.float64, copy=False),
        nearby[lon_col].to_numpy(dtype=np.float64, copy=False),
    )
    nearby["distance_m"] = distances
    within = nearby["distance_m"].to_numpy() <= radius_m
    return nearby.iloc[np.flatnonzero(within)].copy()


def nearest_row(
    points: pd.DataFrame,
    origin_lat: float,
    origin_lon: float,
    lat_col: str = "latitude",
    lon_col: str = "longitude",
) -> tuple[pd.Series, float] | None:
    """Closest row to the origin, or None when ``points`` is empty."""
    if points.empty:
        return None
    distances = haversine_m(
        origin_lat,
        origin_lon,
        points[lat_col].to_numpy(dtype=np.float64, copy=False),
        points[lon_col].to_numpy(dtype=np.float64, copy=False),
    )
    position = int(np.argmin(distances))
    return points.iloc[position], float(distances[position])
