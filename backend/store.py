"""Read pipeline JSON, reloading when the files change on disk."""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SUMMARY_PATH = REPO_ROOT / "data" / "processed" / "venue_summary.json"
POINTS_PATH = REPO_ROOT / "data" / "processed" / "crime_points_by_venue.json"

_summary: dict | None = None
_summary_mtime: float | None = None
_points: dict | None = None
_points_mtime: float | None = None


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
    """Every venue on one Los Angeles frame, for the map placeholder and a future basemap."""
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
        "placeholder": True,
        "note": (
            "Basemap and crime heatmap are not wired. Markers are the LA28 venues "
            "placed on a fixed Los Angeles frame."
        ),
        "bounds": MAP_BOUNDS,
        "labels": MAP_LABELS,
        "markers": markers,
    }


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
