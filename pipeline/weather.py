"""Daily weather at each venue, from 2020 through the latest archive day.

Open-Meteo's historical archive needs no API key. Weather is taken at the
venue pin already stored in venue_summary.json. The joined file adds a daily
crime count for the same pin. It does not rewrite the crime files.

The count follows the same split as the rest of the project. Before March 7,
2024 it is the LAPD report count from permit_event_days.csv. From that date
through the last day in the NIBRS extract, it is the number of NIBRS offense
rows inside the 800 m buffer. Days after the NIBRS file ends keep the weather
and leave the count blank.

Usage (from the repo root):

    python -m pipeline.weather
    python -m pipeline.weather --join-only
"""

from __future__ import annotations

import csv
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SUMMARY_PATH = REPO_ROOT / "data" / "processed" / "venue_summary.json"
CRIME_DAYS_PATH = REPO_ROOT / "data" / "processed" / "permit_event_days.csv"
WEATHER_PATH = REPO_ROOT / "data" / "processed" / "weather_daily.csv"
JOINED_PATH = REPO_ROOT / "data" / "processed" / "venue_days_with_weather.csv"
META_PATH = REPO_ROOT / "data" / "processed" / "weather_meta.json"

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
START = date(2020, 1, 1)
TIMEZONE = "America/Los_Angeles"
DAILY_FIELDS = (
    "weather_code",
    "temperature_2m_max",
    "temperature_2m_min",
    "temperature_2m_mean",
    "precipitation_sum",
    "rain_sum",
    "snowfall_sum",
    "wind_speed_10m_max",
)
# WMO weather interpretation codes for liquid or frozen precipitation.
WET_CODES = frozenset({
    51, 53, 55, 56, 57,
    61, 63, 65, 66, 67,
    71, 73, 75, 77,
    80, 81, 82, 85, 86,
    95, 96, 99,
})
WEATHER_COLUMNS = (
    "venue_id",
    "venue_name",
    "latitude",
    "longitude",
    "date",
    "temp_f_mean",
    "temp_f_max",
    "temp_f_min",
    "precip_in",
    "rain_in",
    "snow_in",
    "wind_mph_max",
    "weather_code",
    "wet_day",
)
NIBRS_START = date(2024, 3, 7)
JOINED_COLUMNS = WEATHER_COLUMNS + (
    "incident_count",
    "count_source",
    "is_permit_event_day",
    "permit_count",
)


def venue_points(summary: dict | None = None) -> list[dict]:
    """Venue pins used for the weather request."""
    payload = summary if summary is not None else json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    points = []
    for venue in payload["venues"]:
        points.append({
            "venue_id": str(venue["venue_id"]),
            "venue_name": str(venue["venue_name"]),
            "latitude": float(venue["latitude"]),
            "longitude": float(venue["longitude"]),
        })
    points.sort(key=lambda item: item["venue_id"])
    return points


def archive_url(latitude: float, longitude: float, start: date, end: date) -> str:
    query = urllib.parse.urlencode({
        "latitude": f"{latitude:.4f}",
        "longitude": f"{longitude:.4f}",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": ",".join(DAILY_FIELDS),
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": TIMEZONE,
    })
    return f"{ARCHIVE_URL}?{query}"


def _number(value: object, places: int) -> str:
    if value is None or value == "":
        return ""
    return f"{float(value):.{places}f}"


def wet_day(precip_in: str, weather_code: str) -> str:
    """1 when the day had precipitation, otherwise 0. Blank if both are missing."""
    if precip_in == "" and weather_code == "":
        return ""
    if precip_in != "" and float(precip_in) > 0:
        return "1"
    if weather_code != "" and int(float(weather_code)) in WET_CODES:
        return "1"
    return "0"


def weather_rows(point: dict, payload: dict) -> list[dict]:
    """One archive response becomes one row per day."""
    daily = payload.get("daily") or {}
    days = daily.get("time") or []
    columns = {
        "temp_f_mean": daily.get("temperature_2m_mean") or [],
        "temp_f_max": daily.get("temperature_2m_max") or [],
        "temp_f_min": daily.get("temperature_2m_min") or [],
        "precip_in": daily.get("precipitation_sum") or [],
        "rain_in": daily.get("rain_sum") or [],
        "snow_in": daily.get("snowfall_sum") or [],
        "wind_mph_max": daily.get("wind_speed_10m_max") or [],
        "weather_code": daily.get("weather_code") or [],
    }
    places = {
        "temp_f_mean": 1,
        "temp_f_max": 1,
        "temp_f_min": 1,
        "precip_in": 3,
        "rain_in": 3,
        "snow_in": 3,
        "wind_mph_max": 1,
        "weather_code": 0,
    }
    rows = []
    for index, day in enumerate(days):
        row = {
            "venue_id": point["venue_id"],
            "venue_name": point["venue_name"],
            "latitude": f"{point['latitude']:.4f}",
            "longitude": f"{point['longitude']:.4f}",
            "date": day,
        }
        for key, series in columns.items():
            value = series[index] if index < len(series) else None
            row[key] = _number(value, places[key])
        row["wet_day"] = wet_day(row["precip_in"], row["weather_code"])
        rows.append(row)
    return rows


def load_crime_days(path: Path = CRIME_DAYS_PATH) -> dict[tuple[str, str], dict]:
    """Daily report counts already joined to permits. Keyed by venue and date."""
    found: dict[tuple[str, str], dict] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            found[(row["venue_id"], row["date"])] = row
    return found


def load_weather_rows(path: Path = WEATHER_PATH) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def nibrs_daily_counts(points: list[dict], path: Path | None = None) -> tuple[dict[tuple[str, str], int], date]:
    """Offense rows inside each 800 m buffer, from March 7, 2024 through the file's last day."""
    from pipeline.geo import buffer_zone
    from pipeline.merge_crime import NIBRS_PATH, _load_nibrs

    frame = _load_nibrs(path or NIBRS_PATH)
    if frame.empty:
        return {}, NIBRS_START
    latest = frame["occurred_at"].max().date()
    counts: dict[tuple[str, str], int] = {}
    for point in points:
        nearby = buffer_zone(frame, point["latitude"], point["longitude"])
        if nearby.empty:
            continue
        for day, count in nearby["occurred_at"].dt.date.value_counts().items():
            counts[(point["venue_id"], day.isoformat())] = int(count)
    return counts, latest


def blend_crime_days(
    report_days: dict[tuple[str, str], dict],
    nibrs_counts: dict[tuple[str, str], int],
    venues: list[dict],
    nibrs_end: date,
) -> dict[tuple[str, str], dict]:
    """Reports before the NIBRS start, then NIBRS offense counts through nibrs_end.

    A day in the NIBRS window with no offense inside the buffer is 0. Report
    counts on or after the start date are not used. Permit fields still come
    from the report table when that table has a row.
    """
    cutoff = NIBRS_START.isoformat()
    end = nibrs_end.isoformat()
    blended: dict[tuple[str, str], dict] = {}
    for key, row in report_days.items():
        if key[1] >= cutoff:
            continue
        blended[key] = {
            "venue_id": row.get("venue_id", key[0]),
            "venue_name": row.get("venue_name", ""),
            "incident_count": str(row.get("incident_count", "")),
            "count_source": "report",
            "is_permit_event_day": row.get("is_permit_event_day", ""),
            "permit_count": row.get("permit_count", ""),
        }
    if nibrs_end < NIBRS_START:
        return blended
    names = {item["venue_id"]: item.get("venue_name", "") for item in venues}
    cursor = NIBRS_START
    while cursor.isoformat() <= end:
        day = cursor.isoformat()
        for venue in venues:
            venue_id = venue["venue_id"]
            key = (venue_id, day)
            permit = report_days.get(key) or {}
            blended[key] = {
                "venue_id": venue_id,
                "venue_name": permit.get("venue_name") or names.get(venue_id, ""),
                "incident_count": str(int(nibrs_counts.get(key, 0))),
                "count_source": "nibrs",
                "is_permit_event_day": permit.get("is_permit_event_day", ""),
                "permit_count": permit.get("permit_count", ""),
            }
        cursor += timedelta(days=1)
    return blended


def join_weather(weather: list[dict], crime_days: dict[tuple[str, str], dict]) -> list[dict]:
    """Weather days plus the crime fields for dates the blended table contains."""
    joined = []
    seen = set()
    for row in weather:
        key = (row["venue_id"], row["date"])
        seen.add(key)
        crime = crime_days.get(key) or {}
        joined.append(_with_crime(row, crime))
    for key, crime in sorted(crime_days.items()):
        if key in seen:
            continue
        joined.append(_with_crime({
            "venue_id": crime.get("venue_id", key[0]),
            "venue_name": crime.get("venue_name", ""),
            "latitude": "",
            "longitude": "",
            "date": key[1],
            "temp_f_mean": "",
            "temp_f_max": "",
            "temp_f_min": "",
            "precip_in": "",
            "rain_in": "",
            "snow_in": "",
            "wind_mph_max": "",
            "weather_code": "",
            "wet_day": "",
        }, crime))
    joined.sort(key=lambda row: (row["venue_id"], row["date"]))
    return joined


def _with_crime(row: dict, crime: dict) -> dict:
    return {
        **row,
        "incident_count": crime.get("incident_count", ""),
        "count_source": crime.get("count_source", ""),
        "is_permit_event_day": crime.get("is_permit_event_day", ""),
        "permit_count": crime.get("permit_count", ""),
    }


def _write_csv(path: Path, columns: tuple[str, ...], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def fetch_archive(latitude: float, longitude: float, start: date, end: date) -> dict:
    """One venue for the whole span. The free archive allows a few calls a minute."""
    request = urllib.request.Request(
        archive_url(latitude, longitude, start, end),
        headers={"User-Agent": "safe-games-la/1.0 (academic; weather archive)"},
    )
    delay = 65
    for attempt in range(6):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            if error.code != 429 or attempt == 5:
                detail = error.read().decode("utf-8", errors="replace")
                raise RuntimeError(f"Weather archive returned {error.code}: {detail}") from error
            time.sleep(delay)
    raise RuntimeError("Weather archive did not respond.")


def _crime_meta(points: list[dict], nibrs_end: date) -> dict:
    return {
        "report_file": "data/processed/permit_event_days.csv",
        "report_rule": "LAPD report counts inside the 800 m buffer, dates before 2024-03-07.",
        "nibrs_file": "data/raw/nibrs/nibrs_current.csv",
        "nibrs_rule": (
            "NIBRS offense rows inside the 800 m buffer, from 2024-03-07 "
            "through the last date in that file. A day with no offense is 0."
        ),
        "nibrs_start": NIBRS_START.isoformat(),
        "nibrs_end": nibrs_end.isoformat(),
        "venue_count": len(points),
    }


def write_joined(weather: list[dict], points: list[dict], requested_end: date) -> dict:
    """Blend the two crime series onto weather rows and write the join plus meta."""
    nibrs_counts, nibrs_end = nibrs_daily_counts(points)
    blended = blend_crime_days(load_crime_days(), nibrs_counts, points, nibrs_end)
    joined = join_weather(weather, blended)
    _write_csv(JOINED_PATH, JOINED_COLUMNS, joined)
    dates = [row["date"] for row in weather]
    crime_dates = sorted(row["date"] for row in joined if row["incident_count"] != "")
    meta = {
        "source": "Open-Meteo Historical Weather API",
        "endpoint": ARCHIVE_URL,
        "timezone": TIMEZONE,
        "units": {
            "temperature": "fahrenheit",
            "precipitation": "inch",
            "wind": "mph",
        },
        "requested_start": START.isoformat(),
        "requested_end": requested_end.isoformat(),
        "actual_start": dates[0] if dates else "",
        "actual_end": dates[-1] if dates else "",
        "venue_count": len(points),
        "weather_rows": len(weather),
        "joined_rows": len(joined),
        "crime_daily_start": crime_dates[0] if crime_dates else "",
        "crime_daily_end": crime_dates[-1] if crime_dates else "",
        "crime_counts": _crime_meta(points, nibrs_end),
        "crime_daily_note": (
            "incident_count is the LAPD report count inside the 800 m buffer before "
            "March 7, 2024, and the NIBRS offense count inside that buffer from "
            "March 7, 2024 through the last day in the NIBRS file. Days after that "
            "leave incident_count blank. count_source says which series the count came from."
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    META_PATH.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return meta


def build(end: date | None = None) -> dict:
    """Download each venue and write the weather file, the join, and a short meta file."""
    end = end or date.today()
    points = venue_points()
    weather: list[dict] = []
    for point in points:
        if weather:
            time.sleep(8)
        payload = fetch_archive(point["latitude"], point["longitude"], START, end)
        weather.extend(weather_rows(point, payload))
    weather.sort(key=lambda row: (row["venue_id"], row["date"]))
    _write_csv(WEATHER_PATH, WEATHER_COLUMNS, weather)
    return write_joined(weather, points, end)


def join_saved_weather() -> dict:
    """Rebuild the crime join from the weather file already on disk."""
    weather = load_weather_rows()
    if not weather:
        raise RuntimeError("No saved weather rows. Run python -m pipeline.weather first.")
    last = date.fromisoformat(max(row["date"] for row in weather))
    return write_joined(weather, venue_points(), last)


def main() -> None:
    meta = join_saved_weather() if "--join-only" in sys.argv else build()
    counts = meta["crime_counts"]
    print(
        f"Wrote {meta['weather_rows']} weather rows, "
        f"{meta['actual_start']} through {meta['actual_end']}. "
        f"Crime counts run {meta['crime_daily_start']} through {meta['crime_daily_end']} "
        f"({counts['nibrs_start']} through {counts['nibrs_end']} from NIBRS)."
    )


if __name__ == "__main__":
    main()
