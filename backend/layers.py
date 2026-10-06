"""Map markers and the fire, hospital, police, and transit layers."""

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

from backend.briefing import page_copy, reports_through_label
from backend.datasets import NIBRS_CUTOFF, REPO_ROOT, DatasetNotFound, _layer_sources, _text, load_summary
from backend.heatmap import _venue_mask
from backend.venues import list_venues

_layers: dict | None = None
_layers_stamp: tuple[float, ...] | None = None

MAP_BOUNDS = {
    "south": 33.70,
    "north": 34.34,
    "west": -118.68,
    "east": -118.10,
}
MAP_LABELS = [
    {"name": "San Fernando Valley", "latitude": 34.24, "longitude": -118.45},
    {"name": "Downtown", "latitude": 34.045, "longitude": -118.155},
    {"name": "Port of LA", "latitude": 33.718, "longitude": -118.18},
    {"name": "Pacific", "latitude": 33.97, "longitude": -118.62},
]


def map_payload() -> dict:
    """Every venue on one Los Angeles frame for the interactive map."""
    markers = []
    for venue in list_venues():
        markers.append(
            {
                "venue_id": venue["venue_id"],
                "venue_name": venue["venue_name"],
                "olympic_zone": venue["olympic_zone"],
                "latitude": venue["latitude"],
                "longitude": venue["longitude"],
                "crime_count_nearby": venue["crime_count_nearby"],
                "crime_per_km2": venue["crime_per_km2"],
                "lapd_jurisdiction": venue["lapd_jurisdiction"],
            }
        )
    return {
        "placeholder": False,
        "note": "Venue pins share one color. Crime density is the green-to-red heatmap.",
        "bounds": MAP_BOUNDS,
        "labels": MAP_LABELS,
        "markers": markers,
    }

def _location(*parts) -> str:
    return ", ".join(part for part in (_text(part) for part in parts) if part)

def _place(
    row_id: str,
    name: str,
    latitude: float,
    longitude: float,
    views: list[str],
    location: str = "",
    detail: str = "",
) -> dict:
    point = {
        "id": row_id,
        "name": name or "Unnamed",
        "latitude": round(float(latitude), 6),
        "longitude": round(float(longitude), 6),
        "views": views,
    }
    if location:
        point["location"] = location
    if detail:
        point["detail"] = detail
    return point

def _options(entries: list[tuple[str, str, str | None]], present: set[str]) -> list[dict]:
    options = []
    for option_id, label, group in entries:
        if option_id not in present:
            continue
        options.append({"id": option_id, "label": label, "group": group})
    return options

def map_layers() -> dict:
    """Citywide facility points for map toggles. Crime stays on the venue route."""
    global _layers, _layers_stamp
    sources = _layer_sources()
    missing = [path.name for path in sources.values() if not path.exists()]
    if missing:
        names = ", ".join(missing)
        raise DatasetNotFound(f"Missing map layer file(s): {names}")
    stamp = tuple(path.stat().st_mtime for path in sources.values())
    if _layers is not None and stamp == _layers_stamp:
        return _layers

    fire, _report = load_fire_stations(sources["fire"])
    hospitals, _report = load_hospitals(sources["hospitals"])
    police, _report = load_police_stations(sources["police"])
    rail, _report = load_rail_stations(sources["rail"])
    bus, _report = load_bus_stops(sources["bus"])
    def near(frame: pd.DataFrame) -> list[bool]:
        if frame.empty:
            return []
        mask = _venue_mask(frame["latitude"], frame["longitude"])
        return [bool(value) for value in mask.to_numpy()]

    def pack(points: list[dict], entries: list[tuple[str, str, str | None]]) -> dict:
        present = {view for point in points for view in point["views"]}
        return {"count": len(points), "options": _options(entries, present), "points": points}

    fire_near = near(fire)
    fire_points = []
    for index, row in enumerate(fire.itertuples(index=False)):
        views = ["all"]
        if fire_near[index]:
            views.append("venues")
        fire_points.append(
            _place(
                _text(row.station_id),
                _text(row.station_name) or "Fire station",
                row.latitude,
                row.longitude,
                views,
                _location(getattr(row, "address", ""), getattr(row, "zip_code", "")),
            )
        )

    hospital_near = near(hospitals)
    hospital_points = []
    for index, row in enumerate(hospitals.itertuples(index=False)):
        emergency = _text(getattr(row, "emergency_room", "")).lower() in {"yes", "y", "true", "1"}
        views = ["all"]
        if emergency:
            views.append("er")
        if hospital_near[index]:
            views.append("venues")
        hospital_points.append(
            _place(
                _text(row.station_id),
                _text(row.station_name) or "Hospital",
                row.latitude,
                row.longitude,
                views,
                _location(getattr(row, "address", ""), getattr(row, "city", ""), getattr(row, "zip_code", "")),
                "Emergency room" if emergency else "No emergency room",
            )
        )

    police_near = near(police)
    police_points = []
    for index, row in enumerate(police.itertuples(index=False)):
        agency = _text(row.agency)
        if is_lapd_agency(agency):
            kind = "lapd"
        elif "sheriff" in agency.casefold():
            kind = "sheriff"
        else:
            kind = "other"
        views = ["all", kind]
        if police_near[index]:
            views.append("venues")
        police_points.append(
            _place(
                _text(row.station_id),
                _text(row.station_name) or "Police station",
                row.latitude,
                row.longitude,
                views,
                _location(getattr(row, "address", ""), getattr(row, "city", ""), getattr(row, "zip_code", "")),
                agency,
            )
        )

    rail_lines: set[str] = set()
    rail_near = near(rail)
    rail_points = []
    for index, row in enumerate(rail.itertuples(index=False)):
        lines = [part.strip() for part in _text(row.lines).replace(",", ";").split(";") if part.strip()]
        rail_lines.update(lines)
        views = ["all", *[f"line:{line}" for line in lines]]
        if rail_near[index]:
            views.append("venues")
        mode = _text(getattr(row, "rail_mode", "")).replace("_", " ")
        line_text = f"Lines {', '.join(lines)}" if lines else ""
        detail = " · ".join(part for part in (line_text, mode) if part)
        rail_points.append(
            _place(
                _text(row.station_id),
                _text(row.station_name) or "Rail station",
                row.latitude,
                row.longitude,
                views,
                detail=detail,
            )
        )

    bus_stops = (
        bus.groupby("stop_id", as_index=False)
        .agg(
            station_name=("station_name", "first") if "station_name" in bus.columns else ("bus_line", "first"),
            latitude=("latitude", "first"),
            longitude=("longitude", "first"),
            lines=("bus_line", lambda values: ", ".join(sorted({_text(value) for value in values if _text(value)}))),
            rapid=("rapid_service", lambda values: any(_text(value).lower() in {"yes", "y", "true", "1"} or "rapid" in _text(value).lower() for value in values))
            if "rapid_service" in bus.columns
            else ("bus_line", lambda values: False),
        )
    )
    bus_near = near(bus_stops)
    bus_points = []
    for index, row in enumerate(bus_stops.itertuples(index=False)):
        rapid = bool(row.rapid)
        views = ["all"]
        if rapid:
            views.append("rapid")
        if bus_near[index]:
            views.append("venues")
        bus_points.append(
            _place(
                _text(row.stop_id),
                _text(row.station_name) or "Bus stop",
                row.latitude,
                row.longitude,
                views,
                detail=" · ".join(part for part in (f"Lines {row.lines}" if row.lines else "", "Rapid" if rapid else "") if part),
            )
        )

    _layers = {
        "fire": pack(
            fire_points,
            [("all", "All stations", None), ("venues", "Around venues", None)],
        ),
        "hospitals": pack(
            hospital_points,
            [
                ("all", "All hospitals", None),
                ("er", "Emergency rooms", None),
                ("venues", "Around venues", None),
            ],
        ),
        "police": pack(
            police_points,
            [
                ("all", "All stations", None),
                ("lapd", "LAPD", None),
                ("sheriff", "Sheriff", None),
                ("other", "Other cities", None),
                ("venues", "Around venues", None),
            ],
        ),
        "rail": pack(
            rail_points,
            [("all", "All stations", None), ("venues", "Around venues", None)]
            + [(f"line:{line}", f"Line {line}", "Line") for line in sorted(rail_lines)],
        ),
        "bus": pack(
            bus_points,
            [
                ("all", "All stops", None),
                ("venues", "Around venues", None),
                ("rapid", "Rapid", None),
            ],
        ),
    }
    _layers_stamp = stamp
    return _layers

def meta() -> dict:
    summary = load_summary()
    venues = summary["venues"]
    densest = max(venues, key=lambda venue: venue["crime_per_km2"])
    quietest = min(venues, key=lambda venue: venue["crime_per_km2"])

    def highlight(venue: dict) -> dict:
        return {
            "venue_id": venue["venue_id"],
            "venue_name": venue["venue_name"],
            "crime_count_nearby": venue["crime_count_nearby"],
            "crime_per_km2": venue["crime_per_km2"],
        }

    radius = float(summary["meta"].get("buffer_radius_m") or 0)
    count = int(summary["meta"].get("venue_count") or len(venues))
    return {
        **summary["meta"],
        "densest_venue": highlight(densest),
        "quietest_venue": highlight(quietest),
        "copy": page_copy(radius, count, reports_through_label(NIBRS_CUTOFF)),
    }

