"""Slices of the raw crime extracts that the heatmap grid does not keep.

Victim age, sex, and descent are not read. LAPD slices stop on March 6, 2024.
Drug codes are NIBRS offenses from March 7, 2024. The two series are not added.
"""

from __future__ import annotations

import numpy as np

from backend.place_crime import RADIUS_M, _catalog, _kind, count_near

_PERIODS = ("night", "morning", "afternoon", "evening")
_PERIOD_LABELS = {
    "night": "12am–6am",
    "morning": "6am–12pm",
    "afternoon": "12pm–6pm",
    "evening": "6pm–12am",
}
_PREMISE_LABELS = {
    "street": "Street",
    "parking": "Parking lot",
    "bus": "Bus or bus stop",
    "rail": "Rail station or train",
    "mta_property": "MTA property",
    "fire_station": "Fire station",
}
_TOPICS = {
    "hours": "hours",
    "hour": "hours",
    "time": "hours",
    "night": "hours",
    "premise": "premise",
    "premises": "premise",
    "place": "premise",
    "weapon": "weapon",
    "weapons_recorded": "weapon",
    "officer": "officer",
    "officers": "officer",
    "police officer": "officer",
    "bunco": "bunco",
    "scam": "bunco",
    "scams": "bunco",
    "pickpocket": "pickpocket",
    "pickpockets": "pickpocket",
    "drugs": "drugs",
    "drug": "drugs",
    "narcotics": "drugs",
    "area": "area",
    "areas": "area",
    "district": "area",
    "lag": "lag",
    "reporting lag": "lag",
    "delay": "lag",
}
_LAPD_THROUGH = "2024-03-06"
_NIBRS_FROM = "2024-03-07"
_SPLIT = "LAPD reports through March 6, 2024 and NIBRS offenses from March 7, 2024 are separate. They are not added."


def _empty() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    return (np.empty(0), np.empty(0), np.empty(0, dtype=np.int64))


def _total(arrays: tuple[np.ndarray, np.ndarray, np.ndarray] | None) -> int:
    if not arrays or len(arrays[2]) == 0:
        return 0
    return int(arrays[2].sum())


def _count(arrays, place: dict | None) -> int:
    if place is None:
        return _total(arrays)
    return count_near(float(place["latitude"]), float(place["longitude"]), arrays or _empty())


def _topic(text: str) -> str:
    key = str(text or "").strip().lower()
    found = _TOPICS.get(key)
    if found is None:
        known = "hours, premise, weapon, officer, bunco, pickpocket, drugs, area, lag"
        raise ValueError(f"topic is one of {known}.")
    return found


def _period(text: str) -> str | None:
    key = str(text or "").strip().lower().replace("-", "").replace(" ", "")
    aliases = {
        "night": "night",
        "latenight": "night",
        "late": "night",
        "morning": "morning",
        "afternoon": "afternoon",
        "evening": "evening",
        "12am6am": "night",
        "6am12pm": "morning",
        "12pm6pm": "afternoon",
        "6pm12am": "evening",
    }
    if not key:
        return None
    found = aliases.get(key)
    if found is None:
        raise ValueError("period is night, morning, afternoon, or evening.")
    return found


def _bucket(text: str) -> str | None:
    key = str(text or "").strip().lower()
    aliases = {
        "street": "street",
        "parking": "parking",
        "parking lot": "parking",
        "bus": "bus",
        "bus stop": "bus",
        "rail": "rail",
        "station": "rail",
        "train": "rail",
        "metro": "rail",
        "mta": "mta_property",
        "mta property": "mta_property",
        "fire": "fire_station",
        "fire station": "fire_station",
    }
    if not key:
        return None
    found = aliases.get(key)
    if found is None:
        raise ValueError("premise bucket is street, parking, bus, rail, mta, or fire station.")
    return found


def _periods(grids: dict, place: dict | None, period: str | None) -> list[dict]:
    chosen = (period,) if period else _PERIODS
    rows = []
    for item in chosen:
        rows.append({
            "period": item,
            "label": _PERIOD_LABELS[item],
            "records": _count(grids.get(item), place),
        })
    return rows


def _pins(kind: str, name: str, latitude, longitude) -> tuple[list[dict], str | None]:
    if latitude is not None or longitude is not None:
        if latitude is None or longitude is None:
            return [], "Pass both latitude and longitude."
        if not (33.2 <= float(latitude) <= 34.9 and -119.0 <= float(longitude) <= -117.4):
            return [], "That point is outside the Los Angeles crime grid."
        return [{
            "kind": "point",
            "name": name or "this location",
            "detail": "",
            "latitude": float(latitude),
            "longitude": float(longitude),
        }], None
    places: list[dict] = []
    if kind:
        places.extend(_catalog(_kind(kind)))
    elif name:
        from backend.datasets import DatasetNotFound, load_summary

        try:
            for venue in load_summary()["venues"]:
                if venue.get("latitude") is None:
                    continue
                places.append({
                    "kind": "venue",
                    "name": venue.get("venue_name") or venue["venue_id"],
                    "detail": venue["venue_id"],
                    "latitude": float(venue["latitude"]),
                    "longitude": float(venue["longitude"]),
                })
        except DatasetNotFound:
            pass
        for item in ("rail", "fire", "police", "hospital", "bus"):
            places.extend(_catalog(item))
    query = str(name or "").strip().lower()
    if query:
        places = [place for place in places if query in str(place["name"]).lower()]
        if not places:
            return [], f"No listed pin matched {name}."
    return places, None


def _ranked(places: list[dict], arrays) -> list[dict]:
    rows = []
    for place in places:
        rows.append({
            "kind": place.get("kind"),
            "name": place.get("name"),
            "detail": place.get("detail") or "",
            "records": count_near(float(place["latitude"]), float(place["longitude"]), arrays or _empty()),
        })
    rows.sort(key=lambda item: item["records"], reverse=True)
    return rows


def _place_fields(place: dict | None, places: list[dict]) -> dict:
    if place is not None:
        return {"place": place["name"], "within_m": int(RADIUS_M)}
    if len(places) == 1:
        return {"place": places[0]["name"], "within_m": int(RADIUS_M)}
    return {"place": None, "within_m": None}


def _one_place(places: list[dict]) -> dict | None:
    if len(places) == 1:
        return places[0]
    return None


def crime_detail(
    topic: str,
    kind: str = "",
    name: str = "",
    latitude: float | None = None,
    longitude: float | None = None,
    period: str = "",
    bucket: str = "",
    details: dict | None = None,
) -> dict:
    """One raw-data slice, citywide or within 800 m of a pin."""
    try:
        chosen = _topic(topic)
        clock = _period(period)
        premise = _bucket(bucket)
        places, error = _pins(kind, name, latitude, longitude)
    except ValueError as exc:
        return {"error": str(exc), "warnings": []}
    if error:
        return {"error": error, "warnings": []}
    payload = details if details is not None else _loaded()
    if chosen == "area":
        return _area(payload)
    if chosen == "lag":
        return _lag(payload)
    place = _one_place(places)
    ranking = len(places) > 1
    if chosen == "hours":
        return _hours(payload, places, place, clock, ranking)
    if chosen == "premise":
        return _premise(payload, places, place, premise, ranking)
    if chosen == "weapon":
        return _weapon(payload, places, place, ranking)
    if chosen == "drugs":
        return _drugs(payload, places, place, clock, ranking)
    flag = {"officer": None, "bunco": "bunco", "pickpocket": "pickpocket"}[chosen]
    return _flag(payload, chosen, flag, places, place, ranking)


def _loaded() -> dict:
    from backend.heat_warehouse import details_bundle

    return details_bundle()


def _area(payload: dict) -> dict:
    return {
        "topic": "area",
        "series": "lapd_report",
        "through": _LAPD_THROUGH,
        "areas": payload.get("areas") or [],
        "reporting_districts": payload.get("districts") or [],
        "warnings": [
            "LAPD area and reporting district on the report, through March 6, 2024. Not a circle around a venue.",
        ],
    }


def _lag(payload: dict) -> dict:
    return {
        "topic": "lag",
        "series": "lapd_report",
        "through": _LAPD_THROUGH,
        "median_days": payload.get("lag_median_days"),
        "buckets": payload.get("lag") or [],
        "warnings": [
            "Days from the occurred date to the reported date, through March 6, 2024. A negative gap means the report date is earlier.",
        ],
    }


def _hours(payload: dict, places: list[dict], place: dict | None, clock: str | None, ranking: bool) -> dict:
    if ranking and clock is None:
        return {"error": "Pass a period: night, morning, afternoon, or evening.", "warnings": []}
    if ranking:
        lapd_ranked = _ranked(places, (payload.get("lapd_hours") or {}).get(clock))
        nibrs_ranked = _ranked(places, (payload.get("nibrs_hours") or {}).get(clock))
        return {
            "topic": "hours",
            "period": clock,
            "label": _PERIOD_LABELS[clock],
            "within_m": int(RADIUS_M),
            "lapd": {
                "through": _LAPD_THROUGH,
                "highest": lapd_ranked[0] if lapd_ranked else None,
                "ranked": lapd_ranked[:5],
            },
            "nibrs": {
                "from": _NIBRS_FROM,
                "highest": nibrs_ranked[0] if nibrs_ranked else None,
                "ranked": nibrs_ranked[:5],
            },
            "warnings": [
                _SPLIT,
                "12:00 on an LAPD report is often an unknown hour. It is counted in 12pm–6pm and also called out separately when a single place is asked.",
            ],
        }
    target = place
    lapd_rows = _periods(payload.get("lapd_hours") or {}, target, clock)
    nibrs_rows = _periods(payload.get("nibrs_hours") or {}, target, clock)
    return {
        "topic": "hours",
        **_place_fields(target, places),
        "lapd": {
            "through": _LAPD_THROUGH,
            "records": sum(row["records"] for row in lapd_rows),
            "periods": lapd_rows,
            "stamped_1200": _count(payload.get("lapd_noon"), target),
        },
        "nibrs": {
            "from": _NIBRS_FROM,
            "records": sum(row["records"] for row in nibrs_rows),
            "periods": nibrs_rows,
            "stamped_midnight": _count(payload.get("nibrs_midnight"), target),
        },
        "warnings": [
            _SPLIT,
            "12:00 on an LAPD report is often an unknown hour and stays inside 12pm–6pm. NIBRS midnight is a clock time.",
        ],
    }


def _premise(payload: dict, places: list[dict], place: dict | None, premise: str | None, ranking: bool) -> dict:
    if ranking and premise is None:
        return {"error": "Pass a premise bucket: street, parking, bus, rail, mta, or fire station.", "warnings": []}
    grids = payload.get("premise") or {}
    if ranking:
        ranked = _ranked(places, grids.get(premise))
        return {
            "topic": "premise",
            "bucket": premise,
            "label": _PREMISE_LABELS[premise],
            "series": "lapd_report",
            "through": _LAPD_THROUGH,
            "within_m": int(RADIUS_M),
            "highest": ranked[0] if ranked else None,
            "ranked": ranked[:5],
            "warnings": ["Reporting premise on the LAPD report, through March 6, 2024. Not crimes aboard a train."],
        }
    target = place
    rows = [
        {"bucket": key, "label": label, "records": _count(grids.get(key), target)}
        for key, label in _PREMISE_LABELS.items()
    ]
    body = {
        "topic": "premise",
        "series": "lapd_report",
        "through": _LAPD_THROUGH,
        **_place_fields(target, places),
        "buckets": rows,
        "warnings": ["Reporting premise on the LAPD report, through March 6, 2024. Not crimes aboard a train."],
    }
    if target is None:
        body["premise_names"] = payload.get("premise_names") or []
    return body


def _weapon(payload: dict, places: list[dict], place: dict | None, ranking: bool) -> dict:
    arrays = payload.get("weapon") or _empty()
    if ranking:
        ranked = _ranked(places, arrays)
        return {
            "topic": "weapon",
            "series": "lapd_report",
            "through": _LAPD_THROUGH,
            "within_m": int(RADIUS_M),
            "highest": ranked[0] if ranked else None,
            "ranked": ranked[:5],
            "warnings": [
                "Reports with a weapon description, through March 6, 2024. This is not the Weapons offense group, and a blank weapon field is not proof that no weapon was used.",
            ],
        }
    target = place
    recorded = _count(arrays, target)
    total = _count(payload.get("lapd_cells"), target)
    return {
        "topic": "weapon",
        "series": "lapd_report",
        "through": _LAPD_THROUGH,
        **_place_fields(target, places),
        "weapon_recorded": recorded,
        "reports": total,
        "warnings": [
            "Reports with a weapon description, through March 6, 2024. This is not the Weapons offense group, and a blank weapon field is not proof that no weapon was used.",
        ],
    }


def _drugs(payload: dict, places: list[dict], place: dict | None, clock: str | None, ranking: bool) -> dict:
    arrays = (payload.get("drug_hours") or {}).get(clock) if clock else None
    if arrays is None and clock is None:
        arrays = _empty()
        for grid in (payload.get("drugs") or {}).values():
            if _total(grid):
                arrays = _combine(arrays, grid)
    if ranking:
        ranked = _ranked(places, arrays)
        return {
            "topic": "drugs",
            "series": "nibrs",
            "from": _NIBRS_FROM,
            "period": clock,
            "within_m": int(RADIUS_M),
            "codes": payload.get("drug_totals") or [],
            "highest": ranked[0] if ranked else None,
            "ranked": ranked[:5],
            "warnings": [
                "NIBRS codes 35A and 35B from March 7, 2024. 35A is a controlled substance or narcotic offense. 35B is paraphernalia. This is separate from the nine offense groups.",
            ],
        }
    target = place
    return {
        "topic": "drugs",
        "series": "nibrs",
        "from": _NIBRS_FROM,
        "period": clock,
        **_place_fields(target, places),
        "records": _count(arrays, target),
        "codes": payload.get("drug_totals") or [],
        "by_period": _periods(payload.get("drug_hours") or {}, target, None) if clock is None else _periods(payload.get("drug_hours") or {}, target, clock),
        "warnings": [
            "NIBRS codes 35A and 35B from March 7, 2024. 35A is a controlled substance or narcotic offense. 35B is paraphernalia. This is separate from the nine offense groups.",
        ],
    }


def _combine(left, right):
    if _total(left) == 0:
        return right
    if _total(right) == 0:
        return left
    lats = np.concatenate([left[0], right[0]])
    lons = np.concatenate([left[1], right[1]])
    weights = np.concatenate([left[2], right[2]])
    return (lats, lons, weights)


def _flag(payload: dict, topic: str, flag: str | None, places: list[dict], place: dict | None, ranking: bool) -> dict:
    flags = payload.get("flags") or {}
    if topic == "officer":
        keys = ("officer_battery", "officer_adw")
        labels = {
            "officer_battery": "Battery on a police officer",
            "officer_adw": "Assault with a deadly weapon on a police officer",
        }
    else:
        keys = (flag,)
        labels = {"bunco": "Bunco", "pickpocket": "Pickpocket"}
    if ranking:
        combined = _empty()
        for key in keys:
            combined = _combine(combined, flags.get(key) or _empty())
        ranked = _ranked(places, combined)
        return {
            "topic": topic,
            "series": "lapd_report",
            "through": _LAPD_THROUGH,
            "within_m": int(RADIUS_M),
            "highest": ranked[0] if ranked else None,
            "ranked": ranked[:5],
            "warnings": [
                "LAPD description wording through March 6, 2024. This is not every assault on an officer, and NIBRS does not carry these wordings.",
            ],
        }
    target = place
    rows = [
        {"id": key, "label": labels[key], "records": _count(flags.get(key), target)}
        for key in keys
    ]
    return {
        "topic": topic,
        "series": "lapd_report",
        "through": _LAPD_THROUGH,
        **_place_fields(target, places),
        "rows": rows,
        "warnings": [
            "LAPD description wording through March 6, 2024. This is not every assault on an officer, and NIBRS does not carry these wordings.",
        ],
    }
