"""Chat v2. Templates and caveats come from the server. Torchy is unchanged.

The local parser runs first. A narration is optional, and it is dropped when it
names another venue, reverses a direction, or adds a number or a cause.
"""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from backend.briefing import HOT_ROW, PERMIT_INTRO, WET_ROW, weather_hints
from backend.chat import (
    PRESENT_PROVENANCE,
    _FOLLOW_WORDS,
    _hard_block,
    _mentions,
    _normalize,
    _question_text,
    answer_deterministic,
    answer_routed,
)
from backend.claude import ClaudeUnavailable, explain_figures, explanation_uses_only, interpret_question, settings
from backend.datasets import load_summary
from backend.metro_alerts import metro_reply
from backend.event_baseline import fit_count_model, weekday_standardized
from backend.store import get_venue, home_game_comparison, permit_comparison
from pipeline.weather import WET_CODES
from pipeline.weather_compare import HOT_MEAN_F, load_joined_days, present_days, report_daily_counts

_LA = ZoneInfo("America/Los_Angeles")
_UP = re.compile(r"\b(rose|risen|increased|increase|higher|more)\b", re.IGNORECASE)
_DOWN = re.compile(r"\b(fell|fallen|decreased|decrease|lower|fewer)\b", re.IGNORECASE)
_CAUSE = re.compile(r"\b(because|due to|leads to|driven by|cause|causes|caused)\b", re.IGNORECASE)
_PATTERN = {"top_groups", "weekend", "weekend_groups", "rose"}
_RECORDED = {
    "present_total", "total", "density", "city", "busiest_month", "year_count",
    "group_count", "nibrs_total", "since_count", "citywide", "top_category",
    "compare", "time_of_day", "period", "period_groups", "rank_group", "rank_count",
    "rail", "bus", "transit", "fire", "police", "hospital", "services", "sports",
}


def _table_source(template: str, table: dict | None) -> tuple[str, list[str]]:
    """Numbers and labels the narration may repeat. Table cells count, as in v1."""
    parts = [template]
    labels: list[str] = []
    if not table:
        return template, labels
    columns = [str(column) for column in table.get("columns") or []]
    cells = [str(cell) for row in table.get("rows") or [] for cell in row]
    parts.extend(columns)
    parts.extend(cells)
    labels.extend(columns)
    labels.extend(cell for cell in cells if not re.fullmatch(r"[\d,% ]+", cell.strip()))
    return " ".join(parts), labels


def narration_ok(
    text: str,
    template: str,
    allowed_venue_ids: list[str],
    venues: list[dict],
    table: dict | None = None,
) -> bool:
    """True when the sentence stays inside this turn's template and table."""
    if not text or _CAUSE.search(text):
        return False
    if _UP.search(text) and not _UP.search(template):
        return False
    if _DOWN.search(text) and not _DOWN.search(template):
        return False
    source, labels = _table_source(template, table)
    labels.extend(venue["venue_name"] for venue in venues if venue["venue_id"] in set(allowed_venue_ids))
    if not explanation_uses_only(text, source, labels):
        return False
    named = {
        venue["venue_id"]
        for _start, _end, candidates in _mentions(_normalize(text), venues)
        for venue in candidates
    }
    return not named or named <= set(allowed_venue_ids)


def _split_caveat(answer: str) -> tuple[str, str]:
    index = -1
    for marker in ("\n\nSource:", "\n\nCrime context only"):
        at = answer.find(marker)
        if at != -1 and (index == -1 or at < index):
            index = at
    if index == -1:
        return answer.strip(), ""
    return answer[:index].strip(), answer[index:].strip()


def _links(venue_id: str | None, venue_name: str | None) -> list[dict]:
    if not venue_id:
        return [{"href": "#/compare", "label": "Open Compare"}]
    name = venue_name or "this venue"
    return [
        {"href": f"#/venue/{venue_id}", "label": f"Open {name}"},
        {"href": f"#/venue/{venue_id}/weekday", "label": "Day of week"},
        {"href": f"#/venue/{venue_id}/months", "label": "Incidents by month"},
        {"href": f"#/venue/{venue_id}/categories", "label": "Incident types"},
    ]


def _confidence(intent: str | None, caveat: str) -> dict:
    if intent == "seasonal_estimate":
        return {"kind": "seasonal", "text": "Seasonal estimate, not a recorded count."}
    if intent == "event_lift":
        return {"kind": "association", "text": "Association. A range, when shown, is the count-model interval."}
    if intent == "weather_association":
        return {"kind": "association", "text": "Weekday-adjusted association. Not a statement about today's weather."}
    if intent == "current_weather":
        return {"kind": "live", "text": caveat or "Current conditions."}
    if intent in _RECORDED or intent in _PATTERN:
        return {"kind": "recorded", "text": "Recorded count."}
    return {"kind": "none", "text": ""}


def _weather_mode(message: str) -> str | None:
    current = bool(re.search(
        r"\b(right now|currently|current weather|weather now|raining now|is it raining|temperature now)\b",
        message,
    ))
    if re.search(r"\b(now|today|tonight)\b", message) and re.search(r"\b(weather|rain|raining|temperature)\b", message):
        current = True
    historical = bool(re.search(r"\b(wet days?|dry days?|hot days?|cooler days?)\b", message))
    weather_word = re.search(r"\b(weather|rain|raining|precipitation|wet|hot)\b", message)
    crime_word = re.search(r"\b(crime|crimes|records?|incidents?|correlation|gap)\b", message)
    if weather_word and crime_word:
        historical = True
    if current and historical:
        return "both"
    if current:
        return "current"
    if historical:
        return "historical"
    return None


def _one_venue(message: str, venue_id: str | None, venues: list[dict]) -> tuple[dict | None, dict | None]:
    mentions = _mentions(_normalize(message), venues)
    for start, end, candidates in mentions:
        if len(candidates) > 1:
            names = ", ".join(venue["venue_name"] for venue in candidates)
            return None, {
                "status": "clarification",
                "answer": f"Which venue do you mean by “{_normalize(message)[start:end]}”? {names}.",
                "caveat": "",
                "choices": [
                    {
                        "venue_id": venue["venue_id"],
                        "venue_name": venue["venue_name"],
                        "message": _normalize(message)[:start] + _normalize(venue["venue_name"]) + _normalize(message)[end:],
                    }
                    for venue in candidates
                ],
                "tool": "clarify",
                "arguments": {},
                "results": [],
                "links": [],
            }
    resolved = {items[0]["venue_id"]: items[0] for _start, _end, items in mentions}
    if not resolved and venue_id:
        context = next((venue for venue in venues if venue["venue_id"] == venue_id), None)
        if context is None:
            return None, {
                "status": "clarification",
                "answer": "That selected venue is not in the processed roster. Please name a venue.",
                "caveat": "",
                "choices": [],
                "tool": "clarify",
                "arguments": {},
                "results": [],
                "links": [],
            }
        resolved[venue_id] = context
    if len(resolved) != 1:
        return None, {
            "status": "clarification",
            "answer": "Which venue do you mean? Select a venue below or include its full name.",
            "caveat": "",
            "choices": [
                {
                    "venue_id": venue["venue_id"],
                    "venue_name": venue["venue_name"],
                    "message": f"{message} near {venue['venue_name']}",
                }
                for venue in venues
            ],
            "tool": "clarify",
            "arguments": {},
            "results": [],
            "links": _links(None, None),
        }
    return next(iter(resolved.values())), None


def _prepare(message: str) -> str:
    """v2 only. The count word on the page is records; the parser still says incidents."""
    return re.sub(r"\brecords\b", "incidents", message, flags=re.IGNORECASE)


def _guide_entries() -> tuple:
    hints = weather_hints()
    return (
        ("wet", r"\b(?:wet|dry) days?\b", "Weather", hints[WET_ROW], ""),
        ("hot", r"\b(?:hot|cooler) days?\b", "Weather", hints[HOT_ROW], ""),
        ("permit", r"\bpermits?\b", "Permit days", PERMIT_INTRO, ""),
        ("weekday", r"\bday of week\b", "Day of week", "Monday through Sunday record counts, 2020–present.", "weekday"),
        ("months", r"\b(?:by month|monthly|incidents by month)\b", "Incidents by month", "Monthly record counts, 2020–present. Red is the busiest month in the selected range.", "months"),
        ("categories", r"\b(?:incident types?|offense groups?|categories)\b", "Incident types", "Offense groups for the records in view.", "categories"),
        ("density", r"\bdensit", "Density", "Records per square kilometer inside the 800 m circle, 2020–present.", ""),
        ("source", r"\bsource\b|\b800 ?m\b", "Source", PRESENT_PROVENANCE, ""),
    )


def _matching_guide(message: str) -> dict | None:
    for topic_id, pattern, title, text, section in _guide_entries():
        if re.search(pattern, message):
            return {"id": topic_id, "title": title, "text": text, "section": section}
    return None


def _definition_request(message: str) -> dict | None:
    """A question that names a page label. A data question stays on the parser."""
    if re.search(r"\b(look like|highest|data say|how many)\b", message):
        return None
    defines = re.search(
        r"\b(mean|means|meaning|definition)\b|\bwhat is the source\b|\bsource of\b|\bwhich section\b|\bwhere (?:is|are) the\b",
        message,
    )
    if not defines:
        return None
    return _matching_guide(message)


def _guide_after_miss(message: str) -> dict | None:
    """Page help after the parser misses. Crime questions are not given a blurb."""
    if re.search(r"\b(crime|crimes|robber|incident|incidents|records|highest|look like)\b", message):
        return None
    if not re.search(
        r"\b(what does|what do|where (?:is|are|can)|which section|what is the source|source of)\b",
        message,
    ):
        return None
    return _matching_guide(message) or {
        "id": "page",
        "title": "Venue page",
        "text": "Counts, weather, permits, and the source note are on the venue page.",
        "section": "",
    }


def _weather_prediction(message: str) -> bool:
    """True when the question asks weather to change crime. A live reading is not that."""
    forecast = re.search(r"\b(will|predict|prediction|forecast|cause|causes|caused)\b", message)
    weather = re.search(r"\b(rain|raining|weather|wet|hot|temperature|precip)\b", message)
    crime = re.search(r"\b(crime|crimes|records?|incidents?|lower|raise|reduce|increase)\b", message)
    return bool(forecast and weather and crime)


def _event_confidence(venue_id: str, message: str) -> dict:
    """The count-model range, separate from the published table percent."""
    want_home = bool(re.search(r"\bhome\b", message))
    want_permit = bool(re.search(r"\bpermit", message)) or not want_home
    intervals = []
    if want_permit:
        view = permit_comparison(venue_id) or {}
        model = ((view.get("baseline") or {}).get("model")) or {}
        if model.get("multiplier") is not None:
            intervals.append({"label": "Permit days", **{key: model[key] for key in ("multiplier", "low", "high")}})
    if want_home:
        games = home_game_comparison(venue_id) or {}
        model = ((games.get("baseline") or {}).get("model")) or {}
        if model.get("multiplier") is not None:
            intervals.append({"label": "Home games", **{key: model[key] for key in ("multiplier", "low", "high")}})
    if not intervals:
        return {"kind": "association", "text": "Association. The published table percent is not a forecast."}
    text = "; ".join(
        f"{item['label']} {item['multiplier']:.2f} ({item['low']:.2f}–{item['high']:.2f})"
        for item in intervals
    )
    return {"kind": "interval", "text": text, "intervals": intervals}


def narrate_answer(payload: dict) -> str | None:
    """One narration for a template that is already on screen."""
    if payload.get("tool") not in _PATTERN or not payload.get("table"):
        return None
    venues = load_summary()["venues"]
    venue_ids = [row["venue_id"] for row in payload.get("results") or [] if row.get("venue_id")]
    return _narrate(payload.get("tool"), payload.get("answer") or "", payload.get("table"), venue_ids, venues)


def _envelope(body: dict, venues: list[dict], message: str = "", *, narrate: bool = True) -> dict:
    template, caveat = _split_caveat(body.get("answer") or "")
    results = body.get("results") or []
    venue_ids = list(dict.fromkeys(row["venue_id"] for row in results if row.get("venue_id")))
    intent = body.get("question_type")
    narration = _narrate(intent, template, body.get("table"), venue_ids, venues) if narrate else None
    name = results[0]["venue_name"] if len(venue_ids) == 1 else None
    confidence = _event_confidence(venue_ids[0], message) if intent == "event_lift" and len(venue_ids) == 1 else _confidence(intent, caveat)
    links = []
    if body.get("table", {}) and body["table"].get("href"):
        links.append({"href": body["table"]["href"], "label": body["table"].get("link_label") or "Open this section"})
    if body.get("status") == "unsupported":
        links = _links(venue_ids[0] if venue_ids else None, name)
    return {
        "version": "v2",
        "status": body.get("status"),
        "answer": template,
        "caveat": caveat,
        "confidence": confidence,
        "narration": narration,
        "table": body.get("table"),
        "links": links,
        "choices": body.get("choices") or [],
        "results": results,
        "tool": intent,
        "arguments": {"venue_id": venue_ids[0]} if len(venue_ids) == 1 else {"venue_ids": venue_ids},
        "engine": "v2",
    }


def _narrate(intent: str | None, template: str, table: dict | None, venue_ids: list[str], venues: list[dict]) -> str | None:
    if intent not in _PATTERN or not table or not settings()["claude_configured"]:
        return None
    name = next((venue["venue_name"] for venue in venues if venue["venue_id"] in venue_ids), "")
    try:
        text = explain_figures(name, template, table.get("columns") or [], table.get("rows") or [])
    except ClaudeUnavailable:
        return None
    if not narration_ok(text, template, venue_ids, venues, table):
        return None
    return text


def current_conditions(latitude: float, longitude: float) -> dict | None:
    """One Open-Meteo forecast reading. None when the feed does not answer."""
    try:
        with httpx.Client(timeout=httpx.Timeout(8.0, connect=3.0)) as client:
            response = client.get("https://api.open-meteo.com/v1/forecast", params={
                "latitude": latitude,
                "longitude": longitude,
                "current": "temperature_2m,precipitation,weather_code",
                "temperature_unit": "fahrenheit",
                "precipitation_unit": "inch",
                "timezone": "America/Los_Angeles",
            })
            response.raise_for_status()
        current = response.json().get("current") or {}
        if "temperature_2m" not in current:
            return None
        return current
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None


def _current_weather(venue: dict) -> dict:
    reading = current_conditions(float(venue["latitude"]), float(venue["longitude"]))
    name = venue["venue_name"]
    if reading is None:
        caveat = "The current weather feed is unavailable. No condition was filled in."
        return {
            "status": "answered",
            "answer": f"Current weather at {name} could not be read.",
            "caveat": caveat,
            "confidence": {"kind": "live", "text": "Feed unavailable."},
            "tool": "current_weather",
        }
    when = str(reading.get("time") or datetime.now(_LA).strftime("%Y-%m-%dT%H:%M"))
    temp = reading["temperature_2m"]
    precip = float(reading.get("precipitation") or 0)
    code = reading.get("weather_code")
    wet = precip > 0 or (code is not None and int(code) in WET_CODES)
    sky = "Precipitation is falling." if wet else "No precipitation in this reading."
    caveat = (
        f"Fetched at {when} America/Los_Angeles. "
        "Current conditions at the venue pin. This is not applied to the historical weather comparison "
        "and it is not a crime forecast."
    )
    return {
        "status": "answered",
        "answer": f"At {name} the current reading is {temp}°F. {sky} Precipitation is {precip:.2f} inches.",
        "caveat": caveat,
        "confidence": {"kind": "live", "text": f"Fetched at {when}."},
        "tool": "current_weather",
    }


def _covers_one(model: dict | None) -> bool | None:
    if not model or model.get("low") is None or model.get("high") is None:
        return None
    return float(model["low"]) <= 1 <= float(model["high"])


def _gap_sentence(name: str, label: str, other: str, stats: dict | None, model: dict | None) -> str:
    """Lead with the count model. The weekday percent is a different adjustment."""
    if not stats:
        return (
            f"Near {name}, there are not enough {label.lower()} on matching weekdays "
            f"to quote a weekday-adjusted gap against {other.lower()}."
        )
    covered = _covers_one(model)
    if covered is True:
        lead = "No clear difference. "
    elif covered is False and float(model["high"]) < 1:
        lead = "The count model stays below 1.00. "
    elif covered is False:
        lead = "The count model stays above 1.00. "
    else:
        lead = ""
    return (
        f"{lead}Near {name}, {label.lower()} average {stats['event_day_mean']:.2f} records "
        f"and other {other.lower()} of the same weekday average {stats['other_day_mean']:.2f}, "
        f"from {stats['event_day_count']} {label.lower()}."
    )


_weather_fit: dict[tuple, dict | None] = {}


def _fit_weather(rows: list[dict], kind: str) -> dict | None:
    key = (kind, tuple((row["date"], int(row["incident_count"]), int(row["event"])) for row in rows))
    if key not in _weather_fit:
        _weather_fit[key] = fit_count_model(rows, "event")
    return _weather_fit[key]


def _interval_confidence(fitted: list[tuple[str, dict | None]]) -> dict:
    """Same count-model range the event answers show. A small gap does not look exact."""
    intervals = []
    for label, model in fitted:
        if model and model.get("multiplier") is not None:
            intervals.append({
                "label": label,
                "multiplier": model["multiplier"],
                "low": model["low"],
                "high": model["high"],
            })
    if not intervals:
        return {
            "kind": "association",
            "text": "Weekday-adjusted association. No count-model interval. Not a statement about today's weather.",
        }
    text = "; ".join(
        f"{item['label']} {item['multiplier']:.2f}× ({item['low']:.2f}–{item['high']:.2f})"
        for item in intervals
    )
    if all(item["low"] <= 1 <= item["high"] for item in intervals):
        text = f"No clear difference. {text}"
    return {"kind": "interval", "text": text, "intervals": intervals}


def _historical_weather(message: str, venue: dict) -> dict:
    joined = load_joined_days().get(venue["venue_id"]) or []
    reports = report_daily_counts().get(venue["venue_id"]) or {}
    present = present_days(joined, reports)
    want_wet = bool(re.search(r"\b(wet|dry|weather|rain)\b", message))
    want_hot = bool(re.search(r"\b(hot|cooler)\b", message))
    if not want_wet and not want_hot:
        want_wet = True
    parts = []
    fitted = []
    if want_wet:
        rows = [
            {"date": row["date"], "incident_count": row["incident_count"], "event": int(bool(row["wet_day"]))}
            for row in present
        ]
        stats = weekday_standardized(rows, "event")
        fitted_wet = _fit_weather(rows, "wet") if stats else None
        parts.append(_gap_sentence(venue["venue_name"], "Wet days", "days", stats, fitted_wet))
        fitted.append(("Wet days", fitted_wet))
    if want_hot:
        rows = [
            {
                "date": row["date"],
                "incident_count": row["incident_count"],
                "event": int(row["temp_f_mean"] >= HOT_MEAN_F),
            }
            for row in present
        ]
        stats = weekday_standardized(rows, "event")
        fitted_hot = _fit_weather(rows, "hot") if stats else None
        parts.append(_gap_sentence(venue["venue_name"], "Hot days", "days", stats, fitted_hot))
        fitted.append(("Hot days", fitted_hot))
    caveat = (
        "Weekday-adjusted association inside the 800 m buffer. "
        "This is not a cause, not a forecast, and not a statement about today's weather."
    )
    if want_wet and want_hot:
        facet = "both"
    elif want_hot:
        facet = "hot"
    else:
        facet = "wet"
    return {
        "status": "answered",
        "answer": " ".join(parts),
        "caveat": caveat,
        "confidence": _interval_confidence(fitted),
        "tool": "weather_association",
        "facet": facet,
    }


def _finish_weather(payload: dict, venue: dict) -> dict:
    return {
        "version": "v2",
        "status": payload["status"],
        "answer": payload["answer"],
        "caveat": payload["caveat"],
        "confidence": payload["confidence"],
        "narration": None,
        "table": None,
        "links": _links(venue["venue_id"], venue["venue_name"]),
        "choices": payload.get("choices") or [],
        "results": [{"venue_id": venue["venue_id"], "venue_name": venue["venue_name"]}],
        "tool": payload["tool"],
        "arguments": {"venue_id": venue["venue_id"], **({"facet": payload["facet"]} if payload.get("facet") else {})},
        "engine": "v2",
    }


def _finish_live(payload: dict, venue: dict) -> dict:
    return {
        "version": "v2",
        "status": payload["status"],
        "answer": payload["answer"],
        "caveat": payload["caveat"],
        "confidence": payload["confidence"],
        "narration": None,
        "table": payload.get("table"),
        "links": _links(venue["venue_id"], venue["venue_name"]),
        "choices": [],
        "results": [{"venue_id": venue["venue_id"], "venue_name": venue["venue_name"]}],
        "tool": payload["tool"],
        "arguments": {"venue_id": venue["venue_id"]},
        "engine": "v2",
    }


def _blank(status: str, answer: str, caveat: str, venue_id: str | None, tool: str | None) -> dict:
    return {
        "version": "v2",
        "status": status,
        "answer": answer,
        "caveat": caveat,
        "confidence": {"kind": "none", "text": ""},
        "narration": None,
        "table": None,
        "links": _links(venue_id, None),
        "choices": [],
        "results": [],
        "tool": tool,
        "arguments": {"venue_id": venue_id} if venue_id else {},
        "engine": "v2",
    }


def _route_miss(message: str, venue_id: str | None, venues: list[dict], body: dict) -> dict:
    """One model call, only after the parser misses and the question is in scope."""
    if body.get("status") != "unsupported" or _hard_block(_normalize(message)) or not settings()["claude_configured"]:
        return body
    try:
        interpretation = interpret_question(message, [{"venue_id": venue["venue_id"], "venue_name": venue["venue_name"]} for venue in venues], venue_id)
    except ClaudeUnavailable:
        return body
    if not interpretation.scope_supported or interpretation.intent == "unsupported":
        return body
    if interpretation.intent == "tool" and interpretation.tool:
        routed = answer_routed(message, venue_id, tool=interpretation)
    else:
        routed = answer_routed(message, venue_id, intent=interpretation.intent)
    return body if routed.get("status") == "unsupported" else routed


def _stamp(early: dict) -> dict:
    early.update(version="v2", narration=None, table=None, confidence={"kind": "none", "text": ""}, engine="v2")
    return early


def _guide_reply(guide: dict, message: str, venue_id: str | None, venues: list[dict]) -> dict:
    venue, early = _one_venue(message, venue_id, venues)
    if early and not venue_id and not _mentions(_normalize(message), venues):
        venue = None
    elif early and venue is None:
        return _stamp(early)
    chosen = venue["venue_id"] if venue else venue_id
    name = venue["venue_name"] if venue else None
    href = f"#/venue/{chosen}/{guide['section']}".rstrip("/") if chosen else "#/compare"
    label = guide["title"] if chosen else "Open Compare"
    arguments = {"topic": guide["id"]}
    if chosen:
        arguments["venue_id"] = chosen
    return {
        "version": "v2",
        "status": "answered",
        "answer": f"{guide['title']}. {guide['text']}",
        "caveat": "This describes the section. It is not a new count.",
        "confidence": {"kind": "recorded", "text": "Page guide."},
        "narration": None,
        "table": None,
        "links": [{"href": href, "label": label}],
        "choices": [],
        "results": [{"venue_id": chosen, "venue_name": name}] if chosen and name else [],
        "tool": "page_guide",
        "arguments": arguments,
        "engine": "v2",
    }


def _split_weather() -> dict:
    return {
        "version": "v2",
        "status": "unsupported",
        "answer": (
            "Ask about today's weather, or about the weekday-adjusted wet-day and hot-day gaps, in separate questions. "
            "A live reading is not applied to the historical gap."
        ),
        "caveat": "This is not a cause and not a forecast.",
        "confidence": {"kind": "none", "text": ""},
        "narration": None,
        "table": None,
        "links": [],
        "choices": [],
        "results": [],
        "tool": None,
        "arguments": {},
        "engine": "v2",
    }


def _where_highest(message: str) -> bool:
    return bool(
        re.search(r"\bwhere\b", message)
        and re.search(r"\b(highest|most|concentrat)\b", message)
        and re.search(r"\b(crime|crimes|incidents?|records?)\b", message)
    )


def _distance_reply(venue: dict) -> dict | None:
    detail = get_venue(venue["venue_id"]) or {}
    bands = (detail.get("merged_distance") or {}).get("bands") or []
    if not bands:
        return None
    total = sum(int(band.get("count") or 0) for band in bands)
    top = max(bands, key=lambda band: (int(band.get("count") or 0), band.get("label") or ""))
    rows = [[str(band.get("label") or band.get("id")), f"{int(band.get('count') or 0):,}"] for band in bands]
    return {
        "version": "v2",
        "status": "answered",
        "answer": (
            f"Near {venue['venue_name']}, the most records are {str(top.get('label') or '').lower()} "
            f"({int(top.get('count') or 0):,} of {total:,})."
        ),
        "caveat": (
            "Distance bands inside the 800 m buffer, 2020–present. "
            "Locations are rounded, so a point inside 200 m is not a crime at the door."
        ),
        "confidence": {"kind": "recorded", "text": "Recorded count."},
        "narration": None,
        "table": {"columns": ["Distance", "Records"], "rows": rows},
        "links": _links(venue["venue_id"], venue["venue_name"]),
        "choices": [],
        "results": [{"venue_id": venue["venue_id"], "venue_name": venue["venue_name"]}],
        "tool": "distance",
        "arguments": {"venue_id": venue["venue_id"]},
        "engine": "v2",
    }


_REPLAY = {"weather_association", "current_weather", "metro_alerts", "page_guide", "distance"}
_GLUE = _FOLLOW_WORDS | {"instead", "rather", "switch", "to", "at", "near", "on", "in", "me", "show", "tell", "there"}


def _venue_swap(message: str, venues: list[dict]) -> dict | None:
    """A follow-up that only names a different venue, such as “What about the Coliseum?”."""
    normalized = _normalize(message)
    mentions = _mentions(normalized, venues)
    if len(mentions) != 1 or len(mentions[0][2]) != 1:
        return None
    if set(_question_text(normalized, mentions).split()) - _GLUE:
        return None
    return mentions[0][2][0]


def _remembered_tool(prior: str, tool: str | None) -> str | None:
    """Keep the last v2 tool. If it was not stored, read it from the earlier question."""
    if tool in _REPLAY:
        return tool
    if re.search(r"\b(advisories|advisory|service alerts?|metro alerts?)\b", prior):
        return "metro_alerts"
    if re.search(r"\b(right now|currently|current weather|weather now|raining now|is it raining|temperature now)\b", prior):
        return "current_weather"
    if re.search(r"\b(now|today|tonight)\b", prior) and re.search(r"\b(weather|rain|raining|temperature)\b", prior):
        return "current_weather"
    if re.search(r"\b(wet days?|dry days?|hot days?|cooler days?)\b", prior):
        return "weather_association"
    if re.search(r"\b(weather|rain|raining|precipitation|wet|hot)\b", prior) and re.search(
        r"\b(crime|crimes|records?|incidents?|correlation|gap)\b", prior
    ):
        return "weather_association"
    return None


def _guide_by_id(topic: str) -> dict | None:
    for topic_id, _pattern, title, text, section in _guide_entries():
        if topic_id == topic:
            return {"id": topic_id, "title": title, "text": text, "section": section}
    return None


def _replay(message: str, venues: list[dict], prior: str, tool: str | None, arguments: dict) -> dict | None:
    """Repeat the last v2 tool when the new message only changes the venue."""
    tool = _remembered_tool(prior, tool)
    if tool not in _REPLAY:
        return None
    venue = _venue_swap(message, venues)
    if venue is None:
        return None
    if tool == "weather_association":
        facet = str(arguments.get("facet") or "")
        seed = prior if re.search(r"\b(wet|dry|hot|cooler|rain)\b", prior) else ""
        if facet in {"hot", "both"} and not re.search(r"\b(hot|cooler)\b", seed):
            seed = f"{seed} hot days".strip()
        if facet in {"wet", "both", ""} and not re.search(r"\b(wet|dry|rain)\b", seed):
            seed = f"{seed} wet days".strip()
        return _finish_weather(_historical_weather(seed or "wet days", venue), venue)
    if tool == "current_weather":
        return _finish_weather(_current_weather(venue), venue)
    if tool == "metro_alerts":
        return _finish_live(metro_reply(venue), venue)
    if tool == "distance":
        return _distance_reply(venue)
    guide = _guide_by_id(str(arguments.get("topic") or "")) or _matching_guide(prior)
    if guide is None:
        return None
    return _guide_reply(guide, message, venue["venue_id"], venues)


def answer_v2(message: str, venue_id: str | None = None, history: list[dict] | None = None, *, narrate: bool = True) -> dict:
    """One v2 turn. History contributes the earlier question, tool, and venue."""
    message = str(message or "").lower()
    summary = load_summary()
    venues = summary["venues"]
    prior = ""
    carried = None
    carried_tool = None
    carried_args: dict = {}
    roster = {venue["venue_id"] for venue in venues}
    for turn in history or []:
        text = str(turn.get("user_text") or "").strip()
        if text:
            prior = text.lower()
        if turn.get("tool"):
            carried_tool = turn["tool"]
        argued = turn.get("arguments") or {}
        if isinstance(argued, dict) and argued:
            carried_args = argued
            if argued.get("venue_id") in roster:
                carried = argued["venue_id"]
    if not venue_id and carried:
        venue_id = carried
    if re.search(r"\b(advisories|advisory|service alerts?|metro alerts?)\b", message):
        venue, early = _one_venue(message, venue_id, venues)
        if early:
            return _stamp(early)
        return _finish_live(metro_reply(venue), venue)
    guide = _definition_request(message)
    if guide:
        return _guide_reply(guide, message, venue_id, venues)
    if _weather_prediction(message):
        return _blank(
            "unsupported",
            "I cannot say whether the weather will change crime. That would be a forecast.",
            "The weekday-adjusted gap is a past association. A live reading is not applied to it.",
            venue_id,
            None,
        )
    mode = _weather_mode(message)
    if mode == "both":
        refused = _split_weather()
        refused["links"] = _links(venue_id, None)
        return refused
    if mode in {"current", "historical"}:
        venue, early = _one_venue(message, venue_id, venues)
        if early:
            return _stamp(early)
        payload = _current_weather(venue) if mode == "current" else _historical_weather(message, venue)
        return _finish_weather(payload, venue)
    replayed = _replay(message, venues, prior, carried_tool, carried_args)
    if replayed:
        return replayed
    prepared = _prepare(message)
    body = answer_deterministic(prepared, venue_id, _prepare(prior) if prior else None)
    if body.get("status") == "unsupported" and _where_highest(prepared):
        venue, early = _one_venue(message, venue_id, venues)
        if early:
            return _stamp(early)
        located = _distance_reply(venue)
        if located:
            return located
    if body.get("status") == "unsupported":
        body = _route_miss(prepared, venue_id, venues, body)
    if body.get("status") == "unsupported":
        missed = _guide_after_miss(prepared)
        if missed:
            return _guide_reply(missed, message, venue_id, venues)
        template, caveat = _split_caveat(body.get("answer") or "")
        note = "Open the matching section on the venue page. The figures there are the same templates."
        body["answer"] = f"{template}\n\n{note}\n\n{caveat}".strip()
    return _envelope(body, venues, message, narrate=narrate)
