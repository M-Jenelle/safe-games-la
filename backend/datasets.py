"""Load the processed JSON and CSV files, and reload them when they change."""

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
CITY_BASELINE_PATH = REPO_ROOT / "data" / "processed" / "city_baseline.json"
HOME_GAMES_PATH = REPO_ROOT / "data" / "processed" / "dodger_event_risk.json"
NIBRS_CUTOFF = "2024-03-07"
REPORT_WEEKDAY_NOTE = "Uses 2020-2024 reports and is inside the 800m buffer."
MERGED_WEEKDAY_NOTE = ""

_summary: dict | None = None
_summary_mtime: float | None = None
_points: dict | None = None
_points_mtime: float | None = None
_merged: dict | None = None
_merged_mtime: float | None = None
_city_baseline: dict | None = None
_city_baseline_mtime: float | None = None
_crime_time: dict | None = None
_crime_time_mtime: float | None = None
_crime_distance: dict | None = None
_crime_distance_mtime: float | None = None
_listings: dict | None = None
_listings_mtime: float | None = None
_nibrs_charts: dict | None = None
_nibrs_charts_mtime: float | None = None
_permit_days: dict[str, list[dict]] | None = None
_permit_days_mtime: float | None = None
_permit_upcoming: dict[str, list[dict]] | None = None
_permit_upcoming_mtime: float | None = None
_home_games: dict | None = None
_home_games_mtime: float | None = None


class DatasetNotFound(FileNotFoundError):
    """Raised when a pipeline output the API needs is missing."""


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


def _layer_sources() -> dict[str, Path]:
    data_dir = resolve_data_dir(REPO_ROOT)
    return {
        "fire": data_dir / "lafd_fire_stations.csv",
        "hospitals": data_dir / "hospitals_in_LA.csv",
        "police": data_dir / "la_county_police_stations.csv",
        "rail": data_dir / "la_metro_rail_stations.csv",
        "bus": data_dir / "la_metro_bus_stops.csv",
    }


def _read_json(path: Path) -> tuple[dict, float]:
    if not path.exists():
        raise DatasetNotFound(
            f"Missing {path.relative_to(REPO_ROOT)}. Run `python -m pipeline.run` first."
        )
    mtime = path.stat().st_mtime
    return json.loads(path.read_text(encoding="utf-8")), mtime

OFFICIAL_NAMES = {
    "DTLA Arena (Crypto.com Arena)": "Crypto.com Arena",
    "LA Convention Center (Halls 1-3)": "LA Convention Center",
    "Exposition Park Stadium (BMO Stadium)": "BMO Stadium",
    "Valley Complexes 1-4 (Sepulveda Basin Recreation Area)": "Sepulveda Basin Recreation Area",
    "Galen Center (USC)": "Galen Center",
}


def _apply_official_names(summary: dict) -> None:
    """Show the public venue name. The stored file keeps the longer source name."""
    for venue in summary.get("venues") or []:
        current = venue.get("venue_name") or ""
        official = OFFICIAL_NAMES.get(current)
        if not official:
            continue
        former = venue.setdefault("former_names", [])
        if current not in former:
            former.append(current)
        venue["venue_name"] = official


def load_summary() -> dict:
    global _summary, _summary_mtime
    if not SUMMARY_PATH.exists():
        raise DatasetNotFound(
            f"Missing {SUMMARY_PATH.relative_to(REPO_ROOT)}. Run `python -m pipeline.run` first."
        )
    mtime = SUMMARY_PATH.stat().st_mtime
    if _summary is None or mtime != _summary_mtime:
        _summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
        _apply_official_names(_summary)
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

def load_city_baseline() -> dict | None:
    """Citywide reports per km². Missing until ``pipeline.city_baseline`` has run."""
    global _city_baseline, _city_baseline_mtime
    if not CITY_BASELINE_PATH.exists():
        _city_baseline = None
        _city_baseline_mtime = None
        return None
    mtime = CITY_BASELINE_PATH.stat().st_mtime
    if _city_baseline is None or mtime != _city_baseline_mtime:
        _city_baseline = json.loads(CITY_BASELINE_PATH.read_text(encoding="utf-8"))
        _city_baseline_mtime = mtime
    return _city_baseline

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

def _load_home_games() -> dict | None:
    global _home_games, _home_games_mtime
    if not HOME_GAMES_PATH.exists():
        _home_games = None
        _home_games_mtime = None
        return None
    mtime = HOME_GAMES_PATH.stat().st_mtime
    if _home_games is None or mtime != _home_games_mtime:
        _home_games = json.loads(HOME_GAMES_PATH.read_text(encoding="utf-8"))
        _home_games_mtime = mtime
    return _home_games

