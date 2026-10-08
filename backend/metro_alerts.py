"""Live Metro alerts for lines recorded near one venue.

Swiftly serves the GTFS-realtime alert feeds and requires an API key.
Without that key, or when a feed does not answer, no notice is filled in.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

_LA = ZoneInfo("America/Los_Angeles")
_ROOT = Path(__file__).resolve().parents[1]
_ROUTES = _ROOT / "data" / "processed" / "metro_route_ids.json"
_RAIL_FEED = "https://api.goswift.ly/real-time/lametro-rail/gtfs-rt-alerts"
_BUS_FEED = "https://api.goswift.ly/real-time/lametro/gtfs-rt-alerts"
_CAVEAT = "A current Metro service notice. This is not a crime finding and not a forecast."


def swiftly_key() -> str:
    """Key from the process, then from .env. Never logged."""
    existing = os.environ.get("SWIFTLY_API_KEY", "").strip()
    if existing:
        return existing
    path = _ROOT / ".env"
    if not path.is_file():
        return ""
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "SWIFTLY_API_KEY":
            return value.strip().strip('"').strip("'")
    return ""


def route_map() -> dict:
    return json.loads(_ROUTES.read_text(encoding="utf-8"))


def venue_routes(venue: dict) -> dict[str, str]:
    """GTFS route id, and the short name, mapped to the label we can show."""
    mapping = route_map()
    chosen: dict[str, str] = {}
    for station in (venue.get("rail_stations_nearby") or {}).get("stations") or []:
        for letter in str(station.get("lines") or "").split(";"):
            letter = letter.strip()
            route_id = (mapping.get("rail") or {}).get(letter)
            if route_id:
                chosen[route_id] = f"{letter} Line"
                chosen[letter] = f"{letter} Line"
    recorded = {str(line).strip() for line in (venue.get("bus_stops_nearby") or {}).get("lines") or []}
    for label, route_ids in (mapping.get("bus") or {}).items():
        if label not in recorded:
            continue
        chosen[label] = label
        for route_id in route_ids:
            chosen[str(route_id)] = label
    return chosen


def _translation(block: dict | None) -> str:
    if not isinstance(block, dict):
        return ""
    translations = block.get("translation") or []
    for item in translations:
        if item.get("language") in (None, "", "en", "en-US"):
            return str(item.get("text") or "").strip()
    if translations:
        return str(translations[0].get("text") or "").strip()
    return ""


def _active(alert: dict, stamp: float) -> bool:
    periods = alert.get("activePeriod") or alert.get("active_period") or []
    if not periods:
        return True
    for period in periods:
        start = int(period.get("start") or 0)
        end = period.get("end")
        if start and stamp < start:
            continue
        if end and stamp > int(end):
            continue
        return True
    return False


def parse_feed(payload: dict, now: datetime | None = None) -> list[dict]:
    """Alerts that name a route and are active. Both JSON field styles are accepted."""
    moment = now or datetime.now(timezone.utc)
    stamp = moment.timestamp()
    parsed = []
    for entity in payload.get("entity") or []:
        alert = entity.get("alert") or {}
        if not _active(alert, stamp):
            continue
        header = _translation(alert.get("headerText") or alert.get("header_text"))
        if not header:
            continue
        routes = []
        for item in alert.get("informedEntity") or alert.get("informed_entity") or []:
            route = str(item.get("routeId") or item.get("route_id") or "").strip()
            if route and route not in routes:
                routes.append(route)
        if routes:
            parsed.append({"route_ids": routes, "header": header})
    return parsed


def fetch_alerts(key: str) -> list[dict] | None:
    """Rail and bus alert feeds. None when neither feed returns JSON."""
    headers = {"Authorization": key, "Accept": "application/json"}
    found: list[dict] = []
    answered = False
    try:
        with httpx.Client(timeout=httpx.Timeout(8.0, connect=3.0)) as client:
            for url in (_RAIL_FEED, _BUS_FEED):
                try:
                    response = client.get(url, params={"format": "json"}, headers=headers)
                    response.raise_for_status()
                    payload = response.json()
                except (httpx.HTTPError, ValueError, TypeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                answered = True
                found.extend(parse_feed(payload))
    except httpx.HTTPError:
        return None
    return found if answered else None


def metro_reply(venue: dict) -> dict:
    name = venue["venue_name"]
    routes = venue_routes(venue)
    if not routes:
        return {
            "status": "answered",
            "answer": f"No Metro rail station or bus line is recorded near {name}, so no alert was matched.",
            "caveat": _CAVEAT,
            "confidence": {"kind": "live", "text": "No lines recorded."},
            "tool": "metro_alerts",
            "table": None,
        }
    key = swiftly_key()
    if not key:
        return {
            "status": "answered",
            "answer": "Live Metro alerts are not connected. No alert was filled in.",
            "caveat": _CAVEAT,
            "confidence": {"kind": "live", "text": "Feed not connected."},
            "tool": "metro_alerts",
            "table": None,
        }
    alerts = fetch_alerts(key)
    fetched = datetime.now(_LA).strftime("%Y-%m-%dT%H:%M")
    live = {"kind": "live", "text": f"Live feed, fetched at {fetched} America/Los_Angeles."}
    if alerts is None:
        return {
            "status": "answered",
            "answer": "The Metro alert feed did not answer. No alert was filled in.",
            "caveat": _CAVEAT,
            "confidence": {"kind": "live", "text": "Feed unavailable."},
            "tool": "metro_alerts",
            "table": None,
        }
    rows = []
    seen = set()
    for alert in alerts:
        labels = list(dict.fromkeys(routes[route] for route in alert["route_ids"] if route in routes))
        if not labels:
            continue
        header = " ".join(alert["header"].split())
        if len(header) > 180:
            header = header[:177].rstrip() + "..."
        identity = (tuple(labels), header)
        if identity in seen:
            continue
        seen.add(identity)
        rows.append([", ".join(labels), header])
        if len(rows) == 3:
            break
    if not rows:
        answer = f"No current Metro alert matches the lines recorded near {name}."
        table = None
    else:
        answer = f"Current Metro alerts on lines recorded near {name}."
        table = {"columns": ["Line", "Notice"], "rows": rows}
    return {
        "status": "answered",
        "answer": answer,
        "caveat": _CAVEAT,
        "confidence": live,
        "tool": "metro_alerts",
        "table": table,
    }
