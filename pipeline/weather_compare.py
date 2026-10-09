"""Wet-day and hot-day comparisons for one venue.

The daily count is the same 2020–present series as the other venue charts:
reports before March 7, 2024, then NIBRS offenses. A comparison keeps only
months that contain both kinds of day. It does not rewrite the crime files.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

from pipeline.permit_event_days import (
    MIN_EVENT_DAYS_FOR_PERCENT,
    OTHER_DAY_MEAN_FOR_PERCENT,
    association,
)
from pipeline.weather import JOINED_PATH, NIBRS_START

HOT_MEAN_F = 75.0
_joined: dict[str, list[dict]] | None = None
_joined_mtime: float | None = None
_report_days: dict[str, dict[str, int]] | None = None


def load_joined_days(path: Path = JOINED_PATH) -> dict[str, list[dict]]:
    """Venue-day weather rows that have a crime count. Empty when the file is absent."""
    global _joined, _joined_mtime
    if not path.exists():
        _joined = {}
        _joined_mtime = None
        return _joined
    mtime = path.stat().st_mtime
    if _joined is not None and mtime == _joined_mtime:
        return _joined
    grouped: dict[str, list[dict]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            venue_id = row.get("venue_id") or ""
            if not venue_id:
                continue
            if row.get("wet_day") not in ("0", "1") or not row.get("temp_f_mean"):
                continue
            count_text = row.get("incident_count") or ""
            grouped.setdefault(venue_id, []).append({
                "date": row["date"],
                "count_source": row.get("count_source") or "",
                "incident_count": int(count_text) if count_text else 0,
                "wet_day": row["wet_day"] == "1",
                "temp_f_mean": float(row["temp_f_mean"]),
            })
    _joined = grouped
    _joined_mtime = mtime
    return _joined


def _public_stats(stats: dict) -> dict:
    shown = bool(
        stats["event_day_count"] >= MIN_EVENT_DAYS_FOR_PERCENT
        and stats["significant"]
        and stats["percent_useful"]
        and float(stats["other_day_mean"]) >= OTHER_DAY_MEAN_FOR_PERCENT
    )
    return {
        "event_day_count": stats["event_day_count"],
        "other_day_count": stats["other_day_count"],
        "event_day_mean": stats["event_day_mean"],
        "other_day_mean": stats["other_day_mean"],
        "event_day_median": stats["event_day_median"],
        "other_day_median": stats["other_day_median"],
        "absolute_difference": stats["absolute_difference"],
        "lift_pct": stats["lift_pct"] if shown else None,
        "percent_shown": shown,
    }


def _mixed_months(rows: list[dict], predicate) -> tuple[list[int], list[int], int]:
    """Counts from months that contain both kinds of day."""
    months: dict[str, dict[str, list[int]]] = defaultdict(lambda: {"event": [], "other": []})
    for row in rows:
        side = "event" if predicate(row) else "other"
        months[row["date"][:7]][side].append(row["incident_count"])
    event: list[int] = []
    other: list[int] = []
    used = 0
    for bucket in months.values():
        if bucket["event"] and bucket["other"]:
            used += 1
            event.extend(bucket["event"])
            other.extend(bucket["other"])
    return event, other, used


def _stats(event: list[int], other: list[int]) -> dict:
    counts: dict[str, int] = {}
    days: list[str] = []
    event_days: set[str] = set()
    for index, count in enumerate(event):
        key = f"e{index}"
        counts[key] = count
        days.append(key)
        event_days.add(key)
    for index, count in enumerate(other):
        key = f"o{index}"
        counts[key] = count
        days.append(key)
    return association(counts, event_days, days)


def compare_rows(rows: list[dict], predicate, *, empty_note: str) -> dict:
    """One series, already filtered to report or NIBRS."""
    if not rows:
        return {"available": False, "note": empty_note, "month_count": 0}
    event, other, used = _mixed_months(rows, predicate)
    if used == 0 or len(event) < 2 or len(other) < 2:
        return {
            "available": False,
            "note": "No month had both kinds of day.",
            "month_count": used,
        }
    return {
        "available": True,
        "note": "",
        "month_count": used,
        **_public_stats(_stats(event, other)),
    }


def report_daily_counts() -> dict[str, dict[str, int]]:
    """Report totals inside each buffer for dates before the NIBRS start."""
    global _report_days
    if _report_days is not None:
        return _report_days
    from pipeline.permit_event_days import POINTS_PATH, index_crime

    pair, _names = index_crime(POINTS_PATH)
    cutoff = NIBRS_START.isoformat()
    _report_days = {
        venue_id: {day: count for day, count in days.items() if day < cutoff}
        for venue_id, days in pair[0].items()
    }
    return _report_days


def present_days(rows: list[dict], report_by_day: dict[str, int] | None = None) -> list[dict]:
    """One count per day, reports before the cutoff and NIBRS from that date on.

    When report totals are supplied, a day before the cutoff with no report is 0.
    Without them, the count already stored on a report row is kept.
    """
    cutoff = NIBRS_START.isoformat()
    nibrs_last = max((row["date"] for row in rows if row.get("count_source") == "nibrs"), default="")
    built = []
    for row in rows:
        day = row["date"]
        if day < cutoff:
            if report_by_day is None:
                if row.get("count_source") != "report":
                    continue
                count = int(row["incident_count"])
            else:
                count = int(report_by_day.get(day, 0))
        elif nibrs_last and day <= nibrs_last and row.get("count_source") == "nibrs":
            count = int(row["incident_count"])
        else:
            continue
        built.append({**row, "incident_count": count})
    return built


def series_rows(
    rows: list[dict],
    predicate,
    label: str,
    report_by_day: dict[str, int] | None = None,
) -> list[dict]:
    """One weather split, labeled by the comparison rather than the year span."""
    present = present_days(rows, report_by_day)
    return [{
        "source": "present",
        "label": label,
        **compare_rows(present, predicate, empty_note="No days in this series."),
    }]


def nibrs_end(rows_by_venue: dict[str, list[dict]] | None = None) -> str:
    """Last NIBRS day in the joined file."""
    found = rows_by_venue if rows_by_venue is not None else load_joined_days()
    latest = ""
    for rows in found.values():
        for row in rows:
            if row["count_source"] == "nibrs" and row["date"] > latest:
                latest = row["date"]
    return latest


def comparison_payload(rows: list[dict], report_by_day: dict[str, int] | None = None) -> dict:
    """Wet and hot comparisons for the rows of one venue."""
    return {
        "hot_f": int(HOT_MEAN_F),
        "nibrs_start": NIBRS_START.isoformat(),
        "comparisons": [
            {
                "id": "wet",
                "rows": series_rows(rows, lambda row: row["wet_day"], "Wet Day vs Dry Day", report_by_day),
            },
            {
                "id": "hot",
                "rows": series_rows(
                    rows,
                    lambda row: row["temp_f_mean"] >= HOT_MEAN_F,
                    "Hot Day vs Cooler Day",
                    report_by_day,
                ),
            },
        ],
    }


def clear_joined_cache() -> None:
    """Drop the file cache. Tests that swap the path call this."""
    global _joined, _joined_mtime, _report_days
    _joined = None
    _joined_mtime = None
    _report_days = None
