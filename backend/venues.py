"""Assemble one venue briefing from the processed files."""

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

from backend import datasets
from backend.datasets import (
    CITY_BASELINE_PATH,
    DatasetNotFound,
    _layer_sources,
    _text,
    HOME_GAMES_PATH,
    MERGED_WEEKDAY_NOTE,
    NIBRS_CUTOFF,
    REPORT_WEEKDAY_NOTE,
    _load_home_games,
    crime_distance_for,
    crime_time_for,
    load_city_baseline,
    load_crime_points,
    load_merged,
    load_nibrs_charts,
    load_permit_day_rows,
    load_permit_upcoming,
    _merged_venue,
    load_summary,
    nibrs_charts_for,
    ticketmaster_for,
)

_month_categories: dict[str, dict[str, dict[str, int]]] = {}
_month_categories_mtime: float | None = None
_weekday_cache: dict[str, tuple[dict, dict]] | None = None
_weekday_mtime: float | None = None
_hospitals = None
_hospitals_mtime: float | None = None
_present_cache: dict | None = None
_present_mtime: float | None = None


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
    if _month_categories_mtime != datasets._points_mtime:
        _month_categories.clear()
        _month_categories_mtime = datasets._points_mtime
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

def _density_rank(venue: dict, venues: list[dict]) -> dict:
    rate = float(venue["crime_per_km2"])
    higher = sum(1 for other in venues if float(other["crime_per_km2"]) > rate)
    tied = sum(1 for other in venues if float(other["crime_per_km2"]) == rate) > 1
    return {"rank": higher + 1, "of": len(venues), "tied": tied}

def _overlapping_venues(venue: dict, venues: list[dict]) -> list[dict]:
    radius = float(venue["buffer_radius_m"])
    hits = []
    for other in venues:
        if other["venue_id"] == venue["venue_id"]:
            continue
        distance = float(haversine_m(
            venue["latitude"],
            venue["longitude"],
            other["latitude"],
            other["longitude"],
        ))
        if distance < radius + float(other["buffer_radius_m"]):
            hits.append({
                "venue_id": other["venue_id"],
                "venue_name": other["venue_name"],
                "distance_m": round(distance, 1),
            })
    hits.sort(key=lambda item: item["distance_m"])
    return hits

def _hospital_frame():
    global _hospitals, _hospitals_mtime
    path = _layer_sources()["hospitals"]
    if not path.exists():
        return None
    mtime = path.stat().st_mtime
    if _hospitals is None or mtime != _hospitals_mtime:
        frame, _report = load_hospitals(path)
        _hospitals = frame
        _hospitals_mtime = mtime
    return _hospitals

def nearest_emergency_room(latitude: float, longitude: float) -> dict | None:
    """Closest hospital whose record says it has an emergency room."""
    frame = _hospital_frame()
    if frame is None or frame.empty or "emergency_room" not in frame.columns:
        return None
    flag = frame["emergency_room"].astype(str).str.strip().str.lower()
    rooms = frame.loc[flag.isin({"yes", "y", "true", "1"})]
    hit = nearest_row(rooms, latitude, longitude)
    if hit is None:
        return None
    row, distance = hit
    return {
        "station_id": _text(row["station_id"]),
        "station_name": _text(row["station_name"]),
        "distance_m": round(float(distance), 1),
        "emergency_room": "Yes",
        "hospital_type": _text(row.get("hospital_type", "")),
        "bed_capacity": _text(row.get("bed_capacity", "")),
    }

def _venue_weekdays(venue_id: str) -> tuple[dict, dict]:
    """2020–2024 day counts, and the same counts before the NIBRS cutoff."""
    global _weekday_cache, _weekday_mtime
    points = load_crime_points()
    if _weekday_cache is None or _weekday_mtime != datasets._points_mtime:
        built: dict[str, tuple[dict, dict]] = {}
        for vid, block in (points.get("by_venue") or {}).items():
            dates = []
            legacy = []
            for point in block.get("points") or []:
                day = str(point.get("date") or "")[:10]
                if len(day) != 10:
                    continue
                item = {"date": day, "group": crime_group(point.get("category"))}
                dates.append(item)
                if day < NIBRS_CUTOFF:
                    legacy.append(item)
            reports = weekday_from_dates(dates)
            reports["disclaimer"] = REPORT_WEEKDAY_NOTE
            built[vid] = (reports, weekday_from_dates(legacy))
        _weekday_cache = built
        _weekday_mtime = datasets._points_mtime
    return _weekday_cache.get(venue_id, ({}, {}))

def _record_total(value) -> int:
    if isinstance(value, dict):
        return sum(int(count) for count in value.values())
    return int(value or 0)

def present_headlines() -> dict[str, dict]:
    """2020 through the latest month: reports before March 7, 2024, then NIBRS offenses."""
    global _present_cache, _present_mtime
    merged = load_merged()
    if not merged:
        return {}
    if _present_cache is not None and datasets._merged_mtime == _present_mtime:
        return _present_cache
    rows = []
    for venue in load_summary()["venues"]:
        block = (merged.get("venues") or {}).get(venue["venue_id"]) or {}
        months = {
            month: _record_total(groups)
            for month, groups in (block.get("merged_by_month") or {}).items()
        }
        months = {month: count for month, count in months.items() if count}
        count = sum(months.values())
        radius_km = float(venue["buffer_radius_m"]) / 1000.0
        area_km2 = math.pi * radius_km * radius_km
        rate = round(count / area_km2, 1) if area_km2 else 0.0
        peak_count = max(months.values()) if months else 0
        peak_month = min(
            (month for month, value in months.items() if value == peak_count),
            default="",
        )
        rows.append({
            "count": count,
            "crime_per_km2": rate,
            "peak_month": peak_month,
            "peak_count": peak_count,
            "venue_id": venue["venue_id"],
        })
    for row in rows:
        rate = row["crime_per_km2"]
        higher = sum(1 for other in rows if other["crime_per_km2"] > rate)
        tied = sum(1 for other in rows if other["crime_per_km2"] == rate) > 1
        row["density_rank"] = {"rank": higher + 1, "of": len(rows), "tied": tied}
    _present_cache = {row["venue_id"]: row for row in rows}
    _present_mtime = datasets._merged_mtime
    return _present_cache

def _city_comparison(venue: dict, present: dict | None = None) -> dict | None:
    baseline = load_city_baseline()
    if not baseline:
        return None
    reports_rate = float(baseline["crime_per_km2"])
    reports = compared_with_city(float(venue["crime_per_km2"]), reports_rate, "2020–2024")
    payload = {
        "reports": {
            "incident_count": baseline["incident_count"],
            "crime_per_km2": reports_rate,
            "land_area_sq_mi": baseline["land_area_sq_mi"],
            "period": "2020–2024",
            "ratio": reports["ratio"],
            "relation": reports["relation"],
            "value": reports["value"],
            "caption": reports["caption"],
            "summary": reports["summary"],
            "hint": baseline["note"],
        },
    }
    nibrs_base = baseline.get("nibrs") or {}
    nibrs_count = (venue.get("nibrs") or {}).get("count")
    if nibrs_base and nibrs_count is not None:
        radius_km = float(venue["buffer_radius_m"]) / 1000.0
        area_km2 = math.pi * radius_km * radius_km
        venue_rate = round(int(nibrs_count) / area_km2, 1) if area_km2 else 0.0
        city_rate = float(nibrs_base["crime_per_km2"])
        compared = compared_with_city(venue_rate, city_rate, "Mar 2024–present")
        payload["nibrs"] = {
            "offense_count": nibrs_base["offense_count"],
            "crime_per_km2": city_rate,
            "venue_per_km2": venue_rate,
            "start": nibrs_base["start"],
            "end": nibrs_base["end"],
            "period": "Mar 2024–present",
            "ratio": compared["ratio"],
            "relation": compared["relation"],
            "value": compared["value"],
            "caption": compared["caption"],
            "summary": compared["summary"],
            "hint": nibrs_base["note"],
        }
    present_base = baseline.get("present") or {}
    if present and present_base:
        city_rate = float(present_base["crime_per_km2"])
        compared = compared_with_city(float(present["crime_per_km2"]), city_rate, "2020–present")
        payload["present"] = {
            "count": present_base["count"],
            "crime_per_km2": city_rate,
            "through": present_base["through"],
            "period": "2020–present",
            "ratio": compared["ratio"],
            "relation": compared["relation"],
            "value": compared["value"],
            "caption": compared["caption"],
            "summary": compared["summary"],
            "hint": present_base["note"],
        }
    return payload

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
            venues = load_summary()["venues"]
            enriched["density_rank"] = _density_rank(venue, venues)
            enriched["overlapping_venues"] = _overlapping_venues(venue, venues)
            present = present_headlines().get(venue_id)
            if present:
                enriched["present"] = present
            city = _city_comparison(enriched, present)
            if city:
                enriched["city_baseline"] = city
            enriched["nearest_emergency_room"] = nearest_emergency_room(
                float(venue["latitude"]),
                float(venue["longitude"]),
            )
            reports_week, legacy_week = _venue_weekdays(venue_id)
            nibrs_week = (nibrs_charts or {}).get("weekday") or {}
            if legacy_week or nibrs_week:
                merged_week = combine_weekday(legacy_week, nibrs_week)
                merged_week["disclaimer"] = MERGED_WEEKDAY_NOTE
                enriched["merged_weekday"] = annotate_weekday(merged_week, "records")
            if reports_week:
                enriched["crime_weekday"] = annotate_weekday(reports_week, "incidents")
            if nibrs_week:
                enriched["nibrs_weekday"] = annotate_weekday(nibrs_week, "offenses")
            games = _load_home_games()
            home_id = ((games or {}).get("meta") or {}).get("venue_id")
            enriched["display"] = display_for_venue(
                enriched,
                enriched.get("overlapping_venues") or [],
                len(venues),
                reports_through_label(NIBRS_CUTOFF),
                home_id == venue_id,
            )
            enriched["home_games_available"] = bool(enriched["display"]["home_games_available"])
            return enriched
    return None

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
    games = _load_home_games()
    home_id = ((games or {}).get("meta") or {}).get("venue_id")
    note = PERMIT_NOT_MLB_NOTE if home_id == venue_id else None
    venue_name = rows[0].get("venue_name") or match["venue_name"]
    view = comparison_view(rows, year=year, month=month, venue_id=venue_id, venue_name=venue_name, note=note)
    view["source"] = source
    unit = "offenses" if source == "nibrs" else "reports"
    view["intro"] = PERMIT_INTRO
    view["load_intro"] = PERMIT_LOAD_INTRO
    view["group_gap_note"] = group_gap_note(unit)
    enough = int((view.get("summary") or {}).get("event_day_count") or 0) >= MIN_EVENT_DAYS_FOR_PERCENT
    view["group_gap_empty"] = group_gap_empty(unit) if enough else ""
    if source == "nibrs":
        view["source_note"] = (load_nibrs_charts() or {}).get("permit_note") or ""
        view["upcoming"] = []
    else:
        view["upcoming"] = load_permit_upcoming().get(venue_id) or []
    return view

def _game_public(block: dict) -> dict:
    """Same percent rule as the permit table: enough days, a real gap, and a test that clears."""
    difference = float(block["absolute_difference"])
    other_mean = float(block["non_game_day_mean"])
    shown = bool(
        int(block["game_day_count"]) >= 8
        and block.get("significant")
        and abs(difference) >= 0.05
        and other_mean > 0
    )
    return {
        "event_day_count": int(block["game_day_count"]),
        "other_day_count": int(block["non_game_day_count"]),
        "event_day_mean": block["game_day_mean"],
        "other_day_mean": block["non_game_day_mean"],
        "event_day_median": block["game_day_median"],
        "other_day_median": block["non_game_day_median"],
        "absolute_difference": difference,
        "lift_pct": block["lift_pct"] if shown else None,
        "percent_shown": shown,
    }

def home_game_comparison(venue_id: str) -> dict | None:
    """Dodger regular-season home games versus other days in those months.

    The per-listing lift in the file is not returned. None when the venue id
    is unknown.
    """
    match = next((venue for venue in load_summary()["venues"] if venue["venue_id"] == venue_id), None)
    if match is None:
        return None
    payload = _load_home_games()
    meta_block = (payload or {}).get("meta") or {}
    if payload is None or meta_block.get("venue_id") != venue_id:
        return {"available": False, "venue_id": venue_id, "venue_name": match["venue_name"]}
    summary = _game_public(payload["lift"])
    groups = []
    for group in payload.get("by_category") or []:
        if not group.get("significant"):
            continue
        if abs(float(group["absolute_difference"])) < MATERIAL_DAILY_DIFFERENCE:
            continue
        public = _game_public(group)
        if public["percent_shown"] and float(group["non_game_day_mean"]) < OTHER_DAY_MEAN_FOR_PERCENT:
            public = {**public, "percent_shown": False, "lift_pct": None}
        groups.append({"group": group["group"], "label": group["label"], **public})
    groups.sort(key=lambda item: abs(item["absolute_difference"]), reverse=True)
    return {
        "available": True,
        "venue_id": venue_id,
        "venue_name": match["venue_name"],
        "summary": summary,
        "groups": groups,
        "group_gap_note": group_gap_note("reports"),
        "note": (
            "A game day is a completed Dodgers regular-season home game. "
            "Other days are the rest of March through October in 2020–2024."
        ),
        "disclaimer": (
            "Uses completed regular-season home games, 2020-2024, inside the 800 m buffer.\n"
            "2020 games had little or no crowd.\n"
            "A large percentage can still be less than one extra report a day.\n"
            "This is a past comparison, not a forecast."
        ),
    }

