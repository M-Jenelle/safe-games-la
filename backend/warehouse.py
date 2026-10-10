"""Read-only DuckDB tables for the Torchy agent.

Loaded once from the processed files and the facility CSVs. The agent may
only SELECT. The series-break and overlap rules live in data/DATA_DICTIONARY.md
and are repeated by the tools when a result crosses them.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import duckdb
import pandas as pd

from backend.datasets import OFFICIAL_NAMES, REPO_ROOT
from pipeline.crime_groups import GROUP_LABELS, crime_group

ROOT = REPO_ROOT
PROCESSED = ROOT / "data" / "processed"
SERIES_BREAK = "2024-03-07"
HOT_F = 85.0
_connection: duckdb.DuckDBPyConnection | None = None


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def _venues(summary: dict) -> pd.DataFrame:
    rows = []
    for venue in summary.get("venues") or []:
        source_name = venue.get("venue_name") or ""
        rows.append({
            "venue_id": venue.get("venue_id"),
            "venue_name": OFFICIAL_NAMES.get(source_name, source_name),
            "former_name": source_name if source_name in OFFICIAL_NAMES else "",
            "olympic_zone": venue.get("olympic_zone") or "",
            "sports": venue.get("sports") or "",
            "latitude": venue.get("latitude"),
            "longitude": venue.get("longitude"),
            "buffer_radius_m": venue.get("buffer_radius_m") or 800,
        })
    return pd.DataFrame(rows)


def _overlaps(venues: pd.DataFrame) -> pd.DataFrame:
    rows = []
    records = venues.to_dict("records")
    for i, left in enumerate(records):
        for right in records[i + 1:]:
            distance = _haversine_m(float(left["latitude"]), float(left["longitude"]), float(right["latitude"]), float(right["longitude"]))
            if distance < 1600:
                rows.append({
                    "venue_a": left["venue_id"],
                    "venue_b": right["venue_id"],
                    "name_a": left["venue_name"],
                    "name_b": right["venue_name"],
                    "distance_m": round(distance, 1),
                })
    return pd.DataFrame(rows)


def _incidents(path: Path) -> pd.DataFrame:
    if not path.is_file():
        return pd.DataFrame(columns=["venue_id", "incident_id", "category", "offense_group", "date", "latitude", "longitude", "series"])
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for block in (payload.get("by_venue") or {}).values():
        for point in block.get("points") or []:
            rows.append({
                "venue_id": point.get("venue_id"),
                "incident_id": str(point.get("incident_id") or ""),
                "category": point.get("category") or "",
                "offense_group": crime_group(point.get("category")),
                "date": str(point.get("date") or "")[:10],
                "latitude": point.get("latitude"),
                "longitude": point.get("longitude"),
                "series": "lapd_report",
            })
    return pd.DataFrame(rows)


def _offense_counts(incidents: pd.DataFrame) -> pd.DataFrame:
    """Report groups before the series break, then NIBRS groups from the chart file."""
    rows = []
    if not incidents.empty and "offense_group" in incidents.columns:
        subset = incidents[incidents["date"] < SERIES_BREAK]
        grouped = subset.groupby(["venue_id", "offense_group"], dropna=False).size()
        for (venue_id, group_id), count in grouped.items():
            rows.append({
                "venue_id": venue_id,
                "offense_group": group_id,
                "label": GROUP_LABELS.get(group_id, group_id),
                "series": "lapd_report",
                "records": int(count),
            })
    path = PROCESSED / "nibrs_charts.json"
    if path.is_file():
        payload = json.loads(path.read_text(encoding="utf-8"))
        for venue_id, block in (payload.get("venues") or {}).items():
            totals: dict[str, int] = {}
            for period in (block.get("time") or {}).get("periods") or []:
                for group in period.get("groups") or []:
                    group_id = str(group.get("id") or "other")
                    totals[group_id] = totals.get(group_id, 0) + int(group.get("count") or 0)
            for group_id, count in totals.items():
                rows.append({
                    "venue_id": venue_id,
                    "offense_group": group_id,
                    "label": GROUP_LABELS.get(group_id, group_id),
                "series": "nibrs",
                "records": count,
            })
    if not rows:
        return pd.DataFrame(columns=["venue_id", "offense_group", "label", "series", "records"])
    return pd.DataFrame(rows)


def _events() -> pd.DataFrame:
    rows = []
    games = ROOT / "data" / "raw" / "mlb" / "dodgers_home_games_2020_2024.csv"
    if games.is_file():
        frame = pd.read_csv(games)
        for record in frame.to_dict("records"):
            if "Dodger Stadium" not in str(record.get("venue_name") or ""):
                continue
            rows.append({
                "venue_id": "V01",
                "date": str(record.get("date") or "")[:10],
                "name": f"{record.get('away_team') or ''} at Dodgers".strip(),
                "kind": "home_game",
            })
    listings = PROCESSED / "ticketmaster_listings.json"
    if listings.is_file():
        payload = json.loads(listings.read_text(encoding="utf-8"))
        for venue_id, block in (payload.get("venues") or {}).items():
            for event in block.get("events") or []:
                rows.append({
                    "venue_id": venue_id,
                    "date": str(event.get("date") or "")[:10],
                    "name": event.get("name") or "",
                    "kind": "ticketmaster",
                })
    return pd.DataFrame(rows)


def _facilities() -> pd.DataFrame:
    rows = []
    fire = ROOT / "data" / "lafd_fire_stations.csv"
    if fire.is_file():
        for record in _read_csv(fire).to_dict("records"):
            rows.append({
                "kind": "fire",
                "name": record.get("station_name") or "",
                "address": record.get("address") or "",
                "latitude": record.get("latitude") or None,
                "longitude": record.get("longitude") or None,
                "emergency_room": "",
            })
    police = ROOT / "data" / "la_county_police_stations.csv"
    if police.is_file():
        for record in _read_csv(police).to_dict("records"):
            rows.append({
                "kind": "police",
                "name": record.get("station_name") or "",
                "address": record.get("address") or "",
                "latitude": record.get("latitude") or None,
                "longitude": record.get("longitude") or None,
                "emergency_room": "",
            })
    hospitals = ROOT / "data" / "hospitals_in_LA.csv"
    if hospitals.is_file():
        frame = pd.read_csv(hospitals)
        for record in frame.to_dict("records"):
            flag = record.get("Emergency Room?")
            rows.append({
                "kind": "hospital",
                "name": record.get("FACNAME") or "",
                "address": record.get("ADDRESS") or "",
                "latitude": record.get("LATITUDE"),
                "longitude": record.get("LONGITUDE"),
                "emergency_room": "" if flag is None or (isinstance(flag, float) and math.isnan(flag)) else str(flag),
            })
    return pd.DataFrame(rows)


def _transit() -> pd.DataFrame:
    rows = []
    rail = ROOT / "data" / "la_metro_rail_stations.csv"
    if rail.is_file():
        for record in _read_csv(rail).to_dict("records"):
            rows.append({
                "kind": "rail",
                "name": record.get("station_name") or "",
                "lines": record.get("lines") or "",
                "latitude": record.get("latitude") or None,
                "longitude": record.get("longitude") or None,
            })
    bus = ROOT / "data" / "la_metro_bus_stops.csv"
    if bus.is_file():
        for record in _read_csv(bus).to_dict("records"):
            rows.append({
                "kind": "bus",
                "name": record.get("station_name") or "",
                "lines": record.get("bus_line") or "",
                "latitude": record.get("latitude") or None,
                "longitude": record.get("longitude") or None,
            })
    return pd.DataFrame(rows)


def connect() -> duckdb.DuckDBPyConnection:
    """Open the in-memory database, building it on first use."""
    global _connection
    if _connection is not None:
        return _connection
    summary_path = PROCESSED / "venue_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {"venues": []}
    venues = _venues(summary)
    days_path = PROCESSED / "venue_days_with_weather.csv"
    connection = duckdb.connect(":memory:")
    connection.register("venues_frame", venues)
    connection.execute("CREATE TABLE venues AS SELECT * FROM venues_frame")
    _attach_headlines(connection)
    if days_path.is_file():
        connection.execute(
            f"""
            CREATE TABLE venue_days AS
            SELECT
                venue_id, venue_name,
                TRY_CAST(latitude AS DOUBLE) AS latitude,
                TRY_CAST(longitude AS DOUBLE) AS longitude,
                CAST(date AS DATE) AS date,
                TRY_CAST(temp_f_mean AS DOUBLE) AS temp_f_mean,
                TRY_CAST(precip_in AS DOUBLE) AS precip_in,
                TRY_CAST(wet_day AS INTEGER) AS wet_day,
                CASE WHEN TRY_CAST(temp_f_mean AS DOUBLE) >= {HOT_F} THEN 1 ELSE 0 END AS hot_day,
                TRY_CAST(incident_count AS INTEGER) AS incident_count,
                count_source,
                TRY_CAST(is_permit_event_day AS INTEGER) AS is_permit_event_day,
                TRY_CAST(permit_count AS INTEGER) AS permit_count
            FROM read_csv_auto('{days_path.as_posix()}', header=true)
            """
        )
        for source_name, official in OFFICIAL_NAMES.items():
            connection.execute(
                "UPDATE venue_days SET venue_name = ? WHERE venue_name = ?",
                [official, source_name],
            )
    else:
        connection.execute(
            """
            CREATE TABLE venue_days (
                venue_id VARCHAR, venue_name VARCHAR, latitude DOUBLE, longitude DOUBLE,
                date DATE, temp_f_mean DOUBLE, precip_in DOUBLE, wet_day INTEGER, hot_day INTEGER,
                incident_count INTEGER, count_source VARCHAR, is_permit_event_day INTEGER, permit_count INTEGER
            )
            """
        )
    incidents = _incidents(PROCESSED / "crime_points_by_venue.json")
    connection.register("incidents_frame", incidents)
    connection.execute("CREATE TABLE incidents AS SELECT * FROM incidents_frame")
    connection.register("offense_frame", _offense_counts(incidents))
    connection.execute("CREATE TABLE offense_counts AS SELECT * FROM offense_frame")
    connection.execute(
        """
        CREATE TABLE permits AS
        SELECT venue_id, venue_name, date, permit_count, incident_count, count_source
        FROM venue_days
        WHERE is_permit_event_day = 1
        """
    )
    connection.register("events_frame", _events())
    connection.execute("CREATE TABLE events AS SELECT * FROM events_frame")
    connection.register("facilities_frame", _facilities())
    connection.execute(
        """
        CREATE TABLE facilities AS
        SELECT kind, name, address,
               TRY_CAST(latitude AS DOUBLE) AS latitude,
               TRY_CAST(longitude AS DOUBLE) AS longitude,
               emergency_room
        FROM facilities_frame
        """
    )
    connection.register("transit_frame", _transit())
    connection.execute(
        """
        CREATE TABLE transit_stops AS
        SELECT kind, name, lines,
               TRY_CAST(latitude AS DOUBLE) AS latitude,
               TRY_CAST(longitude AS DOUBLE) AS longitude
        FROM transit_frame
        """
    )
    connection.register("overlap_frame", _overlaps(venues) if not venues.empty else pd.DataFrame(
        columns=["venue_a", "venue_b", "name_a", "name_b", "distance_m"]
    ))
    connection.execute("CREATE TABLE overlap_pairs AS SELECT * FROM overlap_frame")
    _load_nearby(connection)
    connection.execute("SET enable_external_access = false")
    connection.execute("SET lock_configuration = true")
    _connection = connection
    return connection


def _attach_headlines(connection: duckdb.DuckDBPyConnection) -> None:
    """2020–present record count on venues, so a headline is not a raw incident count."""
    from backend.venues import present_headlines

    connection.execute("ALTER TABLE venues ADD COLUMN present_records INTEGER")
    connection.execute("ALTER TABLE venues ADD COLUMN present_per_km2 DOUBLE")
    for venue_id, row in present_headlines().items():
        connection.execute(
            "UPDATE venues SET present_records = ?, present_per_km2 = ? WHERE venue_id = ?",
            [int(row.get("count") or 0), row.get("crime_per_km2"), venue_id],
        )


def _load_nearby(connection: duckdb.DuckDBPyConnection) -> None:
    """Places inside each venue circle. transit_stops and facilities have no venue_id."""
    venues = connection.execute(
        "SELECT venue_id, venue_name, latitude, longitude, buffer_radius_m FROM venues"
    ).fetchall()
    places = connection.execute(
        """
        SELECT kind, name, latitude, longitude FROM transit_stops
        UNION ALL
        SELECT kind, name, latitude, longitude FROM facilities
        """
    ).fetchall()
    rows = []
    for venue_id, venue_name, vlat, vlon, radius in venues:
        if vlat is None or vlon is None:
            continue
        limit = float(radius or 800)
        for kind, name, lat, lon in places:
            if lat is None or lon is None:
                continue
            distance = _haversine_m(float(vlat), float(vlon), float(lat), float(lon))
            if distance <= limit:
                rows.append({
                    "venue_id": venue_id,
                    "venue_name": venue_name,
                    "kind": kind,
                    "name": name,
                    "distance_m": round(distance, 1),
                })
    frame = pd.DataFrame(rows, columns=["venue_id", "venue_name", "kind", "name", "distance_m"])
    connection.register("nearby_frame", frame)
    connection.execute("CREATE TABLE nearby AS SELECT * FROM nearby_frame")


def reset() -> None:
    """Drop the cached database. Tests use this."""
    global _connection
    if _connection is not None:
        _connection.close()
    _connection = None
