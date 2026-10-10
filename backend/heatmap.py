"""Citywide crime heatmap.

Cell weights come from a second in-memory DuckDB, separate from the chat
warehouse. Reports before March 7, 2024 stay on the LAPD extract. Later
months come from NIBRS.
"""

from __future__ import annotations

import csv
import json
import math
import re

import numpy as np
import pandas as pd

from pipeline.geo import haversine_m, nearest_row
from pipeline.city_baseline import compared_with_city
from pipeline.crime_groups import GROUP_LABELS
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
    load_bus_stops,
    load_fire_stations,
    load_hospitals,
    load_police_stations,
    load_rail_stations,
    is_lapd_agency,
)

from backend.datasets import DatasetNotFound, load_merged, load_summary
from backend.heat_warehouse import grid_stamp, nibrs_bundle, report_bundle

_crime_views: dict | None = None
_crime_months: list[str] = []
_crime_monthly: dict | None = None
_crime_grids: dict | None = None
_crime_heat_mtime: tuple | None = None
_nibrs_monthly: dict | None = None
_nibrs_monthly_mtime: tuple | None = None
_MONTH_RE = re.compile(r"^20\d{2}-(0[1-9]|1[0-2])$")


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
    """Assemble map views from the DuckDB grids. The scan lives in heat_warehouse."""
    global _crime_months, _crime_monthly, _crime_grids
    bundle = report_bundle()
    all_counts = bundle["all"]
    venue_counts = bundle["venues"]
    if len(all_counts):
        hot_cutoff = max(int(all_counts.quantile(0.9)), 1)
        high_counts = all_counts[all_counts >= hot_cutoff]
    else:
        high_counts = all_counts

    views: dict[str, dict] = {
        "all": {
            "label": "All crime",
            "group": None,
            "hot": False,
            "incident_count": int(all_counts.sum()) if len(all_counts) else 0,
            "points": _grid_points(all_counts),
        },
        "high": {
            "label": "High amount (red)",
            "group": None,
            "hot": True,
            "incident_count": int(high_counts.sum()) if len(high_counts) else 0,
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
    for type_id, label in GROUP_LABELS.items():
        if type_id == "other":
            continue
        counts = bundle["types"].get(type_id)
        if counts is None or len(counts) == 0:
            continue
        views[f"type:{type_id}"] = {
            "label": label,
            "group": "Type of crime",
            "hot": False,
            "incident_count": int(counts.sum()),
            "points": _grid_points(counts),
        }
    _crime_months = list(bundle["months"])
    _crime_monthly = bundle["monthly"]
    _crime_grids = {
        "all": all_counts,
        "venues": venue_counts,
        "types": bundle["types"],
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
    try:
        stamp = grid_stamp()
    except FileNotFoundError:
        return {"months": [], "counts": pd.Series(dtype="int64")}
    if _nibrs_monthly is not None and stamp == _nibrs_monthly_mtime:
        return _nibrs_monthly
    _nibrs_monthly = nibrs_bundle()
    _nibrs_monthly_mtime = stamp
    return _nibrs_monthly


def _drop_month(counts: pd.Series | None) -> pd.Series:
    if counts is None or len(counts) == 0:
        return pd.Series(dtype="int64")
    return counts.groupby(level=[1, 2]).sum()


def _add_grids(left: pd.Series | None, right: pd.Series | None) -> pd.Series:
    if left is None or len(left) == 0:
        return right if right is not None else pd.Series(dtype="int64")
    if right is None or len(right) == 0:
        return left
    return left.add(right, fill_value=0).astype("int64")


def _high_from(counts: pd.Series | None) -> pd.Series:
    if counts is None or len(counts) == 0:
        return pd.Series(dtype="int64")
    cutoff = max(int(counts.quantile(0.9)), 1)
    return counts[counts >= cutoff]


def _report_months(view: str) -> list[str]:
    if _crime_monthly is None:
        return []
    if view in {"all", "high"}:
        return list(_crime_months)
    if view == "venues":
        series = _crime_monthly["venues"]
    elif view.startswith("type:"):
        series = _crime_monthly["types"].get(view.split(":", 1)[1])
    else:
        return list(_crime_months)
    if series is None or len(series) == 0:
        return []
    return sorted(series.index.get_level_values(0).unique())


def _nibrs_months_for(view: str) -> list[str]:
    index = _nibrs_month_index()
    if view in {"all", "high"}:
        return list(index["months"])
    if view == "venues":
        series = index.get("venues")
    elif view.startswith("type:"):
        series = (index.get("types") or {}).get(view.split(":", 1)[1])
    else:
        return []
    if series is None or len(series) == 0:
        return []
    return sorted(series.index.get_level_values(0).unique())


def _nibrs_grid(view: str) -> pd.Series:
    index = _nibrs_month_index()
    if view in {"all", "high"}:
        return _drop_month(index.get("counts"))
    if view == "venues":
        return _drop_month(index.get("venues"))
    if view.startswith("type:"):
        return _drop_month((index.get("types") or {}).get(view.split(":", 1)[1]))
    return pd.Series(dtype="int64")


def _series_or_empty(series: pd.Series | None) -> pd.Series:
    if series is None or len(series) == 0:
        return pd.Series(dtype="int64")
    return series


def _report_grid(view: str) -> pd.Series:
    grids = _crime_grids or {}
    if view in {"all", "high"}:
        return _series_or_empty(grids.get("all"))
    if view == "venues":
        return _series_or_empty(grids.get("venues"))
    if view.startswith("type:"):
        return _series_or_empty((grids.get("types") or {}).get(view.split(":", 1)[1]))
    return pd.Series(dtype="int64")


def _report_monthly(view: str) -> pd.Series | None:
    if _crime_monthly is None:
        return None
    if view in {"all", "high"}:
        return _crime_monthly["all"]
    if view == "venues":
        return _crime_monthly["venues"]
    if view.startswith("type:"):
        return _crime_monthly["types"].get(view.split(":", 1)[1])
    return None


def _nibrs_monthly_series(view: str) -> pd.Series | None:
    index = _nibrs_month_index()
    if view in {"all", "high"}:
        return index.get("counts")
    if view == "venues":
        return index.get("venues")
    if view.startswith("type:"):
        return (index.get("types") or {}).get(view.split(":", 1)[1])
    return None


def _full_blend(view: str) -> pd.Series:
    blended = _add_grids(_report_grid(view), _nibrs_grid(view))
    if view == "high":
        return _high_from(blended)
    return blended


def _range_counts(view: str, start: str, end: str) -> pd.Series | None:
    """Cell weights inside the month span. None means use the full blended view."""
    if view == "nibrs":
        index = _nibrs_month_index()
        months = index["months"]
        if not months or (start <= months[0] and end >= months[-1]):
            return None
        return _sum_range(index["counts"], start, end)
    months = _months_for(view)
    if not months or (start <= months[0] and end >= months[-1]):
        return None
    blended = _add_grids(
        _sum_range(_report_monthly(view), start, end),
        _sum_range(_nibrs_monthly_series(view), start, end),
    )
    if view == "high":
        return _high_from(blended)
    return blended


def _months_for(view: str) -> list[str]:
    if view == "nibrs":
        return list(_nibrs_month_index()["months"])
    return sorted(set(_report_months(view)) | set(_nibrs_months_for(view)))


def crime_heat_points(view: str = "all", start: str | None = None, end: str | None = None) -> dict:
    """One city heatmap.

    ``all`` blends LAPD reports before March 7, 2024 with NIBRS offenses after
    that, through the latest NIBRS month. ``high`` keeps the busiest cells.
    ``venues`` keeps records inside a venue buffer. ``type:<id>`` keeps one
    crime type. ``nibrs`` is the offense extract on its own. ``start`` and
    ``end`` are YYYY-MM bounds. A span inside the series keeps only those
    months. Coordinates are rounded to about 11 m so nearby reports share a
    weight.
    """
    global _crime_views, _crime_heat_mtime
    start = _check_month(start, "start")
    end = _check_month(end, "end")
    try:
        stamp = grid_stamp()
    except FileNotFoundError as exc:
        raise DatasetNotFound(str(exc)) from exc
    if _crime_views is None or stamp != _crime_heat_mtime:
        _crime_views = _build_crime_views()
        _crime_heat_mtime = stamp
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
    if counts is None and view != "nibrs":
        counts = _full_blend(view)
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

