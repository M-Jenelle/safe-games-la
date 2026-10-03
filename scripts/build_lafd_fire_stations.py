#!/usr/bin/env python3
"""Download the official City of Los Angeles LAFD station-location dataset."""

from __future__ import annotations

import argparse
import csv
import json
import urllib.parse
import urllib.request
from pathlib import Path

API_URL = "https://data.lacity.org/resource/rnb4-daiw.json"
SOURCE_URL = "https://data.lacity.org/Public-Safety/FireStations/rnb4-daiw"


def fetch_rows(limit: int = 5000) -> list[dict]:
    query = urllib.parse.urlencode({"$limit": limit, "$order": "fs_cd"})
    request = urllib.request.Request(
        f"{API_URL}?{query}",
        headers={"User-Agent": "safe-games-lafd-stations/1.0"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def normalize_zip(value: str) -> str:
    return str(value or "").split(".")[0].zfill(5)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/lafd_fire_stations.csv")
    args = parser.parse_args()

    rows = []
    for item in fetch_rows():
        point = item.get("the_geom", {}).get("coordinates", ["", ""])
        rows.append({
            "station_id": item.get("fs_cd", ""),
            "station_name": item.get("shp_addr", ""),
            "address": item.get("address", ""),
            "zip_code": normalize_zip(item.get("zip", "")),
            "latitude": point[1],
            "longitude": point[0],
            "department": item.get("deptname", ""),
            "source": SOURCE_URL,
        })

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(target, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {output}")


if __name__ == "__main__":
    main()
