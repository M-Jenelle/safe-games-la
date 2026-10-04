"""Build a new merged crime file. Existing extracts are only read.

Reports dated before March 7, 2024 come from ``crime_points_by_venue.json``.
NIBRS offenses on or after that date come from ``data/raw/nibrs/nibrs_current.csv``.
The result is ``data/processed/crime_merged.json``.

    python -m pipeline.merge_crime
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from pipeline.crime_groups import GROUP_LABELS, crime_group
from pipeline.geo import DEFAULT_BUFFER_RADIUS_M, buffer_zone
from pipeline.loaders import LAT_MAX, LAT_MIN, LON_MAX, LON_MIN

REPO_ROOT = Path(__file__).resolve().parents[1]
POINTS_PATH = REPO_ROOT / "data" / "processed" / "crime_points_by_venue.json"
SUMMARY_PATH = REPO_ROOT / "data" / "processed" / "venue_summary.json"
NIBRS_PATH = REPO_ROOT / "data" / "raw" / "nibrs" / "nibrs_current.csv"
OUTPUT_PATH = REPO_ROOT / "data" / "processed" / "crime_merged.json"
NIBRS_START = date(2024, 3, 7)


def source_for(occurred: date | None, origin: str) -> str | None:
    """Which series a dated row belongs to. Undated rows and the overlap are left out."""
    if occurred is None:
        return None
    if origin == "legacy" and occurred < NIBRS_START:
        return "legacy"
    if origin == "nibrs" and occurred >= NIBRS_START:
        return "nibrs"
    return None


def _parse_date(value: object) -> date | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text or text.lower() == "nat":
        return None
    parsed = datetime.fromisoformat(text[:10])
    return parsed.date()


def _add(bucket: dict[str, dict[str, int]], month: str, key: str, count: int = 1) -> None:
    month_bucket = bucket.setdefault(month, {})
    month_bucket[key] = month_bucket.get(key, 0) + count


def _load_nibrs(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(
        path,
        usecols=["date_occ", "nibr_description", "hndrdth_lat", "hndrdth_lon"],
        dtype={"nibr_description": "string"},
    )
    occurred = pd.to_datetime(frame["date_occ"], errors="coerce")
    latitude = pd.to_numeric(frame["hndrdth_lat"], errors="coerce")
    longitude = pd.to_numeric(frame["hndrdth_lon"], errors="coerce")
    cutoff = pd.Timestamp(NIBRS_START)
    keep = (
        occurred.notna()
        & (occurred >= cutoff)
        & latitude.between(LAT_MIN, LAT_MAX)
        & longitude.between(LON_MIN, LON_MAX)
    )
    out = pd.DataFrame(
        {
            "occurred_at": occurred[keep],
            "category": frame.loc[keep, "nibr_description"].fillna("Unknown").str.strip(),
            "latitude": latitude[keep],
            "longitude": longitude[keep],
        }
    )
    out["category"] = out["category"].replace("", "Unknown")
    return out.reset_index(drop=True)


def _nibrs_heat(frame: pd.DataFrame) -> dict:
    if frame.empty or "occurred_at" not in frame.columns:
        return {"incident_count": 0, "points": []}
    occurred = pd.to_datetime(frame["occurred_at"], errors="coerce")
    kept = frame.loc[occurred.notna() & (occurred >= pd.Timestamp(NIBRS_START))]
    if kept.empty:
        return {"incident_count": 0, "points": []}
    counts = (
        kept.assign(
            latitude=kept["latitude"].round(4),
            longitude=kept["longitude"].round(4),
        )
        .groupby(["latitude", "longitude"])
        .size()
    )
    points = [
        {"latitude": float(lat), "longitude": float(lon), "weight": int(weight)}
        for (lat, lon), weight in counts.items()
    ]
    return {"incident_count": int(counts.sum()), "points": points}


def build_merged(
    venues: list[dict],
    legacy_points: dict[str, list[dict]],
    nibrs: pd.DataFrame,
    radius_m: float = DEFAULT_BUFFER_RADIUS_M,
) -> dict:
    venue_blocks: dict[str, dict] = {}
    for venue in venues:
        venue_id = str(venue["venue_id"])
        merged: dict[str, dict[str, int]] = {}
        for point in legacy_points.get(venue_id, []):
            occurred = _parse_date(point.get("date"))
            if source_for(occurred, "legacy") != "legacy" or occurred is None:
                continue
            _add(merged, occurred.strftime("%Y-%m"), crime_group(point.get("category")))

        nearby = buffer_zone(
            nibrs,
            float(venue["latitude"]),
            float(venue["longitude"]),
            radius_m,
        )
        by_month: dict[str, int] = {}
        by_category: dict[str, int] = {}
        categories_by_month: dict[str, dict[str, int]] = {}
        for row in nearby.itertuples(index=False):
            occurred = row.occurred_at
            if pd.isna(occurred):
                continue
            occurred_date = occurred.date() if hasattr(occurred, "date") else _parse_date(occurred)
            if source_for(occurred_date, "nibrs") != "nibrs" or occurred_date is None:
                continue
            month = occurred_date.strftime("%Y-%m")
            category = str(row.category or "Unknown")
            by_month[month] = by_month.get(month, 0) + 1
            by_category[category] = by_category.get(category, 0) + 1
            _add(categories_by_month, month, category)
            _add(merged, month, crime_group(category))

        venue_blocks[venue_id] = {
            "nibrs_count": int(sum(by_month.values())),
            "nibrs_by_month": dict(sorted(by_month.items())),
            "nibrs_by_category": dict(sorted(by_category.items(), key=lambda item: (-item[1], item[0]))),
            "nibrs_categories_by_month": {
                month: categories_by_month[month] for month in sorted(categories_by_month)
            },
            "merged_by_month": {month: merged[month] for month in sorted(merged)},
        }

    return {
        "meta": {
            "legacy_source": "data/processed/crime_points_by_venue.json",
            "legacy_through": "2024-03-06",
            "nibrs_source": "data/raw/nibrs/nibrs_current.csv",
            "nibrs_from": NIBRS_START.isoformat(),
            "buffer_radius_m": radius_m,
            "groups": GROUP_LABELS,
            "note": (
                "Reports before March 7, 2024 stay one row per LAPD report. "
                "From that date the rows are NIBRS offenses, and one case can "
                "contain more than one offense. The two are not added together "
                "inside the same month."
            ),
        },
        "nibrs_heat": _nibrs_heat(nibrs),
        "venues": venue_blocks,
    }


def main() -> None:
    summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    points = json.loads(POINTS_PATH.read_text(encoding="utf-8"))
    legacy = {
        venue_id: block.get("points", [])
        for venue_id, block in points.get("by_venue", {}).items()
    }
    venues = [
        {
            "venue_id": venue["venue_id"],
            "latitude": venue["latitude"],
            "longitude": venue["longitude"],
        }
        for venue in summary["venues"]
    ]
    radius = float(summary.get("meta", {}).get("buffer_radius_m") or DEFAULT_BUFFER_RADIUS_M)
    document = build_merged(venues, legacy, _load_nibrs(NIBRS_PATH), radius)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(document), encoding="utf-8")
    heat = document["nibrs_heat"]["incident_count"]
    print(f"Wrote {OUTPUT_PATH} with {heat:,} NIBRS offenses in the city heatmap")


if __name__ == "__main__":
    main()
