"""Reported records around a station, stop, or facility.

The chat tables only hold crime inside venue circles. This uses the city
heatmap cells, so a fire station far from a venue is included. A cell is
about 11 m across. The count is records near the pin, not crimes on a train
or inside the building.
"""

from __future__ import annotations

import math
import re

import numpy as np

from pipeline.crime_groups import CRIME_GROUPS
from pipeline.geo import haversine_m

RADIUS_M = 800.0
AREA_KM2 = math.pi * (RADIUS_M / 1000.0) ** 2
_KINDS = {
    "rail": "rail",
    "station": "rail",
    "stations": "rail",
    "metro": "rail",
    "train": "rail",
    "bus": "bus",
    "stop": "bus",
    "stops": "bus",
    "fire": "fire",
    "police": "police",
    "hospital": "hospital",
    "hospitals": "hospital",
}
_GROUPS = {group_id for group_id, _label, _needles in CRIME_GROUPS}
_GROUP_ALIASES = {group_id: group_id for group_id in _GROUPS}
_GROUP_ALIASES.update({
    "weapon": "weapons",
    "firearm": "weapons",
    "firearms": "weapons",
    "gun": "weapons",
    "guns": "weapons",
    "pickpocket": "theft",
    "pickpockets": "theft",
    "auto": "vehicle",
    "car": "vehicle",
})
_UNSUPPORTED = {
    "narcotics": "Narcotics is not one of the nine offense groups. Use crime_detail topic drugs for NIBRS codes 35A and 35B.",
    "drugs": "Narcotics is not one of the nine offense groups. Use crime_detail topic drugs for NIBRS codes 35A and 35B.",
    "drug": "Narcotics is not one of the nine offense groups. Use crime_detail topic drugs for NIBRS codes 35A and 35B.",
    "scam": "Scams are not one of the nine offense groups. Use crime_detail topic bunco for the LAPD bunco wording.",
    "scams": "Scams are not one of the nine offense groups. Use crime_detail topic bunco for the LAPD bunco wording.",
    "society": "Crimes against society is not one of the nine offense groups. Drug codes are crime_detail topic drugs.",
}
_NOTE = (
    "Reported records in map cells within half a mile of the pin, 2020–present. "
    "Not crimes aboard a train, in a station, or inside the building. "
    "Cells are about 11 m across."
)
_LINE_NOTE = (
    "Reported records in map cells within half a mile of any station on this Metro line, 2020–present. "
    "The same letter is the same line. A record near two stations on that line is counted once. "
    "A station on two lines is counted for each line. "
    "Not crimes aboard a train. The area is the union of those half-mile circles."
)
_LINE_REQUEST = re.compile(
    r"^(?:all|lines|every|each)$|^(?:the\s+)?(?:metro\s+)?(?:line\s+)?([a-z])(?:\s+line)?$",
    re.IGNORECASE,
)
_cells: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
_catalogs: dict[str, list[dict]] = {}
_line_groups: dict[str, list[dict]] | None = None
_areas: dict[tuple, float] = {}


def _offense(text: str) -> str | None:
    key = str(text or "").strip().lower()
    if not key or key in {"all", "any", "crime", "crimes"}:
        return None
    if key in _UNSUPPORTED:
        raise ValueError(_UNSUPPORTED[key])
    found = _GROUP_ALIASES.get(key)
    if found is None or found == "other":
        known = ", ".join(group_id for group_id, _label, _needles in CRIME_GROUPS if group_id != "other")
        raise ValueError(f"No offense group named {text}. Groups: {known}.")
    return found


def _kind(text: str) -> str | None:
    key = str(text or "").strip().lower()
    if not key:
        return None
    found = _KINDS.get(key)
    if found is None:
        raise ValueError("kind is rail, bus, fire, police, or hospital.")
    return found


def _arrays(offense: str | None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    key = offense or ""
    cached = _cells.get(key)
    if cached is not None:
        return cached
    from backend.heat_warehouse import nibrs_bundle, report_bundle
    from backend.heatmap import _add_grids, _drop_month

    report = report_bundle()
    nibrs = nibrs_bundle()
    if offense:
        series = _add_grids(
            (report.get("types") or {}).get(offense),
            _drop_month((nibrs.get("types") or {}).get(offense)),
        )
    else:
        series = _add_grids(report.get("all"), _drop_month(nibrs.get("counts")))
    if series is None or len(series) == 0:
        packed = (np.array([]), np.array([]), np.array([]))
    else:
        packed = (
            series.index.get_level_values(0).to_numpy(dtype=np.float64),
            series.index.get_level_values(1).to_numpy(dtype=np.float64),
            series.to_numpy(dtype=np.int64),
        )
    _cells[key] = packed
    return packed


def _catalog(kind: str) -> list[dict]:
    cached = _catalogs.get(kind)
    if cached is not None:
        return cached
    from backend.warehouse import connect

    if kind in {"rail", "bus"}:
        rows = connect().execute(
            """
            SELECT name, lines, latitude, longitude
            FROM transit_stops
            WHERE kind = ? AND latitude IS NOT NULL AND longitude IS NOT NULL
            """,
            [kind],
        ).fetchall()
        places = [
            {"kind": kind, "name": name, "detail": lines or "", "latitude": float(lat), "longitude": float(lon)}
            for name, lines, lat, lon in rows
        ]
    else:
        rows = connect().execute(
            """
            SELECT name, address, latitude, longitude
            FROM facilities
            WHERE kind = ? AND latitude IS NOT NULL AND longitude IS NOT NULL
            """,
            [kind],
        ).fetchall()
        places = [
            {"kind": kind, "name": name, "detail": address or "", "latitude": float(lat), "longitude": float(lon)}
            for name, address, lat, lon in rows
        ]
    _catalogs[kind] = places
    return places


def line_request(text: str) -> str | None:
    """A Metro letter, or 'all'. A station name does not match."""
    match = _LINE_REQUEST.match(str(text or "").strip())
    if match is None:
        return None
    return "all" if match.group(1) is None else match.group(1).upper()


def station_lines(detail: str) -> list[str]:
    """Letters in a station's lines field. 'A;E' is the A Line and the E Line."""
    letters = []
    for part in re.split(r"[;,|/]+", str(detail or "")):
        token = part.strip().upper()
        if re.fullmatch(r"[A-Z]", token) and token not in letters:
            letters.append(token)
    return letters


def line_groups() -> dict[str, list[dict]]:
    """Rail stations grouped by the line letter they serve."""
    global _line_groups
    if _line_groups is not None:
        return _line_groups
    grouped: dict[str, list[dict]] = {}
    for place in _catalog("rail"):
        for letter in station_lines(place.get("detail") or ""):
            grouped.setdefault(letter, []).append(place)
    _line_groups = grouped
    return grouped


def line_places(text: str) -> tuple[list[dict], str | None]:
    """One place per requested line. Its stations list is the whole line."""
    key = line_request(text)
    if key is None:
        return [], "Name a Metro line letter, such as A, or all."
    groups = line_groups()
    if not groups:
        return [], "No Metro rail stations are loaded."
    if key != "all" and key not in groups:
        known = ", ".join(sorted(groups))
        return [], f"No Metro line {key}. Letters on the station list: {known}."
    letters = sorted(groups) if key == "all" else [key]
    places = []
    for letter in letters:
        stations = groups[letter]
        places.append({
            "kind": "rail",
            "name": f"Metro {letter} Line",
            "detail": f"{len(stations)} stations",
            "line": letter,
            "stations": stations,
            "latitude": float(stations[0]["latitude"]),
            "longitude": float(stations[0]["longitude"]),
        })
    return places, None


def _near_mask(
    latitude: float,
    longitude: float,
    lats: np.ndarray,
    lons: np.ndarray,
    radius_m: float,
) -> np.ndarray:
    chosen = (np.abs(lats - latitude) <= 0.012) & (np.abs(lons - longitude) <= 0.015)
    hit = np.zeros(len(lats), dtype=bool)
    if not chosen.any():
        return hit
    distance = haversine_m(latitude, longitude, lats[chosen], lons[chosen])
    hit[np.flatnonzero(chosen)[distance <= radius_m]] = True
    return hit


def count_near(
    latitude: float,
    longitude: float,
    arrays: tuple[np.ndarray, np.ndarray, np.ndarray],
    radius_m: float = RADIUS_M,
) -> int:
    """Sum cell weights whose center is inside the circle."""
    lats, lons, weights = arrays
    if len(weights) == 0:
        return 0
    return int(weights[_near_mask(latitude, longitude, lats, lons, radius_m)].sum())


def count_near_any(
    places: list[dict],
    arrays: tuple[np.ndarray, np.ndarray, np.ndarray],
    radius_m: float = RADIUS_M,
) -> int:
    """Sum cell weights inside any circle. A cell near two places is counted once."""
    lats, lons, weights = arrays
    if len(weights) == 0 or not places:
        return 0
    hit = np.zeros(len(weights), dtype=bool)
    for place in places:
        hit |= _near_mask(float(place["latitude"]), float(place["longitude"]), lats, lons, radius_m)
    return int(weights[hit].sum())


def union_area_km2(places: list[dict], radius_m: float = RADIUS_M, step_m: float = 100.0) -> float:
    """Area of the overlapping circles, estimated on a 100 m grid."""
    if not places:
        return 0.0
    key = tuple(sorted(
        (round(float(place["latitude"]), 5), round(float(place["longitude"]), 5))
        for place in places
    ))
    cached = _areas.get(key)
    if cached is not None:
        return cached
    lats = np.array([float(place["latitude"]) for place in places], dtype=np.float64)
    lons = np.array([float(place["longitude"]) for place in places], dtype=np.float64)
    mean_lat = float(np.mean(lats))
    meters_per_lat = 111_320.0
    meters_per_lon = 111_320.0 * math.cos(math.radians(mean_lat))
    pad_lat = radius_m / meters_per_lat
    pad_lon = radius_m / meters_per_lon
    step_lat = step_m / meters_per_lat
    step_lon = step_m / meters_per_lon
    grid_lat = np.arange(float(lats.min()) - pad_lat, float(lats.max()) + pad_lat + step_lat, step_lat)
    grid_lon = np.arange(float(lons.min()) - pad_lon, float(lons.max()) + pad_lon + step_lon, step_lon)
    mesh_lat, mesh_lon = np.meshgrid(grid_lat, grid_lon, indexing="ij")
    flat_lat = mesh_lat.ravel()
    flat_lon = mesh_lon.ravel()
    hit = np.zeros(flat_lat.shape[0], dtype=bool)
    for latitude, longitude in zip(lats, lons):
        hit |= _near_mask(float(latitude), float(longitude), flat_lat, flat_lon, radius_m)
    area = float(hit.sum()) * (step_m / 1000.0) ** 2
    _areas[key] = area
    return area


def _city_rate() -> float | None:
    from backend.datasets import load_city_baseline

    baseline = load_city_baseline() or {}
    present = baseline.get("present") or {}
    rate = present.get("crime_per_km2")
    return None if rate is None else float(rate)


def _row(place: dict, records: int, city_rate: float | None) -> dict:
    from pipeline.city_baseline import compared_with_city, per_square_mile

    per_km2 = round(records / AREA_KM2, 1) if AREA_KM2 else 0.0
    compared = None if city_rate is None else compared_with_city(per_km2, city_rate, "2020–present")
    return {
        "kind": place.get("kind"),
        "name": place.get("name"),
        "detail": place.get("detail") or "",
        "latitude": round(float(place["latitude"]), 6),
        "longitude": round(float(place["longitude"]), 6),
        "records": records,
        "per_km2": per_km2,
        "per_mi2": per_square_mile(per_km2),
        "city_comparison": None if compared is None else compared["summary"],
    }


def _ranked(places: list[dict], offense: str | None) -> list[dict]:
    arrays = _arrays(offense)
    city = _city_rate()
    scored = [_row(place, count_near(place["latitude"], place["longitude"], arrays), city) for place in places]
    scored.sort(key=lambda item: item["records"], reverse=True)
    return scored


def _line_row(place: dict, offense: str | None, city_rate: float | None) -> dict:
    from pipeline.city_baseline import compared_with_city, per_square_mile

    stations = place.get("stations") or []
    records = count_near_any(stations, _arrays(offense))
    area = union_area_km2(stations)
    per_km2 = round(records / area, 1) if area else 0.0
    compared = None if city_rate is None else compared_with_city(per_km2, city_rate, "2020–present")
    return {
        "kind": "rail",
        "line": place.get("line"),
        "name": place.get("name"),
        "station_count": len(stations),
        "records": records,
        "area_km2": round(area, 2),
        "per_km2": per_km2,
        "per_mi2": per_square_mile(per_km2),
        "city_comparison": None if compared is None else compared["summary"],
    }


def _lines_result(request: str, offense: str | None, kind: str | None) -> dict:
    if kind not in {None, "rail"}:
        return {"error": "A Metro line letter applies to rail stations. Bus routes are not these letters.", "warnings": []}
    places, error = line_places(request)
    if error:
        return {"error": error, "warnings": []}
    label = None if offense is None else offense
    city = _city_rate()
    rows = [_line_row(place, offense, city) for place in places]
    rows.sort(key=lambda item: item["records"], reverse=True)
    body = {
        "kind": "rail",
        "offense": label,
        "within_m": int(RADIUS_M),
        "what_it_counts": _LINE_NOTE,
        "warnings": [_LINE_NOTE],
    }
    if len(rows) == 1:
        body["line"] = rows[0]["line"]
        body["place"] = rows[0]
        return body
    body["line"] = "all"
    body["place_count"] = len(rows)
    body["highest"] = rows[0] if rows else None
    body["ranked"] = rows
    return body


def crime_around(
    kind: str = "",
    name: str = "",
    latitude: float | None = None,
    longitude: float | None = None,
    offense: str = "",
    line: str = "",
) -> dict:
    """Rank pins of one kind, or count records around one named pin, line, or coordinate."""
    try:
        group = _offense(offense)
        chosen = _kind(kind)
    except ValueError as exc:
        return {"error": str(exc), "warnings": []}
    label = None if group is None else group
    if latitude is None and longitude is None:
        requested = line_request(line) or line_request(name)
        if requested:
            return _lines_result(line or name, group, chosen)
    if latitude is not None or longitude is not None:
        if latitude is None or longitude is None:
            return {"error": "Pass both latitude and longitude.", "warnings": []}
        if not (33.2 <= float(latitude) <= 34.9 and -119.0 <= float(longitude) <= -117.4):
            return {"error": "That point is outside the Los Angeles crime grid.", "warnings": []}
        place = {
            "kind": chosen or "point",
            "name": name or "this location",
            "detail": "",
            "latitude": float(latitude),
            "longitude": float(longitude),
        }
        row = _ranked([place], group)[0]
        return {
            "offense": label,
            "within_m": int(RADIUS_M),
            "what_it_counts": _NOTE,
            "place": row,
            "warnings": [_NOTE],
        }
    if chosen is None and not str(name or "").strip():
        return {"error": "Name a rail, bus, fire, police, or hospital pin, or pass a latitude and longitude.", "warnings": []}
    kinds = [chosen] if chosen else ["rail", "bus", "fire", "police", "hospital"]
    places: list[dict] = []
    for item in kinds:
        places.extend(_catalog(item))
    query = str(name or "").strip().lower()
    if query:
        places = [place for place in places if query in str(place["name"]).lower()]
        if not places:
            return {
                "error": f"No {chosen or 'listed'} pin matched {name}. An address that is not in the station or facility list cannot be geocoded.",
                "warnings": [],
            }
    ranked = _ranked(places, group)
    return {
        "kind": chosen,
        "offense": label,
        "within_m": int(RADIUS_M),
        "place_count": len(ranked),
        "what_it_counts": _NOTE,
        "highest": ranked[0] if ranked else None,
        "ranked": ranked[:5],
        "warnings": [_NOTE],
    }
