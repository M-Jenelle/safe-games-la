"""Answers from the supporting fields already produced by the pipeline."""

from __future__ import annotations

from copy import deepcopy
import math

CONTEXT_INTENTS = {"rail", "bus", "transit", "fire", "police", "hospital", "services", "sports"}
LAYERS = {
    "rail": ("rail_stations", "LA Metro GTFS rail stations", "rail_stations_nearby"),
    "bus": ("bus_stops", "LA Metro GTFS bus stops", "bus_stops_nearby"),
    "fire": ("fire_stations", "LAFD fire stations via LA Open Data", "nearest_fire_station"),
    "police": ("police_stations", "LA County GIS police/sheriff stations", "nearest_police_station"),
    "hospital": ("hospitals", "LA County hospital facility data", "nearest_hospital"),
    "sports": ("venues", "Project LA28 venue roster", "sports"),
}


class ContextUnavailable(Exception):
    """A missing or inconsistent supporting field cannot become a zero."""


def _distance(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _count(value) -> bool:
    return type(value) is int and value >= 0


def context_answer(intent: str, venue: dict, meta: dict) -> tuple[str, list, list]:
    topics = ["rail", "bus"] if intent == "transit" else ["fire", "police", "hospital"] if intent == "services" else [intent]
    radius = venue["buffer_radius_m"]
    results, paragraphs, sources = [], [], []
    for topic in topics:
        source_key, label, field = LAYERS[topic]
        source_file = meta.get("sources", {}).get(source_key)
        if not isinstance(source_file, str) or not source_file or field not in venue:
            raise ContextUnavailable("The requested supporting data or its source is missing from the processed summary.")
        row = {"venue_id": venue["venue_id"], "venue_name": venue["venue_name"], "topic": topic}
        value = venue[field]
        if topic in {"rail", "bus"}:
            scope = f"Within an {radius:g} m radius around the venue."
            if not isinstance(value, dict) or not _count(value.get("count")):
                raise ContextUnavailable("The processed transit count is missing or invalid.")
            count = value["count"]
            row["count"] = count
            if topic == "rail":
                stations = value.get("stations")
                if (
                    not isinstance(stations, list) or len(stations) != count
                    or any(
                        not isinstance(station, dict) or not station.get("station_name")
                        or not isinstance(station.get("lines"), str)
                        or not _distance(station.get("distance_m")) or station["distance_m"] > radius
                        for station in stations
                    )
                ):
                    raise ContextUnavailable("The processed rail stations do not match the recorded count or radius.")
                row["stations"] = deepcopy(stations)
                text = f"{venue['venue_name']} — rail stations within {radius:g} m: {count:,}."
                if stations:
                    text += "\n" + "\n".join(
                        f"{station['station_name']} — lines: {station['lines'] or 'not recorded'}; "
                        f"straight-line distance: {station['distance_m']:,.1f} m."
                        for station in stations
                    )
                else:
                    text += " No rail station is recorded within this radius."
            else:
                lines = value.get("lines")
                if not isinstance(lines, list) or any(not isinstance(line, str) or not line for line in lines):
                    raise ContextUnavailable("The processed bus routes are missing or invalid.")
                if bool(count) != bool(lines):
                    raise ContextUnavailable("The processed bus routes do not match the recorded stop count.")
                row["lines"] = list(lines)
                text = f"{venue['venue_name']} — bus stops within {radius:g} m: {count:,}."
                text += f"\nRecorded routes serving these stops: {', '.join(lines)}." if lines else " No bus stop is recorded within this radius."
            source_radius = radius
        elif topic == "sports":
            scope = "Sports listed at this venue in the project roster. This is not a confirmed event schedule."
            if not isinstance(value, str):
                raise ContextUnavailable("The venue's listed sports field is invalid.")
            sports = [sport.strip() for sport in value.split(";") if sport.strip()]
            row["sports"] = sports
            text = f"{venue['venue_name']} — listed sports: {'; '.join(sports)}." if sports else f"No sports are recorded for {venue['venue_name']}."
            source_radius = None
        else:
            scope = "Nearest facility in the loaded dataset; straight-line distance, not restricted to 800 m."
            title = {"fire": "fire station", "police": "police/sheriff station", "hospital": "hospital"}[topic]
            source_radius = None
            if value is None:
                row["facility"] = None
                text = f"No nearest {title} is recorded for {venue['venue_name']}."
            else:
                if not isinstance(value, dict) or not value.get("station_name") or not _distance(value.get("distance_m")):
                    raise ContextUnavailable("The processed nearest-facility name or distance is missing or invalid.")
                row["facility"] = deepcopy(value)
                text = f"Nearest recorded {title} to {venue['venue_name']}: {value['station_name']}. Straight-line distance: {value['distance_m']:,.1f} m."
                if topic == "police" and value.get("agency"):
                    text += f"\nRecorded agency: {value['agency']}. This does not establish jurisdiction."
                if topic == "hospital":
                    flag = str(value.get("emergency_room", "")).strip().casefold()
                    er = {"yes": "Yes", "no": "No"}.get(flag, "Unknown / not recorded")
                    row["emergency_room"] = er
                    text += f"\nEmergency room (recorded flag): {er}. This is the nearest hospital overall; the data does not identify the nearest hospital with an emergency room."
            text += " Availability, travel time, and response time are not recorded."
        paragraphs.append(text)
        results.append(row)
        sources.append({
            "name": label, "file": source_file, "period": None,
            "radius_m": source_radius, "scope": scope,
            "coverage_date": None, "processed_at": meta.get("generated_at"),
        })
    return "\n\n".join(paragraphs), results, sources


def source_text(sources: list[dict]) -> str:
    labels = "\n".join(f"Source: {source['name']} ({source['file']}). Scope: {source['scope']}" for source in sources)
    processed = sources[0].get("processed_at") or "not recorded"
    return (
        f"{labels}\nSupporting data: static snapshot; coverage/as-of date not recorded in the processed summary. "
        f"Processed at: {processed} (processing time, not a source update date)."
    )
