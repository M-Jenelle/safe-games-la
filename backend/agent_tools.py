"""The calculations Torchy can run. Warnings are attached here, not by the model.

The model chooses a tool. Python computes the figure. run_sql is read-only.
"""

from __future__ import annotations

import re
import threading
from datetime import date

import pandas as pd
from scipy.stats import spearmanr

from backend.event_baseline import fit_count_model, weekday_standardized
from backend.warehouse import SERIES_BREAK, connect

ROW_LIMIT = 200
SMALL_N = 30
SERIES_WARNING = (
    "Reporting changed on March 7, 2024. Earlier figures are LAPD reports. "
    "Later figures are NIBRS offenses. They are not added together."
)
OVERLAP_WARNING = (
    "Some 800 m venue circles overlap, so one report can be counted for more than one venue. "
    "Do not add venue totals into one citywide number."
)
SMALL_WARNING = "Fewer than 30 rows. A small count is a weak basis for a comparison."
FORECAST_WARNING = (
    "This is a multiplier for one recorded condition, not a forecast of future daily counts. "
    "The Games are refused: there is no precedent at this venue for Olympic crowds."
)
SPEARMAN_WARNING = (
    "Spearman is the only correlation reported. Its p-value treats each day as independent. "
    "Consecutive days are related, so the p-value is not a formal test."
)
ASSOCIATION_WARNING = "This is an association. It is not a cause."
REPORTED_WARNING = "These are reported records, not a count of every crime that happened."
LABEL_WARNING = (
    "Through March 6, 2024 these groups use LAPD report labels. "
    "From March 7, 2024 they use NIBRS offense codes, and the same words can land in a different group. "
    "Ask for the latest labels only if you want the NIBRS series by itself."
)
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|copy|pragma|install|export|call|set)\b"
    r"|copy\s+from|read_csv|read_text|read_blob|read_json|read_ndjson|read_parquet|parquet_scan|\bglob\s*\(",
    re.IGNORECASE,
)


def describe_data() -> dict:
    """Tables, columns, and the date span of each dated table."""
    connection = connect()
    tables = []
    names = [row[0] for row in connection.execute("SHOW TABLES").fetchall()]
    for name in names:
        columns = [
            {"name": column[0], "type": column[1]}
            for column in connection.execute(f"DESCRIBE {name}").fetchall()
        ]
        coverage = {"rows": connection.execute(f"SELECT count(*) FROM {name}").fetchone()[0]}
        if any(column["name"] == "date" for column in columns):
            span = connection.execute(f"SELECT min(date), max(date) FROM {name}").fetchone()
            coverage["start"] = _iso(span[0])
            coverage["end"] = _iso(span[1])
        tables.append({"table": name, "columns": columns, "coverage": coverage})
    return {
        "tables": tables,
        "dictionary": "data/DATA_DICTIONARY.md",
        "warnings": [SERIES_WARNING, OVERLAP_WARNING, REPORTED_WARNING],
    }


def run_sql(query: str) -> dict:
    """One read-only SELECT. At most 200 rows. Writes are rejected."""
    text = str(query or "").strip().rstrip(";")
    if not text or _FORBIDDEN.search(text) or not re.match(r"(?is)\s*(select|with)\b", text):
        return {"error": "Only a single SELECT is allowed.", "rows": [], "warnings": []}
    if text.count(";") > 0:
        return {"error": "Only a single SELECT is allowed.", "rows": [], "warnings": []}
    if (
        re.search(r"\bcount\s*\(", text, re.IGNORECASE)
        and re.search(r"\bincidents\b", text, re.IGNORECASE)
        and not re.search(r"\bgroup\s+by\b", text, re.IGNORECASE)
    ):
        return {
            "error": (
                "A total count of the incidents table is not the 2020-present headline. "
                "venues.present_records is that count. "
                "A breakdown can group incidents by offense_group or another column."
            ),
            "rows": [],
            "warnings": [],
        }
    unknown = _unknown_venue_id(text)
    if unknown:
        return {
            "error": (
                f"No venue has venue_id {unknown}. Ids are V01 through V14. "
                "Pass the venue name to explain_page for a page count, density, or weekday."
            ),
            "rows": [],
            "warnings": [],
        }
    wrapped = f"SELECT * FROM ({text}) AS agent_query LIMIT {ROW_LIMIT}"
    frame, error = _execute(wrapped)
    if error:
        if re.search(r"\bvenue\b", text, re.IGNORECASE) and "venue_id" not in text.lower():
            error = f"{error} The venue key is venue_id."
        if "not found" in error.lower() and re.search(r"rail|stop|station|fire|police|hospital|\bbus\b", text, re.IGNORECASE):
            error = (
                f"{error} Places within 800 m are in nearby "
                "(venue_id, venue_name, kind, name, distance_m). "
                "kind is rail, bus, fire, police, or hospital."
            )
        elif "not found" in error.lower():
            error = (
                f"{error} For a headline count, call explain_page with the venue name and topic overview. "
                "offense_counts.records is the group count. venues.present_records is the 2020-present total."
            )
        return {"error": error, "rows": [], "warnings": []}
    rows = _records(frame)
    return {
        "columns": list(frame.columns),
        "row_count": len(rows),
        "truncated": len(rows) >= ROW_LIMIT,
        "rows": rows,
        "warnings": _sql_warnings(text, frame),
    }


def correlate(sql_a: str, sql_b: str, lag_days: int = 0, control_weekday: bool = True) -> dict:
    """Align two date/value queries. Spearman, and a count model when weekday is held fixed."""
    left = _series(sql_a)
    right = _series(sql_b)
    if "error" in left:
        return left
    if "error" in right:
        return right
    lag = max(-30, min(30, int(lag_days or 0)))
    right["date"] = pd.to_datetime(right["date"]) + pd.to_timedelta(lag, unit="D")
    joined = left.merge(right, on="date", suffixes=("_a", "_b")).dropna()
    warnings = [ASSOCIATION_WARNING, REPORTED_WARNING, SPEARMAN_WARNING]
    if len(joined) < SMALL_N:
        warnings.append(SMALL_WARNING)
    if _spans_break(joined["date"]):
        warnings.append(SERIES_WARNING)
    if len(joined) < 8:
        return {"error": "Fewer than 8 aligned days.", "n": int(len(joined)), "warnings": warnings}
    rho = spearmanr(joined["value_a"], joined["value_b"])
    result = {
        "n": int(len(joined)),
        "lag_days": lag,
        "spearman": None if rho.statistic is None or pd.isna(rho.statistic) else round(float(rho.statistic), 3),
        "spearman_p": None if rho.pvalue is None or pd.isna(rho.pvalue) else round(float(rho.pvalue), 4),
        "warnings": warnings,
    }
    binary = _is_event_flag(joined["value_b"])
    if control_weekday and binary:
        rows = [
            {"date": day.date().isoformat(), "incident_count": int(count), "event": int(flag)}
            for day, count, flag in zip(joined["date"], joined["value_a"], joined["value_b"], strict=False)
        ]
        model = fit_count_model(rows, "event")
        weekday = weekday_standardized(rows, "event")
        result["weekday_adjusted"] = weekday
        result["count_model"] = model
        if model:
            result["count_model"]["interval"] = [model["low"], model["high"]]
    elif control_weekday:
        result["weekday_note"] = "Weekday control needs the second series to be 0 or 1. Spearman is still reported."
    return result


def live_weather(venue: str) -> dict:
    """Current conditions at the venue pin. Not applied to the historical comparison."""
    found = _summary_venue(venue)
    if found is None:
        return {"error": f"No venue matched {venue}.", "warnings": []}
    from backend.chat_v2 import _current_weather

    body = _current_weather(found)
    caveat = body.get("caveat") or ""
    return {"reading": body.get("answer"), "warnings": [caveat] if caveat else []}


def metro_alerts(venue: str) -> dict:
    """Current Metro notices on lines recorded near the venue."""
    found = _summary_venue(venue)
    if found is None:
        return {"error": f"No venue matched {venue}.", "warnings": []}
    from backend.metro_alerts import metro_reply

    body = metro_reply(found)
    caveat = body.get("caveat") or ""
    table = body.get("table") or {}
    return {
        "reading": body.get("answer"),
        "lines": table.get("rows") or [],
        "warnings": [caveat] if caveat else [],
    }


def compare_with_city(venue: str) -> dict:
    """Venue density against the citywide rate. The ratio is the comparison."""
    found = _summary_venue(venue)
    if found is None:
        return {"error": f"No venue matched {venue}.", "warnings": []}
    from backend.venues import _city_comparison, present_headlines

    present = present_headlines().get(found["venue_id"])
    compared = _city_comparison(found, present)
    if not compared:
        return {"error": "The citywide rate is not loaded.", "warnings": []}
    reports = compared.get("reports") or {}
    return {
        "venue": found["venue_name"],
        "venue_id": found["venue_id"],
        "reports_2020_2024": reports,
        "nibrs": compared.get("nibrs"),
        "present_density_per_km2": None if not present else present.get("crime_per_km2"),
        "warnings": [
            reports.get("hint") or "Venue circles are not added into the citywide total.",
            "A higher or lower rate than the city is a comparison of reported records. It is not a cause.",
        ],
    }


_PAGE_TOPICS = (
    "overview", "categories", "weekday", "months", "weather", "permits", "transit", "care", "source", "density",
)
_PAGE_SOURCE = (
    "LAPD crime reports via the LA Open Data Portal, then LAPD NIBRS offenses. "
    "Period: 2020–present. Circle: 800 m. This is the venue page headline, not the 2020–2024 report total."
)


def compare_conditions(venue: str = "", factor: str = "wet_day") -> dict:
    """Weekday-adjusted gap for wet days, hot days, or permit days. Not a cause."""
    field = _condition_field(factor)
    if field is None:
        return {
            "error": "factor is wet_day, hot_day, or is_permit_event_day.",
            "warnings": [ASSOCIATION_WARNING],
        }
    from backend.datasets import load_summary

    venues = load_summary()["venues"]
    key = str(venue or "").strip().lower()
    if key in {"", "all", "any", "every"}:
        rows = [_one_condition(item, field) for item in venues]
        clear = [row["venue"] for row in rows if row.get("clear_difference")]
        return {
            "factor": field,
            "clear_difference_at": clear,
            "venues": [
                {name: row[name] for name in ("venue", "event_day_mean", "other_day_mean", "event_days", "interval", "clear_difference")}
                for row in rows
            ],
            "warnings": [ASSOCIATION_WARNING, REPORTED_WARNING],
        }
    found = _summary_venue(venue)
    if found is None:
        return {"error": f"No venue matched {venue}.", "warnings": []}
    return _one_condition(found, field)


def rank_venues(metric: str = "records") -> dict:
    """Order the 14 venues by the 2020–present record count or by records per km²."""
    from backend.datasets import load_summary
    from backend.venues import present_headlines

    density = str(metric or "").strip().lower() in {"density", "rate", "per_km2", "crime_per_km2"}
    names = {item["venue_id"]: item["venue_name"] for item in load_summary()["venues"]}
    ranked = []
    for venue_id, row in present_headlines().items():
        ranked.append({
            "venue_id": venue_id,
            "venue": names.get(venue_id, venue_id),
            "records": int(row.get("count") or 0),
            "records_per_km2": row.get("crime_per_km2"),
        })
    ranked.sort(key=lambda item: item["records_per_km2"] if density else item["records"], reverse=True)
    return {
        "metric": "density" if density else "records",
        "period": "2020-present",
        "highest": ranked[0] if ranked else None,
        "lowest": ranked[-1] if ranked else None,
        "ranked": ranked,
        "warnings": [REPORTED_WARNING, OVERLAP_WARNING],
    }


def rank_nearby(kind: str = "rail") -> dict:
    """Which venues have the most places of one kind inside the 800 m circle. Ties stay in the list."""
    chosen = str(kind or "").strip().lower()
    aliases = {
        "rail": "rail", "station": "rail", "stations": "rail", "metro": "rail",
        "bus": "bus", "stops": "bus",
        "fire": "fire", "police": "police", "hospital": "hospital", "hospitals": "hospital",
    }
    field = aliases.get(chosen)
    if field is None:
        return {"error": "kind is rail, bus, fire, police, or hospital.", "warnings": []}
    counted = run_sql(
        "SELECT venue_name, count(*) AS places FROM nearby "
        f"WHERE kind = '{field}' GROUP BY venue_name ORDER BY places DESC"
    )
    rows = counted.get("rows") or []
    top = int(rows[0]["places"]) if rows else 0
    leaders = [row["venue_name"] for row in rows if int(row["places"]) == top]
    return {
        "kind": field,
        "within_m": 800,
        "highest_count": top,
        "tied": leaders,
        "ranked": rows,
        "warnings": [],
    }


def _condition_field(factor: str) -> str | None:
    key = str(factor or "wet_day").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "wet": "wet_day",
        "wet_day": "wet_day",
        "dry": "wet_day",
        "rain": "wet_day",
        "hot": "hot_day",
        "hot_day": "hot_day",
        "permit": "is_permit_event_day",
        "permits": "is_permit_event_day",
        "is_permit_event_day": "is_permit_event_day",
    }
    return aliases.get(key)


def _one_condition(venue: dict, field: str) -> dict:
    from pipeline.weather_compare import HOT_MEAN_F, load_joined_days, present_days, report_daily_counts

    present = present_days(
        load_joined_days().get(venue["venue_id"]) or [],
        report_daily_counts().get(venue["venue_id"]) or {},
    )
    flags = _condition_flags(venue["venue_id"], field) if field == "is_permit_event_day" else {}
    rows = []
    for row in present:
        if field == "wet_day":
            event = int(bool(row["wet_day"]))
        elif field == "hot_day":
            event = int(float(row["temp_f_mean"]) >= HOT_MEAN_F)
        else:
            event = int(flags.get(str(row["date"])[:10], 0))
        rows.append({"date": row["date"], "incident_count": row["incident_count"], "event": event})
    stats = weekday_standardized(rows, "event") if rows else None
    fitted = fit_count_model(rows, "event") if stats else None
    low = None if not fitted else fitted.get("low")
    high = None if not fitted else fitted.get("high")
    clear = bool(fitted) and low is not None and high is not None and not (low <= 1 <= high)
    label = {"wet_day": "wet days", "hot_day": "hot days"}.get(field, "permit days")
    if not stats:
        summary = f"Near {venue['venue_name']}, there are not enough {label} on matching weekdays to quote a gap."
    else:
        if fitted and low is not None and high is not None and low <= 1 <= high:
            lead = "No clear difference. "
        elif fitted and high is not None and high < 1:
            lead = "The count model stays below 1.00. "
        elif fitted and low is not None and low > 1:
            lead = "The count model stays above 1.00. "
        else:
            lead = ""
        summary = (
            f"{lead}Near {venue['venue_name']}, {label} average {stats['event_day_mean']:.2f} records "
            f"and other days of the same weekday average {stats['other_day_mean']:.2f}, "
            f"from {stats['event_day_count']} {label}."
        )
    return {
        "venue": venue["venue_name"],
        "venue_id": venue["venue_id"],
        "factor": field,
        "event_day_mean": None if not stats else round(float(stats["event_day_mean"]), 2),
        "other_day_mean": None if not stats else round(float(stats["other_day_mean"]), 2),
        "event_days": None if not stats else int(stats["event_day_count"]),
        "multiplier": None if not fitted else fitted.get("multiplier"),
        "interval": None if low is None or high is None else [low, high],
        "clear_difference": clear,
        "summary": summary,
        "warnings": [ASSOCIATION_WARNING, REPORTED_WARNING],
    }


def _condition_flags(venue_id: str, column: str) -> dict[str, int]:
    if column not in {"wet_day", "hot_day", "is_permit_event_day"}:
        return {}
    rows = connect().execute(
        f"SELECT CAST(date AS VARCHAR), max({column}) FROM venue_days WHERE venue_id = ? GROUP BY 1",
        [venue_id],
    ).fetchall()
    return {str(day)[:10]: int(flag or 0) for day, flag in rows}


def explain_page(venue: str = "", topic: str = "overview") -> dict:
    """Figures and definitions already printed on the venue page. No new calculation."""
    chosen = _page_topic(topic)
    found = _summary_venue(venue) if str(venue or "").strip() else None
    if str(venue or "").strip() and found is None:
        return {"error": f"No venue matched {venue}.", "topics": list(_PAGE_TOPICS), "warnings": []}
    detail = None
    if found is not None:
        from backend.venues import get_venue

        detail = get_venue(found["venue_id"])
    entries = _page_entries(detail, chosen)
    warnings = []
    if chosen == "categories":
        warnings.append(LABEL_WARNING)
    if chosen in {"overview", "density", "source"}:
        warnings.append(REPORTED_WARNING)
    return {
        "venue": None if detail is None else detail.get("venue_name"),
        "topic": chosen,
        "topics": list(_PAGE_TOPICS),
        "entries": entries,
        "warnings": warnings,
    }


def _page_topic(topic: str) -> str:
    text = str(topic or "overview").strip().lower().replace("-", " ")
    aliases = {
        "incident": "overview",
        "incidents": "overview",
        "headline": "overview",
        "page": "overview",
        "density": "density",
        "city": "overview",
        "category": "categories",
        "categories": "categories",
        "types": "categories",
        "groups": "categories",
        "weekday": "weekday",
        "weekend": "weekday",
        "day of week": "weekday",
        "month": "months",
        "months": "months",
        "weather": "weather",
        "wet": "weather",
        "hot": "weather",
        "permit": "permits",
        "permits": "permits",
        "home games": "permits",
        "transit": "transit",
        "rail": "transit",
        "bus": "transit",
        "metro": "transit",
        "fire": "care",
        "police": "care",
        "hospital": "care",
        "care": "care",
        "source": "source",
        "overlap": "overview",
    }
    return aliases.get(text, "overview")


def _page_entries(detail: dict | None, topic: str) -> list[dict]:
    if topic == "density":
        entries = [_density_entry(detail)]
        return entries
    if topic == "source":
        return [_entry("Source", None, _PAGE_SOURCE, _PAGE_SOURCE)]
    if topic == "categories":
        return _category_entries(detail)
    if topic == "weekday":
        return _weekday_entries(detail)
    if topic == "months":
        return _month_entries(detail)
    if topic == "weather":
        return _weather_entries(detail)
    if topic == "permits":
        return _permit_entries(detail)
    if topic == "transit":
        return _transit_entries(detail)
    if topic == "care":
        return _care_entries(detail)
    return _overview_entries(detail)


def _entry(label: str, value, means: str, source: str) -> dict:
    return {"label": label, "value": value, "means": means, "source": source}


def _overview_entries(detail: dict | None) -> list[dict]:
    if detail is None:
        return [
            _entry("Incidents", None, "Records inside the 800 m circle, 2020–present.", _PAGE_SOURCE),
            _density_entry(None),
        ]
    present = detail.get("present") or {}
    display = detail.get("display") or {}
    city = (detail.get("city_baseline") or {}).get("present") or {}
    rank = present.get("density_rank") or {}
    place = ""
    if rank:
        tied = "tied for " if rank.get("tied") else ""
        place = f" {tied}{rank.get('rank')} of {rank.get('of')}."
    entries = [
        _entry(
            "Incidents",
            present.get("count"),
            display.get("incident_note") or "inside the buffer, 2020–present",
            _PAGE_SOURCE,
        ),
        _entry(
            "Density",
            present.get("crime_per_km2"),
            f"{display.get('density_hint') or 'Records per square kilometer inside the circle.'}{place}",
            _PAGE_SOURCE,
        ),
    ]
    if city:
        entries.append(_entry("vs City", city.get("value"), city.get("summary") or city.get("caption") or "", city.get("hint") or ""))
    if present.get("peak_month"):
        entries.append(_entry(
            "Busiest month",
            present.get("peak_month"),
            f"{present.get('peak_count')} records. Red marks this month on the chart.",
            _PAGE_SOURCE,
        ))
    if display.get("overlap_note"):
        entries.append(_entry("Overlap", None, display["overlap_note"], "800 m circles. A report in the overlap is stored under each venue."))
    return entries


def _density_entry(detail: dict | None) -> dict:
    if detail is None:
        return _entry(
            "Density",
            None,
            "Records per square kilometer inside the 800 m circle, from 2020 through the latest data. The count is divided by the circle's area.",
            _PAGE_SOURCE,
        )
    return _overview_entries(detail)[1]


def _category_entries(detail: dict | None) -> list[dict]:
    if detail is None:
        return [_entry(
            "Incident types",
            None,
            "Offense groups for the records in view. Report labels and NIBRS labels are not the same series.",
            _PAGE_SOURCE,
        )]
    labels = detail.get("crime_groups") or {}
    totals: dict[str, int] = {}
    for groups in (detail.get("merged_by_month") or {}).values():
        for group, count in groups.items():
            label = labels.get(group) or group
            totals[label] = totals.get(label, 0) + int(count or 0)
    ranked = sorted(totals.items(), key=lambda item: item[1], reverse=True)[:8]
    if not ranked:
        return [_entry("Incident types", None, "No grouped records are loaded for this venue.", _PAGE_SOURCE)]
    return [
        _entry(label, count, "Offense group on the Incident Types list, 2020–present.", _PAGE_SOURCE)
        for label, count in ranked
    ]


def _weekday_entries(detail: dict | None) -> list[dict]:
    from backend.briefing import weekday_summary

    if detail is None:
        return [_entry("Day of week", None, "Monday through Sunday record counts, 2020–present.", _PAGE_SOURCE)]
    block = detail.get("merged_weekday") or {}
    return [_entry(
        "Day of week",
        weekday_summary(block, "records"),
        block.get("disclaimer") or "Monday through Sunday record counts, 2020–present.",
        _PAGE_SOURCE,
    )]


def _month_entries(detail: dict | None) -> list[dict]:
    if detail is None:
        return [_entry("Incidents by month", None, "Monthly record counts, 2020–present. Red is the busiest month in the selected range.", _PAGE_SOURCE)]
    present = detail.get("present") or {}
    return [_entry(
        "Incidents by month",
        present.get("peak_month"),
        f"Monthly record counts, 2020–present. The busiest month has {present.get('peak_count')} records. Red marks that month.",
        _PAGE_SOURCE,
    )]


def _weather_entries(detail: dict | None) -> list[dict]:
    from backend.briefing import weather_disclaimer, weather_intro

    means = weather_intro(85)
    source = weather_disclaimer()
    if detail is None:
        return [_entry("Weather", None, means, source)]
    from backend.venues import weather_comparison

    body = weather_comparison(detail["venue_id"]) or {}
    entries = [_entry("Weather", None, body.get("intro") or means, body.get("disclaimer") or source)]
    for block in body.get("comparisons") or []:
        for row in block.get("rows") or []:
            if not row.get("available"):
                continue
            entries.append(_entry(
                row.get("label") or "Weather",
                row.get("lift_pct"),
                (
                    f"Event days average {row.get('event_day_mean')} and other days in the same months "
                    f"average {row.get('other_day_mean')}, from {row.get('event_day_count')} event days. "
                    "The percent is shown only when the page shows it."
                ),
                body.get("disclaimer") or source,
            ))
    return entries


def _permit_entries(detail: dict | None) -> list[dict]:
    from backend.briefing import PERMIT_INTRO

    if detail is None:
        return [_entry("Permit days", None, PERMIT_INTRO, "LADBS temporary-event permits matched to the venue.")]
    from backend.venues import permit_comparison

    body = permit_comparison(detail["venue_id"]) or {}
    summary = body.get("summary") or {}
    entries = [_entry(
        "Permit days",
        summary.get("lift_pct"),
        (
            f"{body.get('intro') or PERMIT_INTRO} "
            f"Permit days average {summary.get('event_day_mean')} and other days in those months "
            f"average {summary.get('other_day_mean')}, from {summary.get('event_day_count')} permit days."
        ),
        body.get("source_note") or body.get("disclaimer") or "The page's permit table. A fresh query is a different calculation.",
    )]
    if detail.get("home_games_available"):
        from backend.venues import home_game_comparison

        games = home_game_comparison(detail["venue_id"]) or {}
        game_summary = games.get("summary") or {}
        entries.append(_entry(
            "Home games",
            game_summary.get("lift_pct"),
            f"Home-game days versus other days in those months. Mean on game days {game_summary.get('event_day_mean')}.",
            games.get("disclaimer") or "MLB home games at this venue. Not the permit table.",
        ))
    return entries


def _transit_entries(detail: dict | None) -> list[dict]:
    if detail is None:
        return [_entry("Transportation", None, "Metro rail stations and bus stops inside the 800 m circle. This is not a live alert.", "Static GTFS extracts.")]
    rail = detail.get("rail_stations_nearby") or {}
    bus = detail.get("bus_stops_nearby") or {}
    nearest = (rail.get("stations") or [None])[0] or {}
    rail_value = "None in the buffer." if not rail.get("count") else f"{rail.get('count')} stations. Nearest {nearest.get('station_name')} · {nearest.get('distance_m')} m"
    return [
        _entry("Rail", rail_value, "Stations inside the 800 m circle.", "Metro rail station extract. Not a live alert and not crime on a train."),
        _entry("Bus", f"{bus.get('count') or 0} stops · {len(bus.get('lines') or [])} lines", ", ".join(bus.get("lines") or []) or "No lines recorded.", "Metro bus stop extract."),
    ]


def _care_entries(detail: dict | None) -> list[dict]:
    if detail is None:
        return [_entry("Nearest response", None, "Nearest fire station, police station, and hospital by straight-line distance. Not a response time.", "Facility lists. Not a jurisdiction boundary.")]
    entries = []
    for label, key in (
        ("Fire", "nearest_fire_station"),
        ("Police", "nearest_police_station"),
        ("Hospital", "nearest_hospital"),
        ("Emergency room", "nearest_emergency_room"),
    ):
        station = detail.get(key) or {}
        if not station:
            continue
        name = station.get("station_name") or station.get("name") or ""
        entries.append(_entry(
            label,
            f"{name} · {station.get('distance_m')} m",
            "Straight-line distance from the venue pin. Not a response time or a boundary.",
            station.get("agency") or "Facility list.",
        ))
    return entries or [_entry("Nearest response", None, "No facility was loaded.", "Facility lists.")]


def forecast(venue: str, horizon: int, regressors: list[str] | None = None, scenario: dict | None = None) -> dict:
    """A multiplier for one recorded condition. Not a projection of future daily counts."""
    fields = [name for name in (regressors or []) if name in {"wet_day", "hot_day", "is_permit_event_day"}]
    if not fields:
        return {
            "error": "Name a regressor: wet_day, hot_day, or is_permit_event_day. Set scenario to 1 for that condition.",
            "games_period": "refused",
            "warnings": [FORECAST_WARNING],
        }
    scenario = scenario or {}
    requested = int(horizon or 30)
    if requested < 1 or requested > 90:
        return {
            "error": (
                f"A horizon of {requested} days was not applied. "
                "This tool does not project daily counts, and it does not clip a longer request to 90."
            ),
            "horizon_days_requested": requested,
            "games_period": "refused",
            "warnings": [FORECAST_WARNING],
        }
    frame = _venue_frame(venue)
    warnings = [FORECAST_WARNING, ASSOCIATION_WARNING, REPORTED_WARNING]
    if frame.empty:
        return {"error": f"No daily rows for {venue}.", "games_period": "refused", "warnings": warnings}
    recorded = frame[frame["incident_count"].notna()].reset_index(drop=True)
    if recorded.empty:
        return {"error": f"No crime days for {venue}.", "games_period": "refused", "warnings": warnings}
    if _spans_break(recorded["date"]):
        warnings.append(SERIES_WARNING)
    holdout = 60 if len(recorded) > 120 else max(14, len(recorded) // 5)
    train = recorded.iloc[:-holdout]
    hold = recorded.iloc[-holdout:]
    primary = fields[0]
    rows = _model_rows(train, primary)
    fitted = fit_count_model(rows, "event") if rows else None
    weekday = weekday_standardized(rows, "event") if rows else None
    backtest = _backtest(hold, primary, weekday, fitted, scenario, train)
    end = pd.to_datetime(recorded["date"]).max().date()
    return {
        "venue": venue,
        "result_kind": "condition_multiplier",
        "interval_means": "Range for the multiplier versus the other condition. Not a future daily count.",
        "horizon_days_requested": requested,
        "projects_daily_counts": False,
        "games_period": "refused",
        "crime_data_through": end.isoformat(),
        "regressor": primary,
        "scenario": {key: scenario.get(key) for key in fields},
        "count_model": fitted,
        "interval": [fitted["low"], fitted["high"]] if fitted else None,
        "weekday_adjusted": weekday,
        "backtest": backtest,
        "n": int(len(train)),
        "weather_rows_without_crime": int(len(frame) - len(recorded)),
        "warnings": warnings,
    }


def _unknown_venue_id(query: str) -> str:
    known = {row[0] for row in connect().execute("SELECT venue_id FROM venues").fetchall()}
    for found in re.findall(r"venue_id\s*=\s*'([^']*)'", query, re.IGNORECASE):
        if found not in known:
            return found
    return ""


def _execute(sql: str, timeout_s: float = 5.0):
    """Run one statement. Interrupt it if it is still going after timeout_s."""
    connection = connect()
    box: dict = {}

    def run() -> None:
        try:
            box["frame"] = connection.execute(sql).df()
        except Exception as exc:
            box["error"] = str(exc).splitlines()[0][:300]

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        connection.interrupt()
        worker.join(2)
        return None, "The query timed out."
    if box.get("error"):
        return None, box["error"]
    return box.get("frame"), None


def _summary_venue(venue: str) -> dict | None:
    from backend.datasets import load_summary

    key = str(venue or "").strip().lower()
    if not key:
        return None
    venues = load_summary()["venues"]
    exact = [
        item for item in venues
        if item["venue_id"].lower() == key or item["venue_name"].lower() == key
        or key in [name.lower() for name in item.get("former_names") or []]
    ]
    if len(exact) == 1:
        return exact[0]
    partial = [
        item for item in venues
        if key in item["venue_name"].lower() or any(key in name.lower() for name in item.get("former_names") or [])
    ]
    return partial[0] if len(partial) == 1 else None


def _venue_frame(venue: str) -> pd.DataFrame:
    key = str(venue or "").strip()
    connection = connect()
    query = """
        SELECT date, incident_count, wet_day, hot_day, is_permit_event_day
        FROM venue_days
        WHERE venue_id = ? OR lower(venue_name) = lower(?)
        ORDER BY date
    """
    frame = connection.execute(query, [key, key]).df()
    if not frame.empty:
        return frame
    matches = connection.execute(
        """
        SELECT venue_id FROM venues
        WHERE lower(venue_name) LIKE '%' || lower(?) || '%'
           OR lower(former_name) LIKE '%' || lower(?) || '%'
        """,
        [key],
    ).fetchall()
    if len(matches) != 1:
        return frame
    return connection.execute(query, [matches[0][0], matches[0][0]]).df()


def _model_rows(frame: pd.DataFrame, field: str) -> list[dict]:
    rows = []
    for record in frame.to_dict("records"):
        day = _iso(record.get("date"))
        if not day:
            continue
        rows.append({
            "date": day,
            "incident_count": int(record.get("incident_count") or 0),
            "event": int(record.get(field) or 0),
        })
    return rows


def _is_event_flag(values: pd.Series) -> bool:
    """True only when every value is exactly 0 or 1. Rounding is not enough."""
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if numeric.empty:
        return False
    return bool(((numeric == 0) | (numeric == 1)).all())


def _backtest(hold: pd.DataFrame, field: str, weekday: dict | None, fitted: dict | None, scenario: dict, train: pd.DataFrame) -> dict:
    scored = hold[hold["incident_count"].notna()]
    if scored.empty or not weekday:
        return {"holdout_days": 0, "scored_days": 0, "mae": None, "baseline_mae": None}
    level = float(weekday["other_day_mean"])
    multiplier = float(fitted["multiplier"]) if fitted else 1.0
    wanted = scenario.get(field)
    train_counts = pd.to_numeric(train["incident_count"], errors="coerce").dropna()
    constant = float(train_counts.mean()) if not train_counts.empty else level
    errors = []
    baseline_errors = []
    for record in scored.to_dict("records"):
        actual = float(record.get("incident_count") or 0)
        flag = int(record.get(field) or 0)
        use_multiplier = wanted is None or int(wanted) == flag
        predicted = level * multiplier if use_multiplier and flag else level
        errors.append(abs(actual - predicted))
        baseline_errors.append(abs(actual - constant))
    mae = round(sum(errors) / len(errors), 2) if errors else None
    baseline_mae = round(sum(baseline_errors) / len(baseline_errors), 2) if baseline_errors else None
    return {
        "holdout_days": int(len(scored)),
        "scored_days": int(len(scored)),
        "mae": mae,
        "baseline_mae": baseline_mae,
        "baseline": "training daily mean",
        "baseline_daily_mean": round(constant, 2),
    }


def _series(query: str) -> dict | pd.DataFrame:
    result = run_sql(query)
    if result.get("error"):
        return {"error": result["error"], "warnings": result.get("warnings") or []}
    frame = pd.DataFrame(result["rows"])
    if frame.empty or "date" not in frame.columns:
        return {
            "error": "Return a date column and one numeric column. Alias that number AS value when the query has more than one number.",
            "warnings": [],
        }
    numeric = [
        name for name in frame.columns
        if name != "date" and pd.to_numeric(frame[name], errors="coerce").notna().any()
    ]
    if "value" in numeric:
        value_column = "value"
    elif len(numeric) == 1:
        value_column = numeric[0]
    else:
        return {
            "error": "Return a date column and one numeric column. Alias that number AS value when the query has more than one number.",
            "warnings": [],
        }
    out = pd.DataFrame({
        "date": pd.to_datetime(frame["date"], errors="coerce"),
        "value": pd.to_numeric(frame[value_column], errors="coerce"),
    }).dropna()
    return out


def _sql_warnings(query: str, frame: pd.DataFrame) -> list[str]:
    warnings = []
    lowered = query.lower()
    dates = pd.to_datetime(frame["date"], errors="coerce").dropna() if "date" in frame.columns else pd.Series(dtype="datetime64[ns]")
    if _spans_break(dates) or ("venue_days" in lowered and "count_source" not in lowered and "incident_count" in lowered):
        if _spans_break(dates) or "count_source" not in lowered:
            if "incident_count" in lowered or "incidents" in lowered:
                warnings.append(SERIES_WARNING)
    if "incidents" in lowered and lowered.count("venue_id") < 1:
        warnings.append(OVERLAP_WARNING)
    elif "venue_id" in frame.columns and frame["venue_id"].nunique() > 1 and "incident" in lowered:
        warnings.append(OVERLAP_WARNING)
    if re.search(r"\b(incident_count|incidents|offense_counts)\b", lowered):
        warnings.append(REPORTED_WARNING)
    if "offense_counts" in lowered or "offense_group" in lowered:
        warnings.append(LABEL_WARNING)
    aggregate = re.search(r"\b(sum|count|avg|min|max)\s*\(", lowered)
    if not aggregate and 0 < len(frame) < SMALL_N:
        warnings.append(SMALL_WARNING)
    return list(dict.fromkeys(warnings))


def _spans_break(dates: pd.Series) -> bool:
    if dates is None or len(dates) == 0:
        return False
    parsed = pd.to_datetime(dates, errors="coerce").dropna()
    if parsed.empty:
        return False
    cut = pd.Timestamp(SERIES_BREAK)
    return bool((parsed < cut).any() and (parsed >= cut).any())


def _records(frame: pd.DataFrame) -> list[dict]:
    copied = frame.copy()
    for column in copied.columns:
        if pd.api.types.is_datetime64_any_dtype(copied[column]):
            copied[column] = copied[column].dt.strftime("%Y-%m-%d")
    return json_ready(copied.where(copied.notna(), None).to_dict("records"))


def json_ready(rows: list[dict]) -> list[dict]:
    cleaned = []
    for row in rows:
        item = {}
        for key, value in row.items():
            if hasattr(value, "item"):
                value = value.item()
            if isinstance(value, date):
                value = value.isoformat()
            item[str(key)] = value
        cleaned.append(item)
    return cleaned


def _iso(value) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return str(value)[:10]
