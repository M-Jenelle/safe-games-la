#!/usr/bin/env python3
"""Build a ZIP/latitude/longitude dataset for LA Metro rail stations.

The source is LA Metro's official rail GTFS feed.  ZIP codes are resolved from
the coordinates with the U.S. Census Bureau's reverse geocoder (ZCTA5).

Examples:
  python scripts/build_la_metro_stations.py
  python scripts/build_la_metro_stations.py --input teammates.csv --name-column stop_name
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

FEED_URL = "https://gitlab.com/LACMTA/gtfs_rail/-/raw/master/gtfs_rail.zip"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"


def download_feed(url: str) -> zipfile.ZipFile:
    request = urllib.request.Request(url, headers={"User-Agent": "safe-games-la-metro-stations/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return zipfile.ZipFile(io.BytesIO(response.read()))


def read_csv(feed: zipfile.ZipFile, filename: str) -> list[dict[str, str]]:
    with feed.open(filename) as raw:
        return list(csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig")))


def reverse_zip(lat: str, lon: str) -> str:
    query = urllib.parse.urlencode({"lat": lat, "lon": lon, "format": "jsonv2", "zoom": "18"})
    request = urllib.request.Request(f"{NOMINATIM_URL}?{query}", headers={"User-Agent": "safe-games-la-metro-stations/1.0 (LA Metro station heat map)"})
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    return payload.get("address", {}).get("postcode", "").split("-")[0]


def station_rows(feed: zipfile.ZipFile) -> list[dict[str, str]]:
    stops = read_csv(feed, "stops.txt")
    routes = {row["route_id"]: row["route_short_name"] or row["route_long_name"] for row in read_csv(feed, "routes.txt")}
    trips = {row["trip_id"]: row["route_id"] for row in read_csv(feed, "trips.txt")}
    station_ids = {row["stop_id"] for row in stops if row.get("location_type") == "1"}
    parent_by_stop = {row["stop_id"]: row.get("parent_station") or row["stop_id"] for row in stops}
    lines_by_station: dict[str, set[str]] = {station_id: set() for station_id in station_ids}
    for stop_time in read_csv(feed, "stop_times.txt"):
        station = parent_by_stop.get(stop_time["stop_id"])
        route = routes.get(trips.get(stop_time["trip_id"], ""))
        if station in lines_by_station and route:
            lines_by_station[station].add(route.removeprefix("Metro ").replace(" Line", ""))

    rows = []
    for stop in stops:
        if stop.get("location_type") != "1":
            continue
        rows.append({
            "station_id": stop["stop_id"],
            "station_name": stop["stop_name"],
            "lines": ";".join(sorted(lines_by_station[stop["stop_id"]])),
            "rail_mode": "subway" if any(line in {"B", "D"} for line in lines_by_station[stop["stop_id"]]) else "light_rail",
            "service_status": "operating",
            "zip_code": "",
            "latitude": stop["stop_lat"],
            "longitude": stop["stop_lon"],
            "source": FEED_URL,
        })
    return rows


def enrich(rows: list[dict[str, str]], delay: float = 0.15) -> None:
    for index, row in enumerate(rows, 1):
        row["zip_code"] = reverse_zip(row["latitude"], row["longitude"])
        print(f"ZIP lookup {index}/{len(rows)}: {row['station_name']} -> {row['zip_code']}", file=sys.stderr)
        time.sleep(max(delay, 1.0))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/la_metro_rail_stations.csv")
    parser.add_argument("--input", help="Optional teammate CSV to enrich by station name")
    parser.add_argument("--name-column", default="station_name")
    args = parser.parse_args()

    feed = download_feed(FEED_URL)
    rows = station_rows(feed)
    enrich(rows)

    if args.input:
        with open(args.input, newline="", encoding="utf-8-sig") as source:
            original = list(csv.DictReader(source))
        by_name = {row["station_name"].casefold(): row for row in rows}
        merged = []
        for item in original:
            match = by_name.get(item.get(args.name_column, "").strip().casefold())
            merged.append({**item, **({k: match[k] for k in ("zip_code", "latitude", "longitude")} if match else {})})
        rows = merged

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0])
    with output.open("w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(target, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {output}")


if __name__ == "__main__":
    main()
