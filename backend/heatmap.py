"""Citywide crime heatmap built from the LAPD extract."""

from __future__ import annotations

import csv
import json
import math
import re
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
_crime_months: list[str] = []
_crime_monthly: dict | None = None
_crime_heat_mtime: float | None = None
_nibrs_monthly: dict | None = None
_nibrs_monthly_mtime: float | None = None
_MONTH_RE = re.compile(r"^20\d{2}-(0[1-9]|1[0-2])$")


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

def _parse_occurred(values: pd.Series) -> pd.Series:
    text = values.astype("string")
    occurred = pd.to_datetime(text, format="%m/%d/%Y %I:%M:%S %p", errors="coerce")
    missing = occurred.isna() & text.fillna("").str.strip().ne("")
    if missing.any():
        occurred.loc[missing] = pd.to_datetime(text.loc[missing], errors="coerce")
    return occurred


def _sum_range(counts: pd.Series, start: str, end: str) -> pd.Series:
    if counts is None or len(counts) == 0:
        return pd.Series(dtype="int64")
    months = counts.index.get_level_values(0)
    chosen = counts[(months >= start) & (months <= end)]
    if chosen.empty:
        return chosen
    return chosen.groupby(level=[1, 2]).sum()


def _check_month(value: str | None, name: str) -> str | None:
    if value is None or value == "":
        return None
    if not _MONTH_RE.match(value):
        raise ValueError(f"{name} must be YYYY-MM")
    return value


def _build_crime_views() -> dict[str, dict]:
    global _crime_months, _crime_monthly
    path = _crime_source_path()
    raw = pd.read_csv(path, usecols=["LAT", "LON", "DATE OCC", "Crm Cd Desc"], low_memory=False)
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
            "month": _parse_occurred(raw.loc[valid, "DATE OCC"]).dt.strftime("%Y-%m").to_numpy(),
            "near": _venue_mask(latitude[valid], longitude[valid]).to_numpy(),
        }
    )
    all_counts = frame.groupby(["latitude", "longitude"]).size()
    hot_cutoff = max(int(all_counts.quantile(0.9)), 1)
    high_counts = all_counts[all_counts >= hot_cutoff]
    venue_rows = frame.loc[frame["near"].to_numpy()]
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
    dated = frame.loc[frame["month"].astype("string").str.match(_MONTH_RE.pattern, na=False)]
    _crime_months = sorted(dated["month"].unique())
    type_monthly: dict[str, pd.Series] = {}
    typed_dated = dated.dropna(subset=["crime_type"])
    if len(typed_dated):
        grouped = typed_dated.groupby(["crime_type", "month", "latitude", "longitude"]).size()
        for type_id in grouped.index.get_level_values(0).unique():
            type_monthly[str(type_id)] = grouped.xs(type_id)
    venue_dated = dated.loc[dated["near"].to_numpy()]
    _crime_monthly = {
        "all": dated.groupby(["month", "latitude", "longitude"]).size(),
        "venues": (
            venue_dated.groupby(["month", "latitude", "longitude"]).size()
            if len(venue_dated)
            else pd.Series(dtype="int64")
        ),
        "types": type_monthly,
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

def _nibrs_month_index() -> dict:
    global _nibrs_monthly, _nibrs_monthly_mtime
    from pipeline.merge_crime import NIBRS_PATH, _load_nibrs

    if not NIBRS_PATH.exists():
        return {"months": [], "counts": pd.Series(dtype="int64")}
    mtime = NIBRS_PATH.stat().st_mtime
    if _nibrs_monthly is not None and mtime == _nibrs_monthly_mtime:
        return _nibrs_monthly
    frame = _load_nibrs(NIBRS_PATH)
    if frame.empty:
        cache = {"months": [], "counts": pd.Series(dtype="int64")}
    else:
        occurred = pd.to_datetime(frame["occurred_at"], errors="coerce")
        kept = frame.loc[occurred.notna()].copy()
        kept["month"] = occurred.loc[kept.index].dt.strftime("%Y-%m")
        kept["latitude"] = pd.to_numeric(kept["latitude"], errors="coerce").round(4)
        kept["longitude"] = pd.to_numeric(kept["longitude"], errors="coerce").round(4)
        counts = kept.groupby(["month", "latitude", "longitude"]).size()
        cache = {
            "months": sorted(counts.index.get_level_values(0).unique()),
            "counts": counts,
        }
    _nibrs_monthly = cache
    _nibrs_monthly_mtime = mtime
    return cache


def _range_counts(view: str, start: str, end: str) -> pd.Series | None:
    """Cell weights inside the month span. None means use the full cached view."""
    if view == "nibrs":
        index = _nibrs_month_index()
        months = index["months"]
        if not months or (start <= months[0] and end >= months[-1]):
            return None
        return _sum_range(index["counts"], start, end)
    months = _crime_months
    if not months or _crime_monthly is None or (start <= months[0] and end >= months[-1]):
        return None
    if view == "high":
        counts = _sum_range(_crime_monthly["all"], start, end)
        if counts.empty:
            return counts
        cutoff = max(int(counts.quantile(0.9)), 1)
        return counts[counts >= cutoff]
    if view == "all":
        return _sum_range(_crime_monthly["all"], start, end)
    if view == "venues":
        return _sum_range(_crime_monthly["venues"], start, end)
    if view.startswith("type:"):
        series = _crime_monthly["types"].get(view.split(":", 1)[1])
        if series is None:
            return pd.Series(dtype="int64")
        return _sum_range(series, start, end)
    return None


def _months_for(view: str) -> list[str]:
    if view == "nibrs":
        return list(_nibrs_month_index()["months"])
    return list(_crime_months)


def crime_heat_points(view: str = "all", start: str | None = None, end: str | None = None) -> dict:
    """One heatmap view of the 2020–2024 LAPD extract.

    ``all`` is the whole city. ``high`` keeps the busiest cells. ``venues``
    keeps reports inside a venue buffer. ``type:<id>`` keeps one crime type.
    ``start`` and ``end`` are YYYY-MM bounds. A span inside the series keeps
    only those months. Coordinates are rounded to about 11 m so nearby reports
    share a weight.
    """
    global _crime_views, _crime_heat_mtime
    start = _check_month(start, "start")
    end = _check_month(end, "end")
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
    months = _months_for(view)
    if months:
        if start is None:
            start = months[0]
        if end is None:
            end = months[-1]
        if start > end:
            start, end = end, start
        if start < months[0]:
            start = months[0]
        if end > months[-1]:
            end = months[-1]
        if start > end:
            start = end = months[0]
    selected = views[view]
    counts = _range_counts(view, start, end) if start and end else None
    if counts is not None:
        selected = {
            "label": selected["label"],
            "group": selected["group"],
            "hot": view == "high",
            "incident_count": int(counts.sum()) if len(counts) else 0,
            "points": _grid_points(counts) if len(counts) else [],
        }
    options = [
        {"id": view_id, "label": payload["label"], "group": payload["group"]}
        for view_id, payload in views.items()
    ]
    return {
        "view": view,
        "hot": selected["hot"],
        "incident_count": selected["incident_count"],
        "location_count": len(selected["points"]),
        "months": months,
        "start": start,
        "end": end,
        "options": options,
        "points": selected["points"],
    }

