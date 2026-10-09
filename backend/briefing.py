"""Sentences and headline fields the page used to build itself.

The numbers come from the same radius, cutoff, and permit rules as the
pipelines. The browser prints what this module returns.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

from pipeline.permit_event_days import (
    MATERIAL_DAILY_DIFFERENCE,
    MIN_EVENT_DAYS_FOR_PERCENT,
)

PERMIT_LOAD_INTRO = "One permit on the day, versus several. Both are compared with other days."
PERMIT_INTRO = (
    "A permit day is a day covered by a temporary-event permit. "
    "Other days are the rest of the months that had a permit."
)
PERMIT_NOT_MLB_NOTE = "These figures use permit days, not the MLB home-game list."
INCIDENT_NOTE = "inside the buffer, 2020–present"
WET_ROW = "Wet Day vs Dry Day"
HOT_ROW = "Hot Day vs Cooler Day"


def weather_intro(hot_f: int) -> str:
    return (
        f"A wet day had rain or snow. A hot day averaged at least {hot_f}°F. "
        "Other days are the rest of the months that had both. "
        "The count is records, 2020–present."
    )


def weather_disclaimer() -> str:
    return (
        "Records inside the 800 m buffer. "
        "This is an association, not a cause and not a forecast."
    )


def reports_through_label(cutoff: str) -> str:
    """The last report day, the day before the NIBRS cutoff."""
    last = date.fromisoformat(cutoff) - timedelta(days=1)
    return f"{last.strftime('%B')} {last.day}, {last.year}"


def circle_area_km2(radius_m: float) -> float:
    return math.pi * (float(radius_m) / 1000) ** 2


def _gap() -> str:
    return f"{MATERIAL_DAILY_DIFFERENCE:g}"


def join_names(names: list[str]) -> str:
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"


def density_hint(radius_m: float, venue_count: int, reports_through: str) -> str:
    area = f"{circle_area_km2(radius_m):.2f}"
    others = max(int(venue_count) - 1, 0)
    radius = f"{round(float(radius_m)):g}"
    return (
        f"Records per square kilometer inside the {radius} m circle, from 2020 through the latest data. "
        f"The circle covers about {area} km², so density is the count divided by that area. "
        f"The place compares this venue with the other {others}."
    )


def overlap_note(radius_m: float, names: list[str]) -> str:
    if not names:
        return ""
    radius = f"{round(float(radius_m)):g}"
    return (
        f"The {radius} m circle overlaps {join_names(names)}. "
        "An incident in the overlap is counted for each venue."
    )


def pair_overlap_note(radius_m: float, left_name: str, right_name: str) -> str:
    radius = f"{round(float(radius_m)):g}"
    return (
        f"The {radius} m circles of {left_name} and {right_name} overlap. "
        "An incident in the overlap is counted for each venue."
    )


def group_gap_note(unit: str) -> str:
    return f"Groups that differ by at least {_gap()} {unit} per day."


def group_gap_empty(unit: str) -> str:
    return f"No offense group differs by at least {_gap()} {unit} per day."


def weekday_summary(block: dict | None, count_word: str) -> str:
    """Busiest day and weekend share. Ties keep the earlier day."""
    if not block or not block.get("total"):
        return ""
    days = [day for day in (block.get("days") or []) if day]
    if not days:
        return ""
    busiest = days[0]
    for day in days[1:]:
        if int(day.get("count") or 0) > int(busiest.get("count") or 0):
            busiest = day
    total = int(block["total"])
    weekend = int(block.get("weekend_count") or 0)
    share = int(math.floor((weekend / total) * 100 + 0.5)) if total else 0
    return (
        f"{busiest.get('label')} is the busiest day, {int(busiest.get('count') or 0):,} {count_word}. "
        f"Weekend days are {weekend:,} ({share}%)."
    )


def annotate_weekday(block: dict | None, count_word: str) -> dict:
    """Copy a weekday block and attach a summary on it and each month."""
    if not block:
        return {}
    copy = dict(block)
    copy["summary"] = weekday_summary(copy, count_word)
    months = {}
    for key, scoped in (copy.get("by_month") or {}).items():
        if not isinstance(scoped, dict):
            months[key] = scoped
            continue
        scoped_copy = dict(scoped)
        scoped_copy["summary"] = weekday_summary(scoped_copy, count_word)
        months[key] = scoped_copy
    if months:
        copy["by_month"] = months
    return copy


def permit_hints() -> dict[str, str]:
    """Tooltip text. Thresholds are the permit-test constants."""
    gap = _gap()
    days = MIN_EVENT_DAYS_FOR_PERCENT
    return {
        "Permit Days vs Other Days": (
            "A permit day is a day covered by a temporary-event permit. "
            "Other days are the rest of the months that had a permit, from 2020 through 2024. "
            "Months with no permit are left out."
        ),
        "Permit Days": (
            "Days covered by at least one temporary-event permit. "
            "Several permits on the same date still count as one day."
        ),
        "Other Days": "The other days in months that had a permit. These are not every day without an event.",
        "Permit-Day Mean": "Average reports on permit days.",
        "Other-Day Mean": "Average reports on other days.",
        "Median": "The middle daily count on permit days, then the middle count on other days.",
        "Difference": (
            "Permit-day mean minus the other-day mean. "
            f"A percent is shown when there are at least {days} permit days, "
            f"the daily gap is at least {gap}, and the gap is unlikely to be chance."
        ),
        "Overall": "All comparison months from 2020 through 2024, taken together.",
        "Offense Groups": (
            "Crime groups, such as theft or assault, where the daily average on permit days "
            f"differs from other days by at least {gap} reports. The bar length is the size of that gap."
        ),
        "Permits on the Same Day": (
            "Permit days split by how many permits cover the date. "
            "One permit and several permits are each compared with other days."
        ),
        "One Permit": "Days covered by a single permit.",
        "Several Permits": "Days covered by two or more permits. Those dates are still one permit day.",
        "Days": "How many dates are in this row.",
        "Reports per Day": "Average reports on the dates in this row.",
        "Offenses per Day": (
            "Average NIBRS offenses on the dates in this row."
        ),
        "Difference vs Other Days": (
            "This row's average minus the average on other days. "
            "Other days is the baseline, so that row has no difference."
        ),
        "Home Games vs Other Days": (
            "Completed Dodgers regular-season home games, 2020 through 2024, "
            "compared with the other days in March through October of those years."
        ),
        "Game Days": "Days with a completed regular-season home game. A doubleheader still counts as one day.",
        "Other Days in Season": "The other days in March through October, 2020 through 2024. Winter days are left out.",
        "Game-Day Mean": "Average reports on home-game days.",
        "Other-Day Mean in Season": "Average reports on the other days in those baseball months.",
        "2020–2024": "All completed regular-season home games from 2020 through 2024, taken together.",
        "Game-Day Groups": (
            "Crime groups where the daily average on home-game days differs from other days in season "
            f"by at least {gap} reports. The bar length is the size of that gap."
        ),
        "Listed Events": (
            "Ticketmaster listings matched to this venue. "
            "The dates are after 2024, so they are not joined to the crime reports."
        ),
        "Month": "Calendar month of the listings. A month with no listing is still shown, so the gap stays visible.",
        "Listings": "How many Ticketmaster events fall in this month.",
        "Event": "The name Ticketmaster published for this listing.",
        "Start": "Local start time when Ticketmaster published one. Not listed means the hour was left off.",
        "Tickets": "On sale means tickets are listed for sale. Not on sale yet is not a cancellation.",
    }


def weather_hints(hot_f: int = 75) -> dict[str, str]:
    """Tooltip text for the weather tables. The percent rule matches the permit table."""
    gap = _gap()
    days = MIN_EVENT_DAYS_FOR_PERCENT
    return {
        "Weather": (
            "Daily weather at the venue pin, compared with records inside the 800 m buffer, 2020–present."
        ),
        WET_ROW: (
            "A wet day had rain or snow. "
            "Dry days are the other days in months that had both."
        ),
        HOT_ROW: (
            f"A hot day averaged at least {hot_f}°F. "
            "Cooler days are the other days in months that had both."
        ),
        "Days": "How many dates are in this side of the comparison.",
        "Other Days": "The other days in months that had both kinds of day.",
        "Mean": "Average records on the days named in this row.",
        "Other-Day Mean": "Average records on the other days in those months.",
        "Median": (
            "The middle daily count on the days in the first column, "
            "then the middle count on the other days."
        ),
        "Difference": (
            "This row's mean minus the other-day mean. "
            f"A percent is shown when there are at least {days} days, "
            f"the daily gap is at least {gap}, and the gap is unlikely to be chance."
        ),
    }


def page_copy(radius_m: float, venue_count: int, reports_through: str) -> dict:
    return {
        "density_hint": density_hint(radius_m, venue_count, reports_through),
        "incident_note": INCIDENT_NOTE,
        "default_crime_hint": (
            "Pins mark venues. Green is fewer and red is where they concentrate."
        ),
        "nibrs_crime_hint": (
            "Pins mark venues. This heatmap is NIBRS offenses from March 2024 through the latest extract. "
            "Green is fewer and red is where they concentrate."
        ),
        "pie_guide": "",
        "weekday_click": "Click a day to see its main offense groups. Click it again to clear.",
        "permit_intro": PERMIT_INTRO,
        "permit_hints": permit_hints(),
        "weather_hints": weather_hints(),
        "min_event_days": MIN_EVENT_DAYS_FOR_PERCENT,
    }


def display_for_venue(
    venue: dict,
    others: list[dict],
    venue_count: int,
    reports_through: str,
    home_games_available: bool,
) -> dict:
    radius = float(venue.get("buffer_radius_m") or 0)
    names = [item["venue_name"] for item in others]
    return {
        "density_hint": density_hint(radius, venue_count, reports_through),
        "incident_note": INCIDENT_NOTE,
        "overlap_note": overlap_note(radius, names),
        "overlap_with": {
            item["venue_id"]: pair_overlap_note(radius, venue["venue_name"], item["venue_name"])
            for item in others
        },
        "home_games_available": bool(home_games_available),
    }
