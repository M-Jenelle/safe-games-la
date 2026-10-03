#!/usr/bin/env python3
"""Add nearest-hospital context to an existing processed venue summary.

This is useful when the large raw crime extract is not present locally and a
team already has generated ``data/processed`` outputs.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUMMARY_PATH = ROOT / "data" / "processed" / "venue_summary.json"
POINTS_PATH = ROOT / "data" / "processed" / "crime_points_by_venue.json"
HOSPITALS_PATH = ROOT / "data" / "hospitals_in_LA.csv"


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return radius * 2 * math.asin(math.sqrt(a))


def main() -> None:
    summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    hospitals = []
    with HOSPITALS_PATH.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            hospitals.append(row)

    for venue in summary["venues"]:
        nearest = min(
            hospitals,
            key=lambda hospital: distance_m(
                float(venue["latitude"]),
                float(venue["longitude"]),
                float(hospital["LATITUDE"]),
                float(hospital["LONGITUDE"]),
            ),
        )
        distance = distance_m(
            float(venue["latitude"]),
            float(venue["longitude"]),
            float(nearest["LATITUDE"]),
            float(nearest["LONGITUDE"]),
        )
        venue["nearest_hospital"] = {
            "station_id": str(nearest["FACID"]),
            "station_name": nearest["FACNAME"],
            "distance_m": round(distance, 1),
            "emergency_room": nearest.get("Emergency Room?", ""),
            "hospital_type": nearest.get("FAC_TYPE_CODE", ""),
            "bed_capacity": nearest.get("CAPACITY", ""),
        }

    summary.setdefault("meta", {}).setdefault("sources", {})["hospitals"] = HOSPITALS_PATH.name
    SUMMARY_PATH.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if POINTS_PATH.exists():
        points = json.loads(POINTS_PATH.read_text(encoding="utf-8"))
        points.setdefault("meta", {}).setdefault("sources", {})["hospitals"] = HOSPITALS_PATH.name
        POINTS_PATH.write_text(json.dumps(points, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Added nearest hospitals to {len(summary['venues'])} venues")


if __name__ == "__main__":
    main()
