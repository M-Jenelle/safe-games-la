"""Time, distance, and permit-day rows from NIBRS offenses.

Uses offenses on or after March 7, 2024, through the latest date in the
extract. Does not rewrite the 2020–2024 crime files or the existing permit
comparison.

    python -m pipeline.nibrs_charts
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from pipeline.crime_distance import distance_by_month, venue_distance_block
from pipeline.crime_groups import crime_group
from pipeline.crime_time import time_by_month, venue_time_block
from pipeline.geo import DEFAULT_BUFFER_RADIUS_M, buffer_zone
from pipeline.merge_crime import NIBRS_PATH, NIBRS_START, _load_nibrs
from pipeline.permit_event_days import (
    GROUP_IDS,
    MONTH_NAMES,
    PERMITS_PATH,
    _calendar_days,
    _parse_day,
    _read_permits,
    expand_permit_days,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SUMMARY_PATH = REPO_ROOT / "data" / "processed" / "venue_summary.json"
OUTPUT_PATH = REPO_ROOT / "data" / "processed" / "nibrs_charts.json"
DISTANCE_DISCLAIMER = (
    "Offense locations are rounded, so a point inside 200 m is not a crime at the door."
)


def _month_span(start: date, end: date) -> str:
    return f"{MONTH_NAMES[start.month - 1]} {start.year}-{MONTH_NAMES[end.month - 1]} {end.year}"


def time_disclaimer(start: date, end: date) -> str:
    return (
        f"Uses NIBRS offenses, {_month_span(start, end)}, inside the 800m buffer.\n"
        "One case can count more than once."
    )


def permit_note(start: date, end: date) -> str:
    return (
        f"Counts are NIBRS offenses from {MONTH_NAMES[start.month - 1]} {start.day}, {start.year} "
        f"through {MONTH_NAMES[end.month - 1]} {end.day}, {end.year}. "
        "One case can include more than one offense."
    )


def window_days(event_days: set[str], start: date, end: date) -> list[str]:
    """Days in the window whose month had at least one permit-covered day."""
    start_text = start.isoformat()
    end_text = end.isoformat()
    months = {day[5:7] for day in event_days if start_text <= day <= end_text}
    if not months:
        return []
    return [day for day in _calendar_days(start, end) if day[5:7] in months]


def permit_coverage(rows: list[dict], start: date, end: date) -> dict[str, dict[str, dict]]:
    """Permit counts on each venue-day inside the NIBRS window."""
    covered: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in rows:
        venue_id = str(row.get("venue_id") or "").strip()
        event_start = _parse_day(row.get("event_start_date"))
        if not venue_id or event_start is None:
            continue
        venue_name = str(row.get("venue_name") or "").strip()
        for day in expand_permit_days(event_start, _parse_day(row.get("event_end_date"))):
            if day < start or day > end:
                continue
            slot = covered[venue_id].setdefault(day.isoformat(), {
                "venue_name": venue_name,
                "permit_count": 0,
            })
            slot["venue_name"] = slot["venue_name"] or venue_name
            slot["permit_count"] += 1
    return covered


def charts_for_frame(
    frame: pd.DataFrame,
    latitude: float,
    longitude: float,
    *,
    start: date,
    end: date,
    radius_m: float = DEFAULT_BUFFER_RADIUS_M,
) -> tuple[dict, dict, dict[str, int], dict[str, dict[str, int]]]:
    """Time pie, distance pie, and daily group counts for one venue frame."""
    nearby = buffer_zone(frame, latitude, longitude, radius_m)
    points = []
    times: dict[str, int] = {}
    totals: dict[str, int] = defaultdict(int)
    groups: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for index, row in enumerate(nearby.itertuples(index=False)):
        occurred = row.occurred_at
        if pd.isna(occurred):
            continue
        occurred_day = occurred.date()
        if occurred_day < start or occurred_day > end:
            continue
        incident_id = str(index)
        category = str(row.category or "Unknown")
        points.append({
            "incident_id": incident_id,
            "latitude": float(row.latitude),
            "longitude": float(row.longitude),
            "category": category,
            "date": occurred_day.isoformat(),
        })
        times[incident_id] = int(occurred.hour) * 100 + int(occurred.minute)
        day = occurred_day.isoformat()
        totals[day] += 1
        groups[crime_group(category)][day] += 1
    time_block = venue_time_block(points, times)
    time_block["disclaimer"] = time_disclaimer(start, end)
    time_block["by_month"] = time_by_month(points, times)
    distance_block = venue_distance_block(points, latitude, longitude)
    distance_block["disclaimer"] = DISTANCE_DISCLAIMER
    distance_block["by_month"] = distance_by_month(points, latitude, longitude)
    return time_block, distance_block, dict(totals), {group: dict(days) for group, days in groups.items()}


def permit_rows_for_venue(
    venue_id: str,
    venue_name: str,
    totals: dict[str, int],
    groups: dict[str, dict[str, int]],
    covered: dict[str, dict],
    start: date,
    end: date,
) -> list[dict]:
    event_days = set(covered)
    date_range = window_days(event_days, start, end)
    rows = []
    for day in date_range:
        slot = covered.get(day)
        counts = {group_id: groups.get(group_id, {}).get(day, 0) for group_id in GROUP_IDS}
        rows.append({
            "venue_id": venue_id,
            "venue_name": venue_name,
            "date": day,
            "incident_count": totals.get(day, 0),
            "is_permit_event_day": int(day in event_days),
            "permit_count": slot["permit_count"] if slot else 0,
            **counts,
        })
    return rows


def build_charts(
    frame: pd.DataFrame,
    venues: list[dict],
    permit_rows: list[dict],
    *,
    start: date | None = None,
    end: date | None = None,
) -> dict:
    occurred = pd.to_datetime(frame["occurred_at"], errors="coerce")
    latest = occurred.max()
    window_start = start or NIBRS_START
    window_end = end or (latest.date() if pd.notna(latest) else window_start)
    covered = permit_coverage(permit_rows, window_start, window_end)
    built = {}
    for venue in venues:
        venue_id = str(venue["venue_id"])
        latitude = venue.get("latitude")
        longitude = venue.get("longitude")
        if latitude is None or longitude is None:
            continue
        time_block, distance_block, totals, groups = charts_for_frame(
            frame,
            float(latitude),
            float(longitude),
            start=window_start,
            end=window_end,
        )
        venue_name = str(venue.get("venue_name") or "")
        built[venue_id] = {
            "time": time_block,
            "distance": distance_block,
            "permit_rows": permit_rows_for_venue(
                venue_id,
                venue_name,
                totals,
                groups,
                covered.get(venue_id, {}),
                window_start,
                window_end,
            ),
        }
    return {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "from": window_start.isoformat(),
        "through": window_end.isoformat(),
        "time_disclaimer": time_disclaimer(window_start, window_end),
        "distance_disclaimer": DISTANCE_DISCLAIMER,
        "permit_note": permit_note(window_start, window_end),
        "venues": built,
    }


def write_charts(
    nibrs_path: Path = NIBRS_PATH,
    summary_path: Path = SUMMARY_PATH,
    permits_path: Path = PERMITS_PATH,
    output_path: Path = OUTPUT_PATH,
) -> dict:
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    payload = build_charts(
        _load_nibrs(nibrs_path),
        summary.get("venues") or [],
        _read_permits(permits_path),
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return payload


def main() -> None:
    payload = write_charts()
    print(f"from {payload['from']} through {payload['through']} venues {len(payload['venues'])}")
    for venue_id in sorted(payload["venues"]):
        block = payload["venues"][venue_id]
        periods = " ".join(f"{item['id']} {item['count']}" for item in block["time"]["periods"])
        bands = " ".join(f"{item['id']} {item['count']}" for item in block["distance"]["bands"])
        print(
            f"{venue_id} offenses {block['time']['total']} {periods} | {bands} "
            f"permit-days {sum(row['is_permit_event_day'] for row in block['permit_rows'])}"
        )


if __name__ == "__main__":
    main()
