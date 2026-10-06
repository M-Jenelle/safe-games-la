"""Citywide crime heatmap built from the LAPD extract."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from pipeline.geo import haversine_m, nearest_row
from pipeline.city_baseline import compared_with_city
from pipeline.crime_groups import GROUP_LABELS, crime_group
from pipeline.permit_event_days import (
    GROUP_IDS,
    MATERIAL_DAILY_DIFFERENCE,
    MIN_EVENT_DAYS_FOR_PERCENT,
    OTHER_DAY_MEAN_FOR_PERCENT,
    collapse_upcoming,
    comparison_view,
)
from backend.briefing import (
    PERMIT_INTRO,
    PERMIT_LOAD_INTRO,
    PERMIT_NOT_MLB_NOTE,
    annotate_weekday,
    display_for_venue,
    group_gap_empty,
    group_gap_note,
    page_copy,
    reports_through_label,
)
from pipeline.weekday import combine_weekday, weekday_from_dates
from pipeline.loaders import (
    LAT_MAX,
    LAT_MIN,
    LON_MAX,
    LON_MIN,
    load_bus_stops,
    load_fire_stations,
    load_hospitals,
    load_police_stations,
    load_rail_stations,
    is_lapd_agency,
)
from pipeline.run import find_crime_csv, resolve_data_dir

from backend.datasets import REPO_ROOT, DatasetNotFound, load_merged, load_summary

_crime_views: dict | None = None
_crime_heat_mtime: float | None = None


def _crime_source_path() -> Path:
    data_dir = resolve_data_dir(REPO_ROOT)
    try:
        return find_crime_csv(data_dir)
    except FileNotFoundError:
        fallback = REPO_ROOT / "data"
        if fallback == data_dir:
            raise
        return find_crime_csv(fallback)

def _crime_type_id(description: str) -> str | None:
    group = crime_group(description)
    return None if group == "other" else group

def _grid_points(counts: pd.Series) -> list[dict]:
    return [
        {"latitude": float(lat), "longitude": float(lon), "weight": int(weight)}
        for (lat, lon), weight in counts.items()
    ]

def _venue_mask(latitude: pd.Series, longitude: pd.Series) -> pd.Series:
    """True where a report is inside any venue's buffer."""
    try:
        summary = load_summary()
    except DatasetNotFound:
        return pd.Series(False, index=latitude.index)
    radius = float(summary.get("buffer_radius_m") or 800)
    origins = [
        (float(venue["latitude"]), float(venue["longitude"]))
        for venue in summary["venues"]
    ]
    if not origins:
        return pd.Series(False, index=latitude.index)
    lat_arr = latitude.to_numpy(dtype=np.float64)
    lon_arr = longitude.to_numpy(dtype=np.float64)
    near = np.zeros(len(lat_arr), dtype=bool)
    for origin_lat, origin_lon in origins:
        near |= haversine_m(origin_lat, origin_lon, lat_arr, lon_arr) <= radius
    return pd.Series(near, index=latitude.index)

def _build_crime_views() -> dict[str, dict]:
    path = _crime_source_path()
    raw = pd.read_csv(path, usecols=["LAT", "LON", "Crm Cd Desc"], low_memory=False)
    latitude = pd.to_numeric(raw["LAT"], errors="coerce")
    longitude = pd.to_numeric(raw["LON"], errors="coerce")
    valid = latitude.between(LAT_MIN, LAT_MAX) & longitude.between(LON_MIN, LON_MAX)
    descriptions = raw.loc[valid, "Crm Cd Desc"].fillna("").astype(str)
    type_by_label = {label: _crime_type_id(label) for label in descriptions.str.upper().unique()}
    frame = pd.DataFrame(
        {
            "latitude": latitude[valid].round(4),
            "longitude": longitude[valid].round(4),
            "crime_type": descriptions.str.upper().map(type_by_label).to_numpy(),
        }
    )
    all_counts = frame.groupby(["latitude", "longitude"]).size()
    hot_cutoff = max(int(all_counts.quantile(0.9)), 1)
    high_counts = all_counts[all_counts >= hot_cutoff]
    venue_rows = frame.loc[_venue_mask(latitude[valid], longitude[valid]).to_numpy()]
    venue_counts = venue_rows.groupby(["latitude", "longitude"]).size()

    views: dict[str, dict] = {
        "all": {
            "label": "All crime",
            "group": None,
            "hot": False,
            "incident_count": int(all_counts.sum()),
            "points": _grid_points(all_counts),
        },
        "high": {
            "label": "High amount (red)",
            "group": None,
            "hot": True,
            "incident_count": int(high_counts.sum()),
            "points": _grid_points(high_counts),
        },
        "venues": {
            "label": "Around venues",
            "group": None,
            "hot": False,
            "incident_count": int(venue_counts.sum()) if len(venue_counts) else 0,
            "points": _grid_points(venue_counts),
        },
    }
    typed = frame.dropna(subset=["crime_type"])
    if len(typed):
        type_counts = typed.groupby(["crime_type", "latitude", "longitude"]).size()
        for type_id, label in GROUP_LABELS.items():
            if type_id == "other":
                continue
            if type_id not in type_counts.index.get_level_values(0):
                continue
            counts = type_counts.xs(type_id)
            views[f"type:{type_id}"] = {
                "label": label,
                "group": "Type of crime",
                "hot": False,
                "incident_count": int(counts.sum()),
                "points": _grid_points(counts),
            }
    return views

def _nibrs_heat_view() -> dict | None:
    heat = (load_merged() or {}).get("nibrs_heat")
    if not heat:
        return None
    return {
        "label": "NIBRS offenses (Mar 2024–present)",
        "group": None,
        "hot": False,
        "incident_count": heat.get("incident_count") or 0,
        "points": heat.get("points") or [],
    }

def _views_with_nibrs(views: dict) -> dict:
    nibrs = _nibrs_heat_view()
    if not nibrs:
        return views
    ordered: dict = {}
    inserted = False
    for view_id, payload in views.items():
        ordered[view_id] = payload
        if view_id == "venues":
            ordered["nibrs"] = nibrs
            inserted = True
    if not inserted:
        ordered["nibrs"] = nibrs
    return ordered

def crime_heat_points(view: str = "all") -> dict:
    """One heatmap view of the 2020–2024 LAPD extract.

    ``all`` is the whole city. ``high`` keeps the busiest cells. ``venues``
    keeps reports inside a venue buffer. ``type:<id>`` keeps one crime type.
    Coordinates are rounded to about 11 m so nearby reports share a weight.
    """
    global _crime_views, _crime_heat_mtime
    try:
        path = _crime_source_path()
    except FileNotFoundError as exc:
        raise DatasetNotFound(str(exc)) from exc
    mtime = path.stat().st_mtime
    if _crime_views is None or mtime != _crime_heat_mtime:
        _crime_views = _build_crime_views()
        _crime_heat_mtime = mtime
    views = _views_with_nibrs(_crime_views)
    if view not in views:
        known = ", ".join(views)
        raise ValueError(f"Unknown crime view '{view}'. Known views: {known}")
    selected = views[view]
    options = [
        {"id": view_id, "label": payload["label"], "group": payload["group"]}
        for view_id, payload in views.items()
    ]
    return {
        "view": view,
        "hot": selected["hot"],
        "incident_count": selected["incident_count"],
        "location_count": len(selected["points"]),
        "options": options,
        "points": selected["points"],
    }

