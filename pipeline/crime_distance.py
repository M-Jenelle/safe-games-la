"""Distance-band counts for incidents already inside each venue buffer.

Uses the same venue coordinates as the 800 m buffer. Does not rewrite the
crime files.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pipeline.crime_groups import GROUP_LABELS, crime_group
from pipeline.crime_time import GROUP_ORDER
from pipeline.geo import haversine_m

REPO_ROOT = Path(__file__).resolve().parents[1]
POINTS_PATH = REPO_ROOT / "data" / "processed" / "crime_points_by_venue.json"
SUMMARY_PATH = REPO_ROOT / "data" / "processed" / "venue_summary.json"
OUTPUT_PATH = REPO_ROOT / "data" / "processed" / "crime_by_distance.json"

BANDS = (
    ("near", "Within 200 m", 0, 200),
    ("mid", "200–400 m", 200, 400),
    ("far", "400–800 m", 400, 800),
)
DISCLAIMER = (
    "Report locations are rounded, so a point inside 200 m is not a crime at the door."
)


def band_for_meters(meters: float) -> str:
    """Band id for a distance already known to fall inside the venue buffer."""
    if meters <= 200:
        return "near"
    if meters <= 400:
        return "mid"
    return "far"


def venue_distance_block(points: list[dict], latitude: float, longitude: float) -> dict:
    """Count one venue's buffered incidents by distance band and offense group."""
    grouped: dict[str, dict[str, int]] = {band_id: {} for band_id, *_rest in BANDS}
    for point in points:
        meters = float(haversine_m(latitude, longitude, point["latitude"], point["longitude"]))
        band_id = band_for_meters(meters)
        group_id = crime_group(point.get("category"))
        bucket = grouped[band_id]
        bucket[group_id] = bucket.get(group_id, 0) + 1
    bands = []
    for band_id, label, _start, _end in BANDS:
        groups = [
            {"id": group_id, "label": GROUP_LABELS[group_id], "count": grouped[band_id][group_id]}
            for group_id in GROUP_ORDER
            if grouped[band_id].get(group_id)
        ]
        groups.sort(key=lambda item: item["count"], reverse=True)
        bands.append({
            "id": band_id,
            "label": label,
            "count": sum(item["count"] for item in groups),
            "groups": groups,
        })
    return {
        "total": len(points),
        "disclaimer": DISCLAIMER,
        "bands": bands,
    }


def distance_by_month(points: list[dict], latitude: float, longitude: float) -> dict[str, dict]:
    """Distance-band counts for each YYYY-MM that has at least one point."""
    buckets: dict[str, list[dict]] = {}
    for point in points:
        month = str(point.get("date") or "")[:7]
        if len(month) != 7 or month[4] != "-":
            continue
        buckets.setdefault(month, []).append(point)
    return {
        month: {
            "total": block["total"],
            "bands": block["bands"],
        }
        for month, rows in buckets.items()
        for block in (venue_distance_block(rows, latitude, longitude),)
    }


def build_distance(points: dict, venues: list[dict]) -> dict:
    origins = {
        str(venue["venue_id"]): (float(venue["latitude"]), float(venue["longitude"]))
        for venue in venues
        if venue.get("latitude") is not None and venue.get("longitude") is not None
    }
    built = {}
    for venue_id, block in (points.get("by_venue") or {}).items():
        origin = origins.get(str(venue_id))
        if origin is None:
            continue
        rows = block.get("points") or []
        built_block = venue_distance_block(rows, *origin)
        built_block["by_month"] = distance_by_month(rows, *origin)
        built[venue_id] = built_block
    return {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "venues": built,
    }


def write_distance(
    points_path: Path = POINTS_PATH,
    summary_path: Path = SUMMARY_PATH,
    output_path: Path = OUTPUT_PATH,
) -> dict:
    points = json.loads(points_path.read_text(encoding="utf-8"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    payload = build_distance(points, summary.get("venues") or [])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


def main() -> None:
    payload = write_distance()
    venues = payload["venues"]
    print(f"venues {len(venues)}")
    for venue_id in sorted(venues):
        block = venues[venue_id]
        counts = " ".join(f"{band['id']} {band['count']}" for band in block["bands"])
        print(f"{venue_id} total {block['total']} {counts}")


if __name__ == "__main__":
    main()
