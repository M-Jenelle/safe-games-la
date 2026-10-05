"""Collect venue-scoped Ticketmaster events and LADBS TSE permits.

Run from the repository root:

    python scripts/collect_prediction_data.py

Ticketmaster's complete Discovery v2 event objects are written to JSON. A
smaller, analysis-friendly CSV is written beside it. LADBS data is fetched
from the City's Socrata API and retained only when its coordinates fall near
one of the venues in ``data/la28_venues.csv``.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline.loaders import load_venues as load_venue_table

VENUES_PATH = ROOT / "data" / "la28_venues.csv"
OUTPUT_DIR = ROOT / "prediction_data"
TM_EVENTS_JSON = OUTPUT_DIR / "ticketmaster_events.json"
TM_EVENTS_CSV = OUTPUT_DIR / "ticketmaster_events.csv"
TSE_JSON = OUTPUT_DIR / "ladbs_tse_permits.json"
TSE_CSV = OUTPUT_DIR / "ladbs_tse_permits.csv"

TM_API = "https://app.ticketmaster.com/discovery/v2"
TSE_API = "https://data.lacity.org/resource/8spw-3fhx.json"
EARTH_RADIUS_M = 6_371_000.0
TM_MIN_INTERVAL_SECONDS = 0.6  # Conservative: stays below 2 requests/second.
TM_DEFAULT_MAX_CALLS = 100
_tm_request_count = 0
_tm_last_request = 0.0


def load_env() -> None:
    """Load local env files without replacing variables already in the shell."""
    for path in (ROOT / ".env", ROOT / ".env.local"):
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def http_json(url: str, params: dict[str, Any] | None = None) -> Any:
    query = urlencode({key: value for key, value in (params or {}).items() if value is not None})
    request = Request(f"{url}?{query}" if query else url, headers={"User-Agent": "safe-games-la-prediction-data/1.0"})
    try:
        with urlopen(request, timeout=45) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"Could not fetch {url}: {exc}") from exc


def ticketmaster_json(
    url: str,
    params: dict[str, Any],
    max_calls: int,
) -> Any:
    """Make a throttled Ticketmaster request with a hard per-run budget."""
    global _tm_request_count, _tm_last_request
    if _tm_request_count >= max_calls:
        raise RuntimeError(
            f"Stopped before the Ticketmaster request budget ({max_calls}) was exceeded. "
            "Run again with --max-ticketmaster-calls if needed."
        )
    query = urlencode({key: value for key, value in params.items() if value is not None})
    request = Request(
        f"{url}?{query}",
        headers={"User-Agent": "safe-games-la-prediction-data/1.0"},
    )
    wait = TM_MIN_INTERVAL_SECONDS - (time.monotonic() - _tm_last_request)
    if wait > 0:
        time.sleep(wait)
    _tm_request_count += 1
    _tm_last_request = time.monotonic()
    try:
        with urlopen(request, timeout=45) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 429:
            retry_after = number(exc.headers.get("Retry-After")) if exc.headers else None
            delay = min(max(retry_after or 10.0, 10.0), 60.0)
            raise RuntimeError(
                f"Ticketmaster returned HTTP 429 after {_tm_request_count} calls. "
                f"Wait at least {delay:g} seconds before retrying."
            ) from exc
        raise RuntimeError(f"Ticketmaster request failed with HTTP {exc.code}") from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"Could not fetch Ticketmaster data: {exc}") from exc


def text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    return str(value).strip()


def number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (lat1, lon1, lat2, lon2))
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text(value).lower()).strip()


def load_venues() -> list[dict[str, Any]]:
    frame, _report = load_venue_table(VENUES_PATH)
    venues = []
    for row in frame.to_dict("records"):
        latitude = number(row.get("latitude"))
        longitude = number(row.get("longitude"))
        if latitude is None or longitude is None:
            continue
        venues.append(
            {
                "venue_id": row.get("venue_id", ""),
                "venue_name": row.get("venue_name", ""),
                "address": row.get("address", ""),
                "city": row.get("city", ""),
                "state": row.get("state", "CA"),
                "zip": row.get("zip", ""),
                "latitude": latitude,
                "longitude": longitude,
            }
        )
    if not venues:
        raise RuntimeError(f"No venues with coordinates found in {VENUES_PATH}")
    return venues


def venue_match(latitude: float | None, longitude: float | None, venues: list[dict[str, Any]], radius_m: float) -> dict[str, Any] | None:
    if latitude is None or longitude is None:
        return None
    closest = min(
        venues,
        key=lambda venue: haversine_m(latitude, longitude, venue["latitude"], venue["longitude"]),
    )
    distance = haversine_m(latitude, longitude, closest["latitude"], closest["longitude"])
    if distance > radius_m:
        return None
    return {"venue_id": closest["venue_id"], "venue_name": closest["venue_name"], "venue_distance_m": round(distance, 1)}


def discover_venue_ids(api_key: str, venue: dict[str, Any], max_calls: int) -> list[dict[str, Any]]:
    """Find Ticketmaster venues near the allow-listed LA28 venue coordinate."""
    response = ticketmaster_json(
        f"{TM_API}/venues.json",
        {
            "apikey": api_key,
            "keyword": venue["venue_name"],
            "latlong": f'{venue["latitude"]},{venue["longitude"]}',
            "radius": 2,
            "unit": "miles",
            "size": 20,
        },
        max_calls,
    )
    matches = []
    for item in response.get("_embedded", {}).get("venues", []):
        location = item.get("location") or {}
        distance = venue_match(number(location.get("latitude")), number(location.get("longitude")), [venue], 4_000)
        if distance:
            matches.append({"ticketmaster_venue_id": item.get("id"), "ticketmaster_venue": item, **distance})
    return matches


def flatten_event(event: dict[str, Any], match: dict[str, Any]) -> dict[str, Any]:
    dates = event.get("dates") or {}
    start = dates.get("start") or {}
    status = dates.get("status") or {}
    venue = (event.get("_embedded") or {}).get("venues", [{}])[0]
    classifications = event.get("classifications") or [{}]
    classification = classifications[0]
    location = venue.get("location") or {}
    return {
        "event_id": event.get("id"),
        "event_name": event.get("name"),
        "event_url": event.get("url"),
        "event_type": event.get("type"),
        "event_sub_type": event.get("subType"),
        "event_test": event.get("test"),
        "venue_id": match["venue_id"],
        "venue_name": match["venue_name"],
        "venue_distance_m": match["venue_distance_m"],
        "ticketmaster_venue_id": venue.get("id"),
        "ticketmaster_venue_name": venue.get("name"),
        "address": (venue.get("address") or {}).get("line1"),
        "city": (venue.get("city") or {}).get("name"),
        "state": (venue.get("state") or {}).get("stateCode"),
        "postal_code": (venue.get("postalCode") or ""),
        "latitude": number(location.get("latitude")),
        "longitude": number(location.get("longitude")),
        "start_local_date": start.get("localDate"),
        "start_local_time": start.get("localTime"),
        "start_date_time": start.get("dateTime"),
        "date_time_tbd": start.get("dateTBD"),
        "date_tbd": start.get("dateTBD"),
        "time_tbd": start.get("timeTBD"),
        "status_code": status.get("code"),
        "timezone": dates.get("timezone"),
        "genre": (classification.get("genre") or {}).get("name"),
        "segment": (classification.get("segment") or {}).get("name"),
        "subgenre": (classification.get("subGenre") or {}).get("name"),
        "promoter": (event.get("promoter") or {}).get("name"),
        "price_ranges": json.dumps(event.get("priceRanges", []), ensure_ascii=False),
        "images": json.dumps(event.get("images", []), ensure_ascii=False),
        "sales": json.dumps(event.get("sales", {}), ensure_ascii=False),
        "accessibility": json.dumps(event.get("accessibility", {}), ensure_ascii=False),
        "please_note": event.get("pleaseNote"),
        "info": event.get("info"),
        "raw_event_json": json.dumps(event, ensure_ascii=False, separators=(",", ":")),
    }


def collect_ticketmaster(
    venues: list[dict[str, Any]],
    api_key: str,
    start_date: str,
    end_date: str,
    max_calls: int,
) -> list[dict[str, Any]]:
    events: dict[str, dict[str, Any]] = {}
    venue_lookup: dict[str, list[dict[str, Any]]] = {}
    for venue in venues:
        matches = discover_venue_ids(api_key, venue, max_calls)
        for match in matches:
            venue_lookup.setdefault(match["ticketmaster_venue_id"], []).append(match)

    for ticketmaster_venue_id, matches in venue_lookup.items():
        response = ticketmaster_json(
            f"{TM_API}/events.json",
            {
                "apikey": api_key,
                "venueId": ticketmaster_venue_id,
                "startDateTime": start_date,
                "endDateTime": end_date,
                "size": 200,
                "sort": "date,asc",
            },
            max_calls,
        )
        for event in response.get("_embedded", {}).get("events", []):
            event_id = event.get("id")
            if not event_id:
                continue
            event_venue = ((event.get("_embedded") or {}).get("venues") or [{}])[0]
            location = event_venue.get("location") or {}
            coordinate_match = venue_match(number(location.get("latitude")), number(location.get("longitude")), venues, 1_600)
            if coordinate_match is None:
                continue
            events[event_id] = flatten_event(event, coordinate_match)
    return sorted(events.values(), key=lambda row: (row.get("start_date_time") or "", row.get("event_name") or ""))


def parse_lat_lon(value: Any) -> tuple[float | None, float | None]:
    if isinstance(value, dict):
        return number(value.get("latitude")), number(value.get("longitude"))
    match = re.search(r"(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)", text(value))
    if not match:
        return None, None
    first, second = float(match.group(1)), float(match.group(2))
    if abs(first) > 90:
        return second, first
    return first, second


def collect_tse(
    venues: list[dict[str, Any]],
    radius_m: float,
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        batch = http_json(
            TSE_API,
            {
                "$limit": 50_000,
                "$offset": offset,
                "$order": "event_start_date DESC",
                "$where": f"event_start_date >= '{start_date}T00:00:00' AND event_start_date <= '{end_date}T23:59:59'",
            },
        )
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < 50_000:
            break
        offset += len(batch)

    output = []
    for row in rows:
        event_date = text(row.get("event_start_date"))[:10]
        if event_date and not (start_date <= event_date <= end_date):
            continue
        latitude, longitude = parse_lat_lon(row.get("lat_lon"))
        match = venue_match(latitude, longitude, venues, radius_m)
        if match is None:
            address = " ".join(
                text(row.get(key))
                for key in ("address_start", "addr_frac_start", "addr_dir", "addr_name", "addr_suff", "addr_suff_dir", "zip_code")
                if text(row.get(key))
            )
            for venue in venues:
                if norm(venue["venue_name"]) in norm(row.get("event_name")) or norm(venue["venue_name"]) in norm(row.get("location")):
                    match = {"venue_id": venue["venue_id"], "venue_name": venue["venue_name"], "venue_distance_m": None}
                    break
        if match is None:
            continue
        output.append({
            **row,
            **match,
            "latitude": latitude,
            "longitude": longitude,
            "source_dataset": "LADBS Building and Safety Temporary Special Event (TSE) Permits",
        })
    return output


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("\n", encoding="utf-8")
        return
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", default="2020-01-01T00:00:00Z", help="Ticketmaster event start date in ISO-8601 UTC")
    parser.add_argument("--end-date", default="2028-12-31T23:59:59Z", help="Ticketmaster event end date in ISO-8601 UTC")
    parser.add_argument("--max-ticketmaster-calls", type=int, default=TM_DEFAULT_MAX_CALLS, help="Hard cap on Ticketmaster requests for this run")
    parser.add_argument("--permit-start-date", default="2020-01-01", help="LADBS permit start date in YYYY-MM-DD")
    parser.add_argument("--permit-end-date", default=date.today().isoformat(), help="LADBS permit end date in YYYY-MM-DD")
    parser.add_argument("--permit-radius-m", type=float, default=800, help="LADBS-to-venue match radius")
    parser.add_argument("--skip-ticketmaster", action="store_true")
    args = parser.parse_args()

    load_env()
    venues = load_venues()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if not args.skip_ticketmaster:
        api_key = os.getenv("TICKETMASTER_API_KEY", "").strip()
        if not api_key:
            raise SystemExit("Missing TICKETMASTER_API_KEY. Put it in .env.local at the repository root.")
        if args.max_ticketmaster_calls < 1:
            raise SystemExit("--max-ticketmaster-calls must be at least 1")
        ticketmaster_events = collect_ticketmaster(venues, api_key, args.start_date, args.end_date, args.max_ticketmaster_calls)
        write_json(TM_EVENTS_JSON, {"meta": {"venue_source": str(VENUES_PATH.relative_to(ROOT)), "event_start_date": args.start_date, "event_end_date": args.end_date, "venue_count": len(venues)}, "events": ticketmaster_events})
        write_csv(TM_EVENTS_CSV, ticketmaster_events)
    else:
        print("Ticketmaster collection skipped; existing Ticketmaster outputs were not changed.")

    permits = collect_tse(venues, args.permit_radius_m, args.permit_start_date, args.permit_end_date)
    write_json(TSE_JSON, {"meta": {"source": TSE_API, "venue_source": str(VENUES_PATH.relative_to(ROOT)), "permit_start_date": args.permit_start_date, "permit_end_date": args.permit_end_date, "match_radius_m": args.permit_radius_m, "venue_count": len(venues)}, "permits": permits})
    write_csv(TSE_CSV, permits)
    if not args.skip_ticketmaster:
        print(f"Ticketmaster events: {len(ticketmaster_events):,} ({_tm_request_count} API calls; max {args.max_ticketmaster_calls})")
    print(f"LADBS TSE permits matched to venues: {len(permits):,}")
    print(f"Wrote prediction data to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
