"""Read pipeline JSON, reloading when the files change on disk."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from pipeline.geo import haversine_m

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
)
from pipeline.run import find_crime_csv, resolve_data_dir

REPO_ROOT = Path(__file__).resolve().parents[1]
SUMMARY_PATH = REPO_ROOT / "data" / "processed" / "venue_summary.json"
POINTS_PATH = REPO_ROOT / "data" / "processed" / "crime_points_by_venue.json"

_summary: dict | None = None
_summary_mtime: float | None = None
_points: dict | None = None
_points_mtime: float | None = None
_crime_views: dict | None = None
_crime_heat_mtime: float | None = None

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


def list_venues() -> list[dict]:
    return [venue_card(venue) for venue in load_summary()["venues"]]


def get_venue(venue_id: str) -> dict | None:
    for venue in load_summary()["venues"]:
        if venue["venue_id"] == venue_id:
            return venue
    return None


def get_crime_points(venue_id: str) -> dict | None:
    block = load_crime_points().get("by_venue", {}).get(venue_id)
    if block is None:
        return None
    return block


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
    if view not in _crime_views:
        known = ", ".join(_crime_views)
        raise ValueError(f"Unknown crime view '{view}'. Known views: {known}")
    selected = _crime_views[view]
    options = [
        {"id": view_id, "label": payload["label"], "group": payload["group"]}
        for view_id, payload in _crime_views.items()
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


def _point(row_id: str, name: str, latitude: float, longitude: float, detail: str = "") -> dict:
    point = {
        "id": row_id,
        "name": name,
        "latitude": round(float(latitude), 6),
        "longitude": round(float(longitude), 6),
    }
    if detail:
        point["detail"] = detail
    return point


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
    bus_stops = (
        bus.groupby("stop_id", as_index=False)
        .agg(
            latitude=("latitude", "first"),
            longitude=("longitude", "first"),
            lines=("bus_line", lambda values: ", ".join(sorted({str(value) for value in values if str(value)}))),
        )
    )

    def rows(frame, id_field: str, name_field: str, detail_field: str | None = None) -> list[dict]:
        points = []
        for row in frame.itertuples(index=False):
            detail = ""
            if detail_field is not None:
                detail = str(getattr(row, detail_field) or "")
            points.append(
                _point(
                    str(getattr(row, id_field)),
                    str(getattr(row, name_field) or "Unnamed"),
                    row.latitude,
                    row.longitude,
                    detail,
                )
            )
        return points

    _layers = {
        "fire": rows(fire, "station_id", "station_name"),
        "hospitals": rows(hospitals, "station_id", "station_name"),
        "police": rows(police, "station_id", "station_name", "agency"),
        "rail": rows(rail, "station_id", "station_name", "lines"),
        "bus": [
            _point(str(row.stop_id), "Bus stop", row.latitude, row.longitude, str(row.lines or ""))
            for row in bus_stops.itertuples(index=False)
        ],
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
