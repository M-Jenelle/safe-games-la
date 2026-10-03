#!/usr/bin/env python3
"""Build an LA County LA Metro bus-stop dataset from official GTFS data.

The GTFS feed provides stop coordinates and route relationships. ZIP codes are
assigned by intersecting the stop point with LA County's official ZIP polygon
layer, avoiding one reverse-geocoder request per stop.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

BUS_FEED_URL = "https://gitlab.com/LACMTA/gtfs_bus/-/raw/master/gtfs_bus.zip"
ZIP_LAYER_URL = (
    "https://arcgis.gis.lacounty.gov/arcgis/rest/services/LACounty_Dynamic/"
    "Administrative_Boundaries/MapServer/5/query?where=1%3D1&outFields=ZIPCODE&"
    "returnGeometry=true&f=geojson&outSR=4326"
)


def download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "safe-games-la-bus-stops/1.0"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def read_csv(feed: zipfile.ZipFile, filename: str):
    with feed.open(filename) as raw:
        yield from csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig"))


def route_class(route: dict[str, str]) -> tuple[str, str]:
    text = f"{route.get('route_long_name', '')} {route.get('route_desc', '')}".lower()
    if "rapid line" in text:
        return "rapid", "yes"
    if "metro g line" in text or "metro j line" in text:
        return "busway_rapid", "yes"
    if "express line" in text or "express" in text:
        return "express", "no"
    if "limited line" in text:
        return "limited", "no"
    if "shuttle" in text:
        return "shuttle", "no"
    return "local", "no"


def point_in_ring(point: tuple[float, float], ring: list[list[float]]) -> bool:
    x, y = point
    inside = False
    for index, (x1, y1) in enumerate(ring):
        x2, y2 = ring[index - 1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def polygon_zip(point: tuple[float, float], polygons: list[tuple[str, list, tuple[float, float, float, float]]]) -> str:
    x, y = point
    for zipcode, geometry, bounds in polygons:
        min_x, min_y, max_x, max_y = bounds
        if not (min_x <= x <= max_x and min_y <= y <= max_y):
            continue
        for polygon in geometry:
            rings = polygon
            outer = rings[0]
            outer_bounds = (min(p[0] for p in outer), min(p[1] for p in outer), max(p[0] for p in outer), max(p[1] for p in outer))
            if not (outer_bounds[0] <= x <= outer_bounds[2] and outer_bounds[1] <= y <= outer_bounds[3]):
                continue
            if point_in_ring(point, outer) and not any(point_in_ring(point, hole) for hole in rings[1:]):
                return zipcode
    return ""


def load_zip_polygons() -> list[tuple[str, list, tuple[float, float, float, float]]]:
    payload = json.loads(download(ZIP_LAYER_URL))
    polygons = []
    for feature in payload["features"]:
        geometry = feature["geometry"]
        coordinates = geometry["coordinates"]
        if geometry["type"] == "Polygon":
            coordinates = [coordinates]
        points = [point for polygon in coordinates for ring in polygon for point in ring]
        bounds = (min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points))
        polygons.append((str(feature["properties"]["ZIPCODE"]).zfill(5), coordinates, bounds))
    return polygons


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/la_metro_bus_stops.csv")
    args = parser.parse_args()

    feed = zipfile.ZipFile(io.BytesIO(download(BUS_FEED_URL)))
    route_info = {}
    for route in read_csv(feed, "routes.txt"):
        route_info[route["route_id"]] = {
            "bus_line": route.get("route_short_name", "").strip() or route.get("route_long_name", "").strip(),
            "route_long_name": route.get("route_long_name", "").strip(),
            "route_id": route["route_id"],
            "service_type": route_class(route)[0],
            "rapid_service": route_class(route)[1],
        }

    trip_route = {trip["trip_id"]: trip["route_id"] for trip in read_csv(feed, "trips.txt")}
    routes_by_stop: dict[str, set[str]] = {}
    for stop_time in read_csv(feed, "stop_times.txt"):
        route_id = trip_route.get(stop_time["trip_id"])
        if route_id in route_info:
            routes_by_stop.setdefault(stop_time["stop_id"], set()).add(route_id)

    polygons = load_zip_polygons()
    rows = []
    for stop in read_csv(feed, "stops.txt"):
        # Keep physical bus stops, not parent station records. Stops outside
        # LA County (for example, on Metro's 460 to Disneyland) are excluded.
        if stop.get("location_type") not in ("", "0", None) or stop["stop_id"] not in routes_by_stop:
            continue
        latitude, longitude = stop["stop_lat"].strip(), stop["stop_lon"].strip()
        route_rows = [route_info[route_id] for route_id in sorted(routes_by_stop[stop["stop_id"]])]
        line_names = sorted({route["bus_line"] for route in route_rows if route["bus_line"]})
        types = sorted({route["service_type"] for route in route_rows})
        rapid = "yes" if any(route["rapid_service"] == "yes" for route in route_rows) else "no"
        zipcode = polygon_zip((float(longitude), float(latitude)), polygons)
        if not zipcode:
            continue
        rows.append({
            "stop_id": stop["stop_id"],
            "stop_code": stop.get("stop_code", ""),
            "station_name": stop["stop_name"].strip(),
            "bus_line": ";".join(line_names),
            "route_count": len(line_names),
            "service_type": ";".join(types),
            "rapid_service": rapid,
            "zip_code": zipcode,
            "latitude": latitude,
            "longitude": longitude,
            "wheelchair_boarding": stop.get("wheelchair_boarding", ""),
            "service_status": "scheduled_in_current_feed",
            "source": BUS_FEED_URL,
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
