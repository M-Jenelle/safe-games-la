"""Per-venue buffer joins and the two frontend payloads."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

import pandas as pd

logger = logging.getLogger("pipeline")

from pipeline.geo import DEFAULT_BUFFER_RADIUS_M, buffer_zone, nearest_row
from pipeline.loaders import is_lapd_agency


def _split_bus_lines(values: pd.Series) -> list[str]:
    lines: set[str] = set()
    for raw in values.dropna():
        for part in str(raw).split(";"):
            token = part.strip()
            if token:
                lines.add(token)

    def sort_key(line: str) -> tuple:
        return (0, int(line)) if line.isdigit() else (1, line)

    return sorted(lines, key=sort_key)


def _crime_by_category(nearby: pd.DataFrame) -> dict[str, int]:
    if nearby.empty:
        return {}
    counts = nearby["category"].value_counts()
    return {str(category): int(count) for category, count in counts.items()}


def _crime_by_month(nearby: pd.DataFrame) -> dict[str, int]:
    if nearby.empty:
        return {}
    dated = nearby.dropna(subset=["occurred_at"])
    if dated.empty:
        return {}
    months = dated["occurred_at"].dt.to_period("M").astype(str)
    counts = months.value_counts().sort_index()
    return {str(month): int(count) for month, count in counts.items()}


def _station_payload(row: pd.Series, distance_m: float, extra: list[str]) -> dict:
    payload = {
        "station_id": str(row["station_id"]),
        "station_name": str(row["station_name"]),
        "distance_m": round(float(distance_m), 1),
    }
    for column in extra:
        payload[column] = str(row[column])
    return payload


def _quality_flags(
    venue: pd.Series,
    crime_count: int,
    rail_count: int,
    bus_count: int,
    fire_hit: bool,
    police_hit: bool,
    lapd: bool | None,
    hospital_hit: bool = True,
) -> list[str]:
    flags: list[str] = []
    if crime_count == 0:
        flags.append("no_crime_within_radius")
    if rail_count == 0:
        flags.append("no_rail_stations_within_radius")
    if bus_count == 0:
        flags.append("no_bus_stops_within_radius")
    if not fire_hit:
        flags.append("no_fire_stations_loaded")
    if not police_hit:
        flags.append("no_police_stations_loaded")
    if not hospital_hit:
        flags.append("no_hospitals_loaded")
    geocode = str(venue.get("geocode_result", "") or "")
    if geocode.startswith("MISMATCH"):
        flags.append("geocode_mismatch")
    elif geocode.startswith("NOT_FOUND"):
        flags.append("geocode_not_found")
    city = str(venue.get("city", "") or "").casefold()
    if police_hit and lapd is False and city == "los angeles":
        flags.append("city_of_la_but_nearest_station_is_not_lapd")
    return flags


def _crime_point_records(venue_id: str, nearby: pd.DataFrame) -> list[dict]:
    if nearby.empty:
        return []
    ordered = nearby.sort_values(
        ["occurred_at", "incident_id"],
        ascending=True,
        na_position="last",
    )
    points: list[dict] = []
    for row in ordered.itertuples(index=False):
        occurred = row.occurred_at
        date = None if pd.isna(occurred) else occurred.strftime("%Y-%m-%d")
        points.append(
            {
                "venue_id": venue_id,
                "incident_id": str(row.incident_id),
                "latitude": float(row.latitude),
                "longitude": float(row.longitude),
                "category": str(row.category),
                "date": date,
            }
        )
    return points


def build_venue_outputs(
    venues: pd.DataFrame,
    crime: pd.DataFrame,
    rail: pd.DataFrame,
    bus: pd.DataFrame,
    fire: pd.DataFrame,
    police: pd.DataFrame,
    radius_m: float = DEFAULT_BUFFER_RADIUS_M,
    source_names: dict[str, str] | None = None,
    hospitals: pd.DataFrame | None = None,
) -> tuple[dict, dict]:
    """Build ``venue_summary`` and ``crime_points_by_venue`` documents.

    Crime incidents inside more than one venue buffer are repeated under each
    venue so a per-venue heatmap does not have to recompute the join.
    """
    area_km2 = math.pi * (radius_m / 1000.0) ** 2
    summary_rows: list[dict] = []
    points_by_venue: dict[str, dict] = {}

    ordered = venues.sort_values("venue_id")
    for venue in ordered.itertuples(index=False):
        origin_lat = float(venue.latitude)
        origin_lon = float(venue.longitude)
        venue_id = str(venue.venue_id)
        logger.info("buffering %s %s", venue_id, venue.venue_name)

        crime_nearby = buffer_zone(crime, origin_lat, origin_lon, radius_m)
        rail_nearby = buffer_zone(rail, origin_lat, origin_lon, radius_m)
        bus_nearby = buffer_zone(bus, origin_lat, origin_lon, radius_m)
        fire_hit = nearest_row(fire, origin_lat, origin_lon)
        police_hit = nearest_row(police, origin_lat, origin_lon)
        hospital_hit = nearest_row(hospitals, origin_lat, origin_lon) if hospitals is not None else None

        crime_count = int(len(crime_nearby))
        rail_stations = [
            {
                "station_id": str(row.station_id),
                "station_name": str(row.station_name),
                "lines": str(row.lines),
                "distance_m": round(float(row.distance_m), 1),
            }
            for row in rail_nearby.sort_values("distance_m").itertuples(index=False)
        ]
        bus_lines = _split_bus_lines(bus_nearby["bus_line"]) if not bus_nearby.empty else []

        nearest_fire = (
            _station_payload(fire_hit[0], fire_hit[1], []) if fire_hit else None
        )
        nearest_police = None
        lapd: bool | None = None
        if police_hit is not None:
            nearest_police = _station_payload(
                police_hit[0],
                police_hit[1],
                ["agency"],
            )
            lapd = is_lapd_agency(str(police_hit[0]["agency"]))
        nearest_hospital = (
            _station_payload(
                hospital_hit[0],
                hospital_hit[1],
                ["emergency_room", "hospital_type", "bed_capacity"],
            )
            if hospital_hit
            else None
        )

        venue_series = ordered.loc[ordered["venue_id"] == venue.venue_id].iloc[0]
        flags = _quality_flags(
            venue_series,
            crime_count,
            len(rail_stations),
            int(len(bus_nearby)),
            fire_hit is not None,
            police_hit is not None,
            lapd,
            hospital_hit is not None if hospitals is not None else True,
        )

        summary_rows.append(
            {
                "venue_id": venue_id,
                "venue_name": str(venue.venue_name),
                "address": str(getattr(venue, "address", "") or ""),
                "city": str(getattr(venue, "city", "") or ""),
                "olympic_zone": str(getattr(venue, "olympic_zone", "") or ""),
                "sports": str(getattr(venue, "sports", "") or ""),
                "latitude": origin_lat,
                "longitude": origin_lon,
                "buffer_radius_m": radius_m,
                "crime_count_nearby": crime_count,
                "crime_per_km2": round(crime_count / area_km2, 1) if area_km2 else 0.0,
                "crime_by_category": _crime_by_category(crime_nearby),
                "crime_by_month": _crime_by_month(crime_nearby),
                "rail_stations_nearby": {
                    "count": len(rail_stations),
                    "stations": rail_stations,
                },
                "bus_stops_nearby": {
                    "count": int(len(bus_nearby)),
                    "lines": bus_lines,
                },
                "nearest_fire_station": nearest_fire,
                "nearest_police_station": nearest_police,
                "nearest_hospital": nearest_hospital,
                "lapd_jurisdiction": lapd,
                "data_quality_flags": flags,
            }
        )
        points_by_venue[venue_id] = {
            "venue_name": str(venue.venue_name),
            "latitude": origin_lat,
            "longitude": origin_lon,
            "point_count": crime_count,
            "points": _crime_point_records(venue_id, crime_nearby),
        }

    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    meta = {
        "generated_at": generated_at,
        "buffer_radius_m": radius_m,
        "buffer_area_km2": round(area_km2, 6),
        "distance": "haversine, earth radius 6371000 m",
        "jurisdiction_method": (
            "This is a nearest-station estimate, not an official boundary."
        ),
        "crime_points_note": (
            "An incident that falls inside more than one venue buffer is listed "
            "under each of those venues."
        ),
        "sources": source_names or {},
        "venue_count": len(summary_rows),
    }
    summary = {"meta": meta, "venues": summary_rows}
    points = {"meta": meta, "by_venue": points_by_venue}
    return summary, points
