"""Time-of-day counts for incidents already inside each venue buffer.

Joins TIME OCC from the LAPD file onto ``crime_points_by_venue.json``.
Does not rewrite the crime files.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from pipeline.crime_groups import GROUP_LABELS, crime_group
from pipeline.run import find_crime_csv

REPO_ROOT = Path(__file__).resolve().parents[1]
POINTS_PATH = REPO_ROOT / "data" / "processed" / "crime_points_by_venue.json"
OUTPUT_PATH = REPO_ROOT / "data" / "processed" / "crime_time_of_day.json"

PERIODS = (
    ("night", "12am–6am", 0, 6),
    ("morning", "6am–12pm", 6, 12),
    ("afternoon", "12pm–6pm", 12, 18),
    ("evening", "6pm–12am", 18, 24),
)
GROUP_ORDER = (
    "sexual",
    "homicide",
    "robbery",
    "assault",
    "weapons",
    "vehicle",
    "burglary",
    "theft",
    "vandalism",
    "other",
)
DISCLAIMER = (
    "These hours come from the police report, 2020–2024, inside the 800 m buffer. "
    "12:00 exactly is often an unknown hour, so the 12pm–6pm slice runs a little high."
)


def classify_time(value: object) -> tuple[str | None, bool]:
    """Return the part-of-day id and whether the clock is exactly 12:00."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None, False
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None, False
    if number < 0:
        return None, False
    hour, minute = divmod(number, 100)
    if hour > 23 or minute > 59:
        return None, False
    period_id = None
    for candidate, _label, start, end in PERIODS:
        if start <= hour < end:
            period_id = candidate
            break
    return period_id, hour == 12 and minute == 0


def disclaimer_for(noon_count: int, unknown_count: int) -> str:
    text = DISCLAIMER
    if noon_count == 1:
        text += " This venue has 1 report at 12:00."
    elif noon_count:
        text += f" This venue has {noon_count:,} reports at 12:00."
    if unknown_count == 1:
        text += " 1 report has no usable hour and is left off the chart."
    elif unknown_count:
        text += f" {unknown_count:,} reports have no usable hour and are left off the chart."
    return text


def venue_time_block(points: list[dict], times: dict[str, int]) -> dict:
    """Count one venue's buffered incidents by part of day and offense group."""
    grouped: dict[str, dict[str, int]] = {period_id: {} for period_id, *_rest in PERIODS}
    noon_count = 0
    unknown_count = 0
    for point in points:
        period_id, exact_noon = classify_time(times.get(str(point.get("incident_id"))))
        if period_id is None:
            unknown_count += 1
            continue
        if exact_noon:
            noon_count += 1
        group_id = crime_group(point.get("category"))
        bucket = grouped[period_id]
        bucket[group_id] = bucket.get(group_id, 0) + 1
    periods = []
    for period_id, label, _start, _end in PERIODS:
        groups = [
            {"id": group_id, "label": GROUP_LABELS[group_id], "count": grouped[period_id][group_id]}
            for group_id in GROUP_ORDER
            if grouped[period_id].get(group_id)
        ]
        groups.sort(key=lambda item: item["count"], reverse=True)
        periods.append({
            "id": period_id,
            "label": label,
            "count": sum(item["count"] for item in groups),
            "groups": groups,
        })
    total = len(points)
    return {
        "total": total,
        "noon_count": noon_count,
        "unknown_count": unknown_count,
        "disclaimer": disclaimer_for(noon_count, unknown_count),
        "periods": periods,
    }


def load_times(path: Path) -> dict[str, int]:
    frame = pd.read_csv(path, usecols=["DR_NO", "TIME OCC"], dtype={"DR_NO": "string"})
    clock = pd.to_numeric(frame["TIME OCC"], errors="coerce")
    times: dict[str, int] = {}
    for incident_id, value in zip(frame["DR_NO"], clock, strict=True):
        if pd.isna(value) or incident_id is None:
            continue
        times[str(incident_id)] = int(value)
    return times


def build_time_of_day(points: dict, times: dict[str, int]) -> dict:
    venues = {}
    for venue_id, block in (points.get("by_venue") or {}).items():
        venues[venue_id] = venue_time_block(block.get("points") or [], times)
    return {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "venues": venues,
    }


def write_time_of_day(points_path: Path = POINTS_PATH, crime_csv: Path | None = None, output_path: Path = OUTPUT_PATH) -> dict:
    source = crime_csv or find_crime_csv(REPO_ROOT / "data")
    times = load_times(source)
    points = json.loads(points_path.read_text(encoding="utf-8"))
    payload = build_time_of_day(points, times)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


def main() -> None:
    payload = write_time_of_day()
    venues = payload["venues"]
    print(f"venues {len(venues)}")
    for venue_id in sorted(venues):
        block = venues[venue_id]
        counted = sum(period["count"] for period in block["periods"])
        print(
            f"{venue_id} total {block['total']} counted {counted} "
            f"noon {block['noon_count']} unknown {block['unknown_count']}"
        )


if __name__ == "__main__":
    main()
