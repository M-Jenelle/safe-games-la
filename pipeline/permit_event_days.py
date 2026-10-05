"""Join LADBS temporary-event permits to day-level crime near each venue.

Permits are a weaker event signal than a published game schedule. This writes
the joined days and a per-venue association summary. It does not change the
crime files, the Dodger MLB file, or the app.
"""

from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median

from pipeline.crime_groups import CRIME_GROUPS, GROUP_LABELS, crime_group

REPO_ROOT = Path(__file__).resolve().parents[1]
PERMITS_PATH = REPO_ROOT / "prediction_data" / "ladbs_tse_permits.csv"
POINTS_PATH = REPO_ROOT / "data" / "processed" / "crime_points_by_venue.json"
DAYS_PATH = REPO_ROOT / "data" / "processed" / "permit_event_days.csv"
SUMMARY_PATH = REPO_ROOT / "data" / "processed" / "permit_event_lift.json"

PERIOD_START = date(2020, 1, 1)
PERIOD_END = date(2024, 12, 31)
ALPHA = 0.05
MATERIAL_DAILY_DIFFERENCE = 0.05
DISCLAIMER = (
    "LADBS temporary special event permits are a weaker event signal than a "
    "published game schedule. Several permits can belong to one event. Each "
    "permit is matched to the nearest Olympic venue within 800 m, including a "
    "few name matches without coordinates. Downtown and Exposition Park buffers "
    "overlap, so the same street report can appear under more than one venue. "
    "The comparison is permit-covered days versus other days in the same months, "
    "using LAPD reports from 2020–2024 inside the existing 800 m buffer. "
    "This is an association, not a cause, and not a prediction of a future date. "
    "Dodger Stadium rows in this file use permits only, not the MLB home-game list."
)
GROUP_IDS = [group_id for group_id, _label, _needles in CRIME_GROUPS] + ["other"]


def _parse_day(value) -> date | None:
    text = str(value or "")[:10]
    if len(text) != 10 or text[4] != "-" or text[7] != "-":
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def expand_permit_days(start: date, end: date | None) -> list[date]:
    """Inclusive calendar days. A missing or earlier end stays on the start date."""
    if end is None or end < start:
        end = start
    days = []
    cursor = start
    while cursor <= end:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def index_permits(rows: list[dict]) -> tuple[dict, dict, int]:
    """Venue/date coverage for the crime window, plus later permit days."""
    historical: dict[str, dict[str, dict]] = defaultdict(dict)
    upcoming: dict[str, dict[str, dict]] = defaultdict(dict)
    historical_rows: dict[str, int] = defaultdict(int)
    name_only = 0
    for row in rows:
        venue_id = str(row.get("venue_id") or "").strip()
        if not venue_id:
            continue
        start = _parse_day(row.get("event_start_date"))
        if start is None:
            continue
        if not str(row.get("venue_distance_m") or "").strip():
            name_only += 1
        end = _parse_day(row.get("event_end_date"))
        name = " ".join(str(row.get("event_name") or "").split())
        venue_name = str(row.get("venue_name") or "").strip()
        days = expand_permit_days(start, end)
        if any(PERIOD_START <= day <= PERIOD_END for day in days):
            historical_rows[venue_id] += 1
        for day in days:
            bucket = historical if PERIOD_START <= day <= PERIOD_END else upcoming if day > PERIOD_END else None
            if bucket is None:
                continue
            slot = bucket[venue_id].setdefault(day.isoformat(), {
                "venue_name": venue_name,
                "permit_count": 0,
                "event_names": [],
            })
            slot["venue_name"] = slot["venue_name"] or venue_name
            slot["permit_count"] += 1
            if name and name not in slot["event_names"]:
                slot["event_names"].append(name)
    return historical, upcoming, name_only, historical_rows


def index_crime(points_path) -> tuple[dict, dict]:
    """Daily totals and coarse-group counts already inside each venue buffer."""
    data = json.loads(Path(points_path).read_text(encoding="utf-8"))
    totals: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    groups: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    names = {}
    for venue_id, block in data.get("by_venue", {}).items():
        names[venue_id] = block.get("venue_name") or ""
        for point in block.get("points") or []:
            day = str(point.get("date") or "")[:10]
            if _parse_day(day) is None:
                continue
            totals[venue_id][day] += 1
            groups[venue_id][crime_group(point.get("category"))][day] += 1
    return (
        {venue_id: dict(days) for venue_id, days in totals.items()},
        {venue_id: {group: dict(days) for group, days in group_days.items()} for venue_id, group_days in groups.items()},
    ), names


def _calendar_days(start: date, end: date) -> list[str]:
    days = []
    cursor = start
    while cursor <= end:
        days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return days


def comparison_days(event_days: set[str]) -> list[str]:
    """2020–2024 days whose month had at least one permit-covered day."""
    months = {day[5:7] for day in event_days if PERIOD_START.isoformat() <= day <= PERIOD_END.isoformat()}
    if not months:
        return []
    return [day for day in _calendar_days(PERIOD_START, PERIOD_END) if day[5:7] in months]


def _mann_whitney_p(event_counts: list[int], other_counts: list[int]):
    if len(event_counts) < 2 or len(other_counts) < 2:
        return None
    from scipy.stats import mannwhitneyu

    try:
        result = mannwhitneyu(event_counts, other_counts, alternative="two-sided")
    except ValueError:
        return None
    p_value = float(result.pvalue)
    if math.isnan(p_value):
        return None
    if p_value != 0 and p_value < 1e-6:
        return p_value
    return round(p_value, 6)


def association(counts_by_day: dict[str, int], event_days: set[str], date_range: list[str]) -> dict:
    event_counts, other_counts = [], []
    for day in date_range:
        count = counts_by_day.get(day, 0)
        (event_counts if day in event_days else other_counts).append(count)
    event_mean = mean(event_counts) if event_counts else 0
    other_mean = mean(other_counts) if other_counts else 0
    lift_pct = ((event_mean - other_mean) / other_mean * 100) if other_mean else None
    rounded_lift = round(lift_pct, 1) if lift_pct is not None else None
    difference = round(event_mean - other_mean, 2)
    p_value = _mann_whitney_p(event_counts, other_counts)
    significant = bool(p_value is not None and p_value < ALPHA)
    return {
        "event_day_mean": round(event_mean, 2),
        "other_day_mean": round(other_mean, 2),
        "event_day_median": median(event_counts) if event_counts else 0,
        "other_day_median": median(other_counts) if other_counts else 0,
        "absolute_difference": difference,
        "lift_pct": rounded_lift if abs(difference) >= MATERIAL_DAILY_DIFFERENCE else None,
        "unsuppressed_lift_pct": rounded_lift,
        "event_day_count": len(event_counts),
        "other_day_count": len(other_counts),
        "p_value": p_value,
        "significant": significant,
        "percent_useful": abs(difference) >= MATERIAL_DAILY_DIFFERENCE and rounded_lift is not None,
    }


def build_merge(permit_rows: list[dict], crime_totals: dict, crime_groups: dict, venue_names: dict) -> tuple[list[dict], dict]:
    historical, upcoming, name_only, historical_rows = index_permits(permit_rows)
    day_rows = []
    venues = []
    for venue_id in sorted(historical):
        covered = historical[venue_id]
        event_days = set(covered)
        date_range = comparison_days(event_days)
        venue_name = next((slot["venue_name"] for slot in covered.values() if slot["venue_name"]), "") or venue_names.get(venue_id, "")
        totals = crime_totals.get(venue_id, {})
        overall = association(totals, event_days, date_range)
        by_group = []
        for group_id in GROUP_IDS:
            by_group.append({
                "group": group_id,
                "label": GROUP_LABELS[group_id],
                **association(crime_groups.get(venue_id, {}).get(group_id, {}), event_days, date_range),
            })
        permit_rows_for_venue = historical_rows[venue_id]
        for day in date_range:
            slot = covered.get(day)
            counts = {
                group_id: crime_groups.get(venue_id, {}).get(group_id, {}).get(day, 0)
                for group_id in GROUP_IDS
            }
            day_rows.append({
                "venue_id": venue_id,
                "venue_name": venue_name,
                "date": day,
                "incident_count": totals.get(day, 0),
                "is_permit_event_day": int(day in event_days),
                "permit_count": slot["permit_count"] if slot else 0,
                "event_names": " | ".join(slot["event_names"]) if slot else "",
                **counts,
            })
        later = []
        for day in sorted(upcoming.get(venue_id, {})):
            slot = upcoming[venue_id][day]
            later.append({
                "date": day,
                "permit_count": slot["permit_count"],
                "event_names": slot["event_names"],
                "historical_lift_pct": overall["lift_pct"],
            })
        venues.append({
            "venue_id": venue_id,
            "venue_name": venue_name,
            "permit_rows_in_crime_window": permit_rows_for_venue,
            "thin_sample": overall["event_day_count"] < 20,
            **overall,
            "by_group": by_group,
            "upcoming_permit_days": later,
        })
    summary = {
        "meta": {
            "kind": "permit_event_association",
            "disclaimer": DISCLAIMER,
            "crime_period": f"{PERIOD_START.isoformat()} to {PERIOD_END.isoformat()}",
            "buffer_radius_m": 800,
            "comparison": "months_with_a_permit_covered_day",
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "sources": {
                "permits": "prediction_data/ladbs_tse_permits.csv",
                "crime": "data/processed/crime_points_by_venue.json",
            },
            "name_only_permit_rows": name_only,
            "venue_count": len(venues),
            "day_rows": len(day_rows),
        },
        "venues": venues,
    }
    day_rows.sort(key=lambda row: (row["venue_id"], row["date"]))
    return day_rows, summary


def _read_permits(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_outputs(day_rows: list[dict], summary: dict) -> None:
    DAYS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "venue_id", "venue_name", "date", "incident_count", "is_permit_event_day",
        "permit_count", "event_names", *GROUP_IDS,
    ]
    with DAYS_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(day_rows)
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")


def main() -> None:
    (totals, groups), names = index_crime(POINTS_PATH)
    day_rows, summary = build_merge(_read_permits(PERMITS_PATH), totals, groups, names)
    write_outputs(day_rows, summary)
    print(f"Wrote {DAYS_PATH} ({len(day_rows):,} venue-days)")
    print(f"Wrote {SUMMARY_PATH}")
    print(f"{'venue':<42} {'event days':>10} {'other days':>10} {'event mean':>10} {'other mean':>10} {'lift %':>8} {'p':>10}")
    for venue in summary["venues"]:
        lift = "-" if venue["lift_pct"] is None else f"{venue['lift_pct']:.1f}"
        p_value = "-" if venue["p_value"] is None else f"{venue['p_value']:.4g}"
        print(
            f"{venue['venue_name'][:42]:<42} {venue['event_day_count']:>10} "
            f"{venue['other_day_count']:>10} {venue['event_day_mean']:>10} "
            f"{venue['other_day_mean']:>10} {lift:>8} {p_value:>10}"
        )


if __name__ == "__main__":
    main()
