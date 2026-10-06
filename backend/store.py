"""Read pipeline JSON, reloading when the files change on disk."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
import pandas as pd

from pipeline.geo import haversine_m
from pipeline.permit_event_days import GROUP_IDS, collapse_upcoming, comparison_view

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

REPO_ROOT = Path(__file__).resolve().parents[1]
SUMMARY_PATH = REPO_ROOT / "data" / "processed" / "venue_summary.json"
POINTS_PATH = REPO_ROOT / "data" / "processed" / "crime_points_by_venue.json"
MERGED_PATH = REPO_ROOT / "data" / "processed" / "crime_merged.json"
PERMIT_DAYS_PATH = REPO_ROOT / "data" / "processed" / "permit_event_days.csv"
PERMIT_LIFT_PATH = REPO_ROOT / "data" / "processed" / "permit_event_lift.json"
TIME_PATH = REPO_ROOT / "data" / "processed" / "crime_time_of_day.json"
DISTANCE_PATH = REPO_ROOT / "data" / "processed" / "crime_by_distance.json"
LISTINGS_PATH = REPO_ROOT / "data" / "processed" / "ticketmaster_listings.json"
NIBRS_CHARTS_PATH = REPO_ROOT / "data" / "processed" / "nibrs_charts.json"

_summary: dict | None = None
_summary_mtime: float | None = None
_points: dict | None = None
_points_mtime: float | None = None
_month_categories: dict[str, dict[str, dict[str, int]]] = {}
_month_categories_mtime: float | None = None
_crime_views: dict | None = None
_crime_heat_mtime: float | None = None
_merged: dict | None = None
_merged_mtime: float | None = None
_permit_days: dict[str, list[dict]] | None = None
_permit_days_mtime: float | None = None
_permit_upcoming: dict[str, list[dict]] | None = None
_permit_upcoming_mtime: float | None = None
_crime_time: dict | None = None
_crime_time_mtime: float | None = None
_crime_distance: dict | None = None
_crime_distance_mtime: float | None = None
_listings: dict | None = None
_listings_mtime: float | None = None
_nibrs_charts: dict | None = None
_nibrs_charts_mtime: float | None = None

# First matching rule wins. Descriptions are the LAPD "Crm Cd Desc" text.
CRIME_TYPES = (
    ("sexual", "Sexual offenses", ("RAPE", "SEXUAL", "LEWD", "INDECENT")),
    ("homicide", "Homicide", ("HOMICIDE", "MANSLAUGHTER")),
    ("robbery", "Robbery", ("ROBBERY",)),
    ("assault", "Assault", ("ASSAULT", "BATTERY")),
    ("weapons", "Weapons", ("WEAPON", "FIREARM", "SHOTS FIRED")),
    ("vehicle", "Vehicle", ("VEHICLE", "MOTOR VEHICLE", "BIKE - STOLEN")),
    ("burglary", "Burglary", ("BURGLARY",)),
    ("theft", "Theft", ("THEFT", "SHOPLIFTING", "PICKPOCKET", "BUNCO", "STOLEN")),
    ("vandalism", "Vandalism", ("VANDALISM",)),
)
_layers: dict | None = None
_layers_stamp: tuple[float, ...] | None = None


class DatasetNotFound(FileNotFoundError):
    """Raised when a pipeline output the API needs is missing."""


def _read_json(path: Path) -> tuple[dict, float]:
    if not path.exists():
        raise DatasetNotFound(
            f"Missing {path.relative_to(REPO_ROOT)}. Run `python -m pipeline.run` first."
        )
    mtime = path.stat().st_mtime
    return json.loads(path.read_text(encoding="utf-8")), mtime


def load_summary() -> dict:
    global _summary, _summary_mtime
    if not SUMMARY_PATH.exists():
        raise DatasetNotFound(
            f"Missing {SUMMARY_PATH.relative_to(REPO_ROOT)}. Run `python -m pipeline.run` first."
        )
    mtime = SUMMARY_PATH.stat().st_mtime
    if _summary is None or mtime != _summary_mtime:
        _summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
        _summary_mtime = mtime
    return _summary


def load_crime_points() -> dict:
    global _points, _points_mtime
    if not POINTS_PATH.exists():
        raise DatasetNotFound(
            f"Missing {POINTS_PATH.relative_to(REPO_ROOT)}. Run `python -m pipeline.run` first."
        )
    mtime = POINTS_PATH.stat().st_mtime
    if _points is None or mtime != _points_mtime:
        _points, _points_mtime = _read_json(POINTS_PATH)
    return _points


def venue_card(venue: dict) -> dict:
    """Roster fields. The full briefing stays on the detail route."""
    police = venue.get("nearest_police_station") or {}
    hospital = venue.get("nearest_hospital") or {}
    return {
        "venue_id": venue["venue_id"],
        "venue_name": venue["venue_name"],
        "olympic_zone": venue["olympic_zone"],
        "sports": venue["sports"],
        "latitude": venue["latitude"],
        "longitude": venue["longitude"],
        "address": venue.get("address") or "",
        "crime_count_nearby": venue["crime_count_nearby"],
        "crime_per_km2": venue["crime_per_km2"],
        "lapd_jurisdiction": venue["lapd_jurisdiction"],
        "buffer_radius_m": venue["buffer_radius_m"],
        "data_quality_flags": venue["data_quality_flags"],
        "nearest_police_agency": police.get("agency"),
        "nearest_police_name": police.get("station_name"),
        "nearest_police_distance_m": police.get("distance_m"),
        "nearest_hospital_name": hospital.get("station_name"),
        "nearest_hospital_distance_m": hospital.get("distance_m"),
    }


def load_merged() -> dict | None:
    """Joined crime series, when ``crime_merged.json`` has been built."""
    global _merged, _merged_mtime
    if not MERGED_PATH.exists():
        _merged = None
        _merged_mtime = None
        return None
    mtime = MERGED_PATH.stat().st_mtime
    if _merged is None or mtime != _merged_mtime:
        _merged = json.loads(MERGED_PATH.read_text(encoding="utf-8"))
        _merged_mtime = mtime
    return _merged


def _merged_venue(venue_id: str) -> dict | None:
    merged = load_merged()
    if not merged:
        return None
    return (merged.get("venues") or {}).get(venue_id)


def list_venues() -> list[dict]:
    cards = []
    for venue in load_summary()["venues"]:
        card = venue_card(venue)
        block = _merged_venue(venue["venue_id"])
        if block is not None:
            card["nibrs_count"] = block.get("nibrs_count") or 0
        cards.append(card)
    return cards


def crime_categories_by_month(venue_id: str) -> dict[str, dict[str, int]]:
    """Incident-type counts for each YYYY-MM inside one venue buffer."""
    global _month_categories_mtime
    try:
        points = load_crime_points()
    except DatasetNotFound:
        return {}
    if _month_categories_mtime != _points_mtime:
        _month_categories.clear()
        _month_categories_mtime = _points_mtime
    cached = _month_categories.get(venue_id)
    if cached is not None:
        return cached
    block = points.get("by_venue", {}).get(venue_id) or {}
    counts: dict[str, dict[str, int]] = {}
    for point in block.get("points", []):
        date = point.get("date") or ""
        if len(date) < 7:
            continue
        month = date[:7]
        category = point.get("category") or "Unknown"
        bucket = counts.setdefault(month, {})
        bucket[category] = bucket.get(category, 0) + 1
    _month_categories[venue_id] = counts
    return counts


def get_venue(venue_id: str) -> dict | None:
    for venue in load_summary()["venues"]:
        if venue["venue_id"] == venue_id:
            enriched = dict(venue)
            enriched["crime_categories_by_month"] = crime_categories_by_month(venue_id)
            block = _merged_venue(venue_id)
            if block is not None:
                enriched["nibrs"] = {
                    "count": block.get("nibrs_count") or 0,
                    "by_month": block.get("nibrs_by_month") or {},
                    "by_category": block.get("nibrs_by_category") or {},
                    "categories_by_month": block.get("nibrs_categories_by_month") or {},
                }
                enriched["merged_by_month"] = block.get("merged_by_month") or {}
                groups = (load_merged() or {}).get("meta", {}).get("groups") or {}
                if groups:
                    enriched["crime_groups"] = groups
            time_block = crime_time_for(venue_id)
            if time_block:
                enriched["crime_time"] = time_block
            distance_block = crime_distance_for(venue_id)
            if distance_block:
                enriched["crime_distance"] = distance_block
            listings = ticketmaster_for(venue_id)
            if listings:
                enriched["ticketmaster"] = listings
            nibrs_charts = nibrs_charts_for(venue_id)
            if nibrs_charts:
                enriched["nibrs_time"] = nibrs_charts.get("time") or {}
                enriched["nibrs_distance"] = nibrs_charts.get("distance") or {}
            return enriched
    return None


def load_crime_time() -> dict | None:
    """Part-of-day counts. Empty when ``crime_time_of_day.json`` has not been built."""
    global _crime_time, _crime_time_mtime
    if not TIME_PATH.exists():
        _crime_time = None
        _crime_time_mtime = None
        return None
    mtime = TIME_PATH.stat().st_mtime
    if _crime_time is None or mtime != _crime_time_mtime:
        _crime_time = json.loads(TIME_PATH.read_text(encoding="utf-8"))
        _crime_time_mtime = mtime
    return _crime_time


def crime_time_for(venue_id: str) -> dict | None:
    payload = load_crime_time()
    if not payload:
        return None
    return (payload.get("venues") or {}).get(venue_id)


def load_crime_distance() -> dict | None:
    """Distance-band counts. Empty when ``crime_by_distance.json`` has not been built."""
    global _crime_distance, _crime_distance_mtime
    if not DISTANCE_PATH.exists():
        _crime_distance = None
        _crime_distance_mtime = None
        return None
    mtime = DISTANCE_PATH.stat().st_mtime
    if _crime_distance is None or mtime != _crime_distance_mtime:
        _crime_distance = json.loads(DISTANCE_PATH.read_text(encoding="utf-8"))
        _crime_distance_mtime = mtime
    return _crime_distance


def crime_distance_for(venue_id: str) -> dict | None:
    payload = load_crime_distance()
    if not payload:
        return None
    return (payload.get("venues") or {}).get(venue_id)


def load_ticketmaster() -> dict | None:
    """Future Ticketmaster listings. Empty when that file has not been built."""
    global _listings, _listings_mtime
    if not LISTINGS_PATH.exists():
        _listings = None
        _listings_mtime = None
        return None
    mtime = LISTINGS_PATH.stat().st_mtime
    if _listings is None or mtime != _listings_mtime:
        _listings = json.loads(LISTINGS_PATH.read_text(encoding="utf-8"))
        _listings_mtime = mtime
    return _listings


def ticketmaster_for(venue_id: str) -> dict | None:
    payload = load_ticketmaster()
    if not payload:
        return None
    return (payload.get("venues") or {}).get(venue_id)


def load_nibrs_charts() -> dict | None:
    """NIBRS time, distance, and permit-day rows. Empty when that file is absent."""
    global _nibrs_charts, _nibrs_charts_mtime
    if not NIBRS_CHARTS_PATH.exists():
        _nibrs_charts = None
        _nibrs_charts_mtime = None
        return None
    mtime = NIBRS_CHARTS_PATH.stat().st_mtime
    if _nibrs_charts is None or mtime != _nibrs_charts_mtime:
        _nibrs_charts = json.loads(NIBRS_CHARTS_PATH.read_text(encoding="utf-8"))
        _nibrs_charts_mtime = mtime
    return _nibrs_charts


def nibrs_charts_for(venue_id: str) -> dict | None:
    payload = load_nibrs_charts()
    if not payload:
        return None
    return (payload.get("venues") or {}).get(venue_id)


def get_crime_points(venue_id: str) -> dict | None:
    block = load_crime_points().get("by_venue", {}).get(venue_id)
    if block is None:
        return None
    return block


def load_permit_day_rows() -> dict[str, list[dict]]:
    """Venue-day rows from the permit merge. Empty when that file is absent."""
    global _permit_days, _permit_days_mtime
    if not PERMIT_DAYS_PATH.exists():
        _permit_days = {}
        _permit_days_mtime = None
        return _permit_days
    mtime = PERMIT_DAYS_PATH.stat().st_mtime
    if _permit_days is not None and mtime == _permit_days_mtime:
        return _permit_days
    grouped: dict[str, list[dict]] = {}
    with PERMIT_DAYS_PATH.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            venue_id = row.get("venue_id") or ""
            if not venue_id:
                continue
            parsed = {
                "date": row.get("date") or "",
                "venue_name": row.get("venue_name") or "",
                "incident_count": int(row.get("incident_count") or 0),
                "is_permit_event_day": int(row.get("is_permit_event_day") or 0),
                "permit_count": int(row.get("permit_count") or 0),
            }
            for group_id in GROUP_IDS:
                parsed[group_id] = int(row.get(group_id) or 0)
            grouped.setdefault(venue_id, []).append(parsed)
    _permit_days = grouped
    _permit_days_mtime = mtime
    return _permit_days


def load_permit_upcoming() -> dict[str, list[dict]]:
    """Later permit dates from the lift file, collapsed into date spans."""
    global _permit_upcoming, _permit_upcoming_mtime
    if not PERMIT_LIFT_PATH.exists():
        _permit_upcoming = {}
        _permit_upcoming_mtime = None
        return _permit_upcoming
    mtime = PERMIT_LIFT_PATH.stat().st_mtime
    if _permit_upcoming is not None and mtime == _permit_upcoming_mtime:
        return _permit_upcoming
    payload = json.loads(PERMIT_LIFT_PATH.read_text(encoding="utf-8"))
    upcoming = {}
    for venue in payload.get("venues") or []:
        venue_id = venue.get("venue_id")
        if venue_id:
            upcoming[venue_id] = collapse_upcoming(venue.get("upcoming_permit_days") or [])
    _permit_upcoming = upcoming
    _permit_upcoming_mtime = mtime
    return _permit_upcoming


def permit_comparison(venue_id: str, year: str = "all", month: str = "all", source: str = "reports") -> dict | None:
    """Permit-day versus other-day means. None when the venue id is unknown."""
    match = next((venue for venue in load_summary()["venues"] if venue["venue_id"] == venue_id), None)
    if match is None:
        return None
    if source == "nibrs":
        charts = load_nibrs_charts() or {}
        block = (charts.get("venues") or {}).get(venue_id) or {}
        rows = block.get("permit_rows") or []
    else:
        rows = load_permit_day_rows().get(venue_id) or []
    if not rows:
        return {"available": False, "venue_id": venue_id, "venue_name": match["venue_name"], "source": source}
    note = None
    if venue_id == "V01":
        note = "These figures use permit days, not the MLB home-game list."
    venue_name = rows[0].get("venue_name") or match["venue_name"]
    view = comparison_view(rows, year=year, month=month, venue_id=venue_id, venue_name=venue_name, note=note)
    view["source"] = source
    if source == "nibrs":
        view["source_note"] = (load_nibrs_charts() or {}).get("permit_note") or ""
        view["upcoming"] = []
    else:
        view["upcoming"] = load_permit_upcoming().get(venue_id) or []
    return view


# Fixed frame for the citywide placeholder. Real tiles will replace this later.
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
    text = description.upper()
    for type_id, _label, needles in CRIME_TYPES:
        if any(needle in text for needle in needles):
            return type_id
    return None


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
        for type_id, label, _needles in CRIME_TYPES:
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


def _layer_sources() -> dict[str, Path]:
    data_dir = resolve_data_dir(REPO_ROOT)
    return {
        "fire": data_dir / "lafd_fire_stations.csv",
        "hospitals": data_dir / "hospitals_in_LA.csv",
        "police": data_dir / "la_county_police_stations.csv",
        "rail": data_dir / "la_metro_rail_stations.csv",
        "bus": data_dir / "la_metro_bus_stops.csv",
    }


def _text(value) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except TypeError:
        pass
    text = str(value).strip()
    if text.lower() in {"nan", "none"}:
        return ""
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text


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

    return {
        **summary["meta"],
        "densest_venue": highlight(densest),
        "quietest_venue": highlight(quietest),
    }
