#!/usr/bin/env python3
"""Build a countywide police/sheriff station CSV from LA County GIS."""

from __future__ import annotations

import argparse
import csv
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API_URL = (
    "https://public.gis.lacounty.gov/public/rest/services/LACounty_Dynamic/"
    "LMS_Data_Public/MapServer/146/query?where=1%3D1&outFields=*"
    "&returnGeometry=true&outSR=4326&f=geojson"
)
SOURCE_URL = "https://public.gis.lacounty.gov/public/rest/services/LACounty_Dynamic/LMS_Data_Public/MapServer/146"


def fetch() -> dict:
    request = urllib.request.Request(API_URL, headers={"User-Agent": "safe-games-la-police-stations/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def agency_and_type(name: str, source: str, organization: str) -> tuple[str, str]:
    text = f"{name} {source} {organization}".lower()
    if "sheriff" in text:
        return "Los Angeles County Sheriff's Department", "sheriff"
    if "highway patrol" in text or "chp" in text:
        return "California Highway Patrol", "state_police"
    if "federal bureau" in text or "fbi" in text:
        return "Federal Bureau of Investigation", "federal_police"
    if "lapd" in text or "los angeles police" in text:
        return "Los Angeles Police Department", "municipal_police"
    if "police" in text:
        usable_org = organization and "." not in organization and organization.upper() not in {"NULL", "LA COUNTY"}
        if usable_org:
            return organization, "municipal_police"
        return name.split(" - ")[0], "municipal_police"
    return organization or "Law Enforcement", "law_enforcement"


def clean_zip(value: object) -> str:
    return str(value or "").split(".")[0].zfill(5)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/la_county_police_stations.csv")
    args = parser.parse_args()

    features = fetch()["features"]
    rows = []
    for feature in features:
        item = feature["properties"]
        point = feature.get("geometry", {}).get("coordinates", ["", ""])
        agency, station_type = agency_and_type(item.get("name", ""), item.get("source", ""), item.get("org_name", ""))
        updated = item.get("date_updated")
        updated_date = datetime.fromtimestamp(updated / 1000, timezone.utc).date().isoformat() if updated else ""
        rows.append({
            "station_id": item.get("OBJECTID", ""),
            "station_name": item.get("name", ""),
            "agency": agency,
            "station_type": station_type,
            "address": " ".join(filter(None, [item.get("addrln1"), item.get("addrln2")])),
            "city": item.get("city", ""),
            "state": item.get("state", "CA"),
            "zip_code": clean_zip(item.get("zip")),
            "latitude": point[1],
            "longitude": point[0],
            "phone": item.get("phones", ""),
            "hours": item.get("hours", ""),
            "description": item.get("description", ""),
            "website": item.get("url") or item.get("link", ""),
            "source_updated": updated_date,
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
