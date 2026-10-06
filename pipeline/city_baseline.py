"""Citywide LAPD rate for comparing a venue circle with the city.

The rate is every usable report in the 2020–2024 extract divided by the
City of Los Angeles land area. Venue circles are never summed into it.
"""

from __future__ import annotations

import json
from pathlib import Path

# U.S. Census Bureau QuickFacts, Los Angeles city, 2020 land area.
CITY_LAND_SQ_MI = 469.49
# International square mile, from the exact statute mile of 1,609.344 m.
SQ_MI_TO_KM2 = (1609.344 ** 2) / 1_000_000

CITY_BASELINE_NOTE = (
    "Citywide rate is every LAPD report with a usable location, 2020-2024, "
    "divided by the City of Los Angeles land area "
    "(U.S. Census Bureau 2020, 469.49 square miles). "
    "Venue circles are not added into that total."
)


def city_land_area_km2() -> float:
    return CITY_LAND_SQ_MI * SQ_MI_TO_KM2


def city_rate(incident_count: int) -> float:
    """Reports per km², rounded like the venue densities."""
    area = city_land_area_km2()
    if not area:
        return 0.0
    return round(incident_count / area, 1)


def compared_with_city(venue_rate: float, citywide_rate: float, period: str = "2020–2024") -> dict:
    """Say whether one venue density sits above, below, or near the city rate."""
    ratio = (venue_rate / citywide_rate) if citywide_rate else 0.0
    city_label = f"{citywide_rate:,.1f}"
    if ratio >= 1.05:
        relation = "above"
        shown = f"{ratio:.1f}".rstrip("0").rstrip(".")
        value = f"{shown}×"
        caption = f"above {city_label}/km², {period}"
        summary = f"Above the city. {shown}× the citywide rate of {city_label} reports/km²."
    elif ratio <= 0.95:
        relation = "below"
        percent = f"{ratio * 100:.1f}".rstrip("0").rstrip(".")
        value = f"{percent}%"
        caption = f"of {city_label}/km², {period}"
        summary = f"Below the city. {percent}% of the citywide rate of {city_label} reports/km²."
    else:
        relation = "near"
        value = "Same"
        caption = f"as {city_label}/km², {period}"
        summary = f"About the citywide rate of {city_label} reports/km²."
    return {
        "ratio": round(ratio, 3),
        "relation": relation,
        "value": value,
        "caption": caption,
        "summary": summary,
    }


def baseline_document(incident_count: int) -> dict:
    rate = city_rate(incident_count)
    return {
        "incident_count": int(incident_count),
        "land_area_sq_mi": CITY_LAND_SQ_MI,
        "land_area_km2": round(city_land_area_km2(), 2),
        "crime_per_km2": rate,
        "area_name": "City of Los Angeles",
        "area_source": "U.S. Census Bureau, 2020 land area",
        "period": "2020-2024",
        "note": CITY_BASELINE_NOTE,
    }


def nibrs_document(offense_count: int, start: str, end: str) -> dict:
    """Offenses per km² for March 7, 2024 through the latest extract date."""
    rate = city_rate(offense_count)
    return {
        "offense_count": int(offense_count),
        "crime_per_km2": rate,
        "start": start,
        "end": end,
        "period": "Mar 2024–present",
        "note": (
            "Citywide rate is every NIBRS offense with a usable location from March 7, 2024 "
            f"through {end}, divided by the same city land area. "
            "One case can include more than one offense, so this is not the same count as a 2020-2024 report. "
            "Venue circles are not added into that total."
        ),
    }


def present_document(legacy_count: int, nibrs: dict) -> dict:
    """Reports through March 6, 2024, then NIBRS offenses through the extract."""
    total = int(legacy_count) + int(nibrs["offense_count"])
    end = nibrs["end"]
    return {
        "count": total,
        "legacy_count": int(legacy_count),
        "nibrs_offense_count": int(nibrs["offense_count"]),
        "crime_per_km2": city_rate(total),
        "through": end,
        "period": "2020–present",
        "note": (
            "2020-present counts LAPD reports through March 6, 2024, then NIBRS offenses "
            f"from March 7, 2024 through {end}. One NIBRS case can include more than one offense. "
            "The city rate divides that combined count by the City of Los Angeles land area "
            "(U.S. Census Bureau 2020, 469.49 square miles). Venue circles are not added into that total."
        ),
    }


def write_baseline(
    incident_count: int,
    path: Path,
    nibrs: dict | None = None,
    present: dict | None = None,
) -> dict:
    document = baseline_document(incident_count)
    if nibrs:
        document["nibrs"] = nibrs
    if present:
        document["present"] = present
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return document


def main() -> None:
    import pandas as pd

    from pipeline.loaders import load_crime
    from pipeline.merge_crime import NIBRS_PATH, NIBRS_START, _load_nibrs
    from pipeline.run import find_crime_csv, resolve_data_dir

    root = Path(__file__).resolve().parents[1]
    frame, report = load_crime(find_crime_csv(resolve_data_dir(root)))
    cutoff = pd.Timestamp(NIBRS_START)
    occurred = frame["occurred_at"]
    legacy_count = int((occurred.notna() & (occurred < cutoff)).sum())
    nibrs_frame = _load_nibrs(NIBRS_PATH)
    nibrs_dates = nibrs_frame["occurred_at"]
    nibrs = nibrs_document(
        len(nibrs_frame),
        nibrs_dates.min().date().isoformat(),
        nibrs_dates.max().date().isoformat(),
    )
    present = present_document(legacy_count, nibrs)
    document = write_baseline(
        report.rows_out,
        root / "data" / "processed" / "city_baseline.json",
        nibrs,
        present,
    )
    print(
        f"{document['incident_count']} reports, "
        f"{document['crime_per_km2']} per km2, "
        f"land area {document['land_area_km2']} km2"
    )
    print(
        f"{nibrs['offense_count']} offenses, "
        f"{nibrs['crime_per_km2']} per km2, "
        f"{nibrs['start']} through {nibrs['end']}"
    )
    print(
        f"{present['count']} combined, "
        f"{present['legacy_count']} reports before the cutoff, "
        f"{present['crime_per_km2']} per km2"
    )


if __name__ == "__main__":
    main()
