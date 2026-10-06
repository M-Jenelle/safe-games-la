"""Slim Ticketmaster calendar for the venues a listing was matched to.

Reads the collector CSV without the image and raw-event columns, and
writes one small JSON file. These dates are in the future, so the
output carries no crime counts.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from pipeline.geo import haversine_m

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = REPO_ROOT / "prediction_data" / "ticketmaster_events.csv"
SUMMARY_PATH = REPO_ROOT / "data" / "processed" / "venue_summary.json"
OUTPUT_PATH = REPO_ROOT / "data" / "processed" / "ticketmaster_listings.json"

USECOLS = (
    "event_id",
    "event_name",
    "event_test",
    "venue_id",
    "ticketmaster_venue_name",
    "venue_distance_m",
    "latitude",
    "longitude",
    "start_local_date",
    "start_local_time",
    "status_code",
)

MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
NAME_STOP = {
    "los",
    "angeles",
    "the",
    "at",
    "and",
    "of",
    "center",
    "field",
    "stadium",
    "theater",
    "theatre",
    "hall",
    "halls",
}
DISCLAIMER = (
    "These are Ticketmaster listings. The dates fall after the 2020–2024 crime file, "
    "so no report count is attached. A crowded month means more listed events."
)


def name_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", str(value or "").lower())
        if len(token) >= 4 and token not in NAME_STOP
    }


def shares_name(left: str, right: str) -> bool:
    return bool(name_tokens(left) & name_tokens(right))


def clock_value(value: object) -> str | None:
    """Local start time as HH:MM, or None when Ticketmaster left the hour off."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return None
    parts = text.split(":")
    if len(parts) < 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(float(parts[1]))
    except ValueError:
        return None
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def is_on_sale(value: object) -> bool:
    return str(value or "").strip().lower() == "onsale"


def month_keys(start: date, end: date) -> list[str]:
    keys = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        keys.append(f"{year:04d}-{month:02d}")
        month += 1
        if month == 13:
            month = 1
            year += 1
    return keys


def month_label(key: str) -> str:
    year, month = key.split("-")
    return f"{MONTHS[int(month) - 1]} {year}"


def busiest_sentence(months: list[dict]) -> str:
    positive = [month for month in months if month["count"]]
    if not positive:
        return ""
    peak = max(month["count"] for month in positive)
    names = [month["label"] for month in positive if month["count"] == peak]
    if len(names) == 1:
        return f"{names[0]} has the most listings, {peak}."
    if len(names) == 2:
        return f"{names[0]} and {names[1]} have the most listings, {peak} each."
    return f"{', '.join(names[:-1])}, and {names[-1]} have the most listings, {peak} each."


def clock_sentence(total: int, with_time: int) -> str:
    missing = total - with_time
    if total == 0:
        return ""
    if with_time == total:
        if total == 1:
            return "The listing has a start time."
        return f"All {total} listings have a start time."
    if with_time == 0:
        return "None of these listings have a start time."
    if with_time == 1:
        head = "1 listing has a start time."
    else:
        head = f"{with_time} listings have a start time."
    if missing == 1:
        return f"{head} The other listing does not."
    return f"{head} The other {missing} do not."


def location_note(
    venue_name: str,
    listed_name: str,
    distance_m: float,
    buffer_m: float,
    others: list[dict],
) -> str:
    rounded = int(round(distance_m))
    circle = "inside" if distance_m <= buffer_m else "outside"
    buffer = int(round(buffer_m))
    note = f"Listed at {listed_name}, {rounded:,} m from this pin, {circle} the {buffer:,} m circle."
    if shares_name(venue_name, listed_name):
        return note
    named = [other for other in others if shares_name(other["venue_name"], listed_name)]
    if not named:
        return note
    other = min(named, key=lambda item: item["distance_m"])
    other_m = int(round(other["distance_m"]))
    return f"{note} {other['venue_name']} is {other_m:,} m from the same pin."


def _pin_distance(latitude: float, longitude: float, venue: dict) -> float:
    return float(haversine_m(venue["latitude"], venue["longitude"], latitude, longitude))


def venue_listing_block(rows: list[dict], venue: dict, venues: list[dict]) -> dict:
    """One venue's calendar. ``rows`` already belong to that venue."""
    events = []
    for row in rows:
        day = row.get("date")
        if not isinstance(day, date):
            continue
        events.append({
            "date": day.isoformat(),
            "name": row["name"],
            "start_time": row.get("start_time"),
            "on_sale": bool(row.get("on_sale")),
        })
    events.sort(key=lambda item: (item["date"], item["name"]))
    first = date.fromisoformat(events[0]["date"])
    last = date.fromisoformat(events[-1]["date"])
    counts: dict[str, int] = {}
    for event in events:
        key = event["date"][:7]
        counts[key] = counts.get(key, 0) + 1
    months = [
        {"month": key, "label": month_label(key), "count": counts.get(key, 0)}
        for key in month_keys(first, last)
    ]
    pin = rows[0]
    others = []
    for other in venues:
        if other["venue_id"] == venue["venue_id"]:
            continue
        others.append({
            "venue_name": other["venue_name"],
            "distance_m": _pin_distance(pin["latitude"], pin["longitude"], other),
        })
    distance_m = _pin_distance(pin["latitude"], pin["longitude"], venue)
    with_time = sum(1 for event in events if event["start_time"])
    return {
        "listed_venue": pin["listed_name"],
        "distance_m": int(round(distance_m)),
        "event_count": len(events),
        "with_start_time": with_time,
        "note": location_note(
            venue["venue_name"],
            pin["listed_name"],
            distance_m,
            float(venue.get("buffer_radius_m") or 800),
            others,
        ),
        "busiest": busiest_sentence(months),
        "clock": clock_sentence(len(events), with_time),
        "disclaimer": DISCLAIMER,
        "months": months,
        "events": events,
    }


def listings_from_frame(frame: pd.DataFrame, venues: list[dict]) -> dict:
    """Group collector rows onto LA28 venues. Test rows are dropped."""
    if frame.empty:
        return {"disclaimer": DISCLAIMER, "venues": {}}
    working = frame.copy()
    if "event_test" in working.columns:
        flag = working["event_test"].astype(str).str.lower()
        working = working.loc[~flag.isin(("true", "1"))]
    by_id = {venue["venue_id"]: venue for venue in venues}
    grouped: dict[str, list[dict]] = {}
    seen: set[str] = set()
    for record in working.to_dict(orient="records"):
        venue_id = str(record.get("venue_id") or "").strip()
        if venue_id not in by_id:
            continue
        event_id = str(record.get("event_id") or "").strip()
        if event_id and event_id in seen:
            continue
        if event_id:
            seen.add(event_id)
        parsed = _parse_day(record.get("start_local_date"))
        if parsed is None:
            continue
        latitude = _float_or_none(record.get("latitude"))
        longitude = _float_or_none(record.get("longitude"))
        if latitude is None or longitude is None:
            continue
        name = str(record.get("event_name") or "").strip()
        if not name:
            continue
        grouped.setdefault(venue_id, []).append({
            "date": parsed,
            "name": name,
            "start_time": clock_value(record.get("start_local_time")),
            "on_sale": is_on_sale(record.get("status_code")),
            "listed_name": str(record.get("ticketmaster_venue_name") or "").strip() or by_id[venue_id]["venue_name"],
            "latitude": latitude,
            "longitude": longitude,
        })
    blocks = {}
    for venue_id, rows in grouped.items():
        if rows:
            blocks[venue_id] = venue_listing_block(rows, by_id[venue_id], venues)
    return {"disclaimer": DISCLAIMER, "venues": blocks}


def _parse_day(value: object) -> date | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if len(text) < 10:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _float_or_none(value: object) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def write_listings(
    source_path: Path = SOURCE_PATH,
    summary_path: Path = SUMMARY_PATH,
    output_path: Path = OUTPUT_PATH,
) -> dict:
    frame = pd.read_csv(source_path, usecols=lambda column: column in USECOLS)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    payload = listings_from_frame(frame, summary["venues"])
    payload["generated_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    payload["source"] = "prediction_data/ticketmaster_events.csv"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


def main() -> None:
    payload = write_listings()
    venues = payload["venues"]
    print(f"venues {len(venues)}")
    for venue_id in sorted(venues):
        block = venues[venue_id]
        print(f"{venue_id} events {block['event_count']} timed {block['with_start_time']} {block['busiest']}")


if __name__ == "__main__":
    main()
