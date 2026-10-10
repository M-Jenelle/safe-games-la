"""Reported records around a station, stop, or facility.

The chat tables only hold crime inside venue circles. This uses the city
heatmap cells, so a fire station far from a venue is included. A cell is
about 11 m across. The count is records near the pin, not crimes on a train
or inside the building.
"""

from __future__ import annotations

import math

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
    "narcotics": "Narcotics is not its own group in these tables.",
    "drugs": "Narcotics is not its own group in these tables.",
    "drug": "Narcotics is not its own group in these tables.",
    "scam": "Scams are not a group in these tables.",
    "scams": "Scams are not a group in these tables.",
    "society": "Crimes against society is not a group in these tables. Weapons is a group. Narcotics is not separate.",
}
_NOTE = (
    "Reported records in map cells within 800 m of the pin, 2020–present. "
    "Not crimes aboard a train, in a station, or inside the building. "
    "Cells are about 11 m across."
)
_cells: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
_catalogs: dict[str, list[dict]] = {}


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
    chosen = (
        (np.abs(lats - latitude) <= 0.012)
        & (np.abs(lons - longitude) <= 0.015)
    )
    if not chosen.any():
        return 0
    distance = haversine_m(latitude, longitude, lats[chosen], lons[chosen])
    return int(weights[chosen][distance <= radius_m].sum())


def _city_rate() -> float | None:
    from backend.datasets import load_city_baseline

    baseline = load_city_baseline() or {}
    present = baseline.get("present") or {}
    rate = present.get("crime_per_km2")
    return None if rate is None else float(rate)


def _row(place: dict, records: int, city_rate: float | None) -> dict:
    from pipeline.city_baseline import compared_with_city

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
        "city_comparison": None if compared is None else compared["summary"],
    }


def _ranked(places: list[dict], offense: str | None) -> list[dict]:
    arrays = _arrays(offense)
    city = _city_rate()
    scored = [_row(place, count_near(place["latitude"], place["longitude"], arrays), city) for place in places]
    scored.sort(key=lambda item: item["records"], reverse=True)
    return scored


def crime_around(
    kind: str = "",
    name: str = "",
    latitude: float | None = None,
    longitude: float | None = None,
    offense: str = "",
) -> dict:
    """Rank pins of one kind, or count records around one named pin or coordinate."""
    try:
        group = _offense(offense)
        chosen = _kind(kind)
    except ValueError as exc:
        return {"error": str(exc), "warnings": []}
    label = None if group is None else group
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
