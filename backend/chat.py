"""Small data-backed analyst, optionally interpreted by Claude.

Only full-period totals, the most common category, and two-venue total
comparisons are supported. Unknown filters are rejected rather than ignored.
"""

from __future__ import annotations

import re
import unicodedata

from backend.claude import ClaudeUnavailable, interpret_question, settings
from backend.store import DatasetNotFound, load_summary

PERIOD = "2020–2024"
RADIUS_M = 800
SOURCE_FILE = "Crime_Data_from_2020_to_2024.csv"
SOURCE = {
    "name": "LAPD crime reports via the LA Open Data Portal",
    "file": SOURCE_FILE,
    "period": PERIOD,
    "radius_m": RADIUS_M,
}
PROVENANCE = (
    f"Source: {SOURCE['name']} ({SOURCE_FILE}). "
    f"Period: {PERIOD}. Analysis: {RADIUS_M} m radius around each venue."
)
HELP = (
    "I can answer full-period incident totals, the most common crime category, "
    "and crime-count comparisons between two named venues. "
    "This demo cannot answer other categories or filters, crime causes, live "
    "conditions, safety assessments, or 2028 predictions."
)


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).casefold()
    value = re.sub(r"\bwhat['’]s\b", "what is", value)
    return " ".join(re.findall(r"[a-z0-9]+", value))


def _reply(status: str, answer: str, *, results=None, choices=None, intent=None) -> dict:
    return {
        "status": status,
        "answer": f"{answer}\n\n{PROVENANCE}",
        "source": dict(SOURCE),
        "question_type": intent,
        "results": results or [],
        "choices": choices or [],
    }


def _aliases(venue: dict) -> set[str]:
    """Build name variants from the real roster, with no hardcoded venue IDs."""
    name = venue["venue_name"]
    parts = [name, re.sub(r"\([^)]*\)", "", name)]
    parts.extend(re.findall(r"\(([^)]*)\)", name))
    aliases = {_normalize(venue["venue_id"])}
    uninformative = {
        "la", "los", "angeles", "of", "the", "com", "halls", "area",
        "recreation", "country",
    }
    for part in parts:
        normalized = _normalize(part)
        if not normalized:
            continue
        aliases.add(normalized)
        aliases.add(re.sub(r"\blos angeles\b", "la", normalized))
        aliases.add(re.sub(r"\bla\b", "los angeles", normalized))
        aliases.add(re.sub(r"^(?:la|los angeles) ", "", normalized))
        # Short names such as "Dodger", "Coliseum", and "Venice". Shared
        # tokens such as "stadium" deliberately yield a clarification.
        aliases.update(
            word for word in normalized.split()
            if len(word) >= 3 and word not in uninformative
        )
    return aliases - {""}


def _mentions(message: str, venues: list[dict]) -> list[tuple[int, int, list[dict]]]:
    matches: dict[tuple[int, int], dict[str, dict]] = {}
    for venue in venues:
        for alias in _aliases(venue):
            for match in re.finditer(rf"(?<!\w){re.escape(alias)}(?!\w)", message):
                matches.setdefault(match.span(), {})[venue["venue_id"]] = venue
    # A full name consumes its shorter aliases. Two separate mentions of
    # "Venice Beach" and "Venice Beach Boardwalk" still resolve separately.
    accepted = []
    for (start, end), candidates in sorted(
        matches.items(), key=lambda item: (-(item[0][1] - item[0][0]), item[0][0])
    ):
        if any(start < right and end > left for left, right, _ in accepted):
            continue
        accepted.append((start, end, list(candidates.values())))
    return sorted(accepted, key=lambda item: item[0])


def _question_text(message: str, mentions: list) -> str:
    residual = message
    for start, end, _ in reversed(mentions):
        residual = residual[:start] + " venue " + residual[end:]
    residual = re.sub(r"\b2020 (?:(?:to|through) )?2024\b", "", residual)
    residual = re.sub(r"\b800 ?(?:m|meters|metres)\b", "", residual)
    return residual


def _outside_scope(message: str, mentions: list) -> bool:
    """Code rejects explicit unsupported qualifiers even if Claude drops them."""
    residual = _question_text(message, mentions)
    forbidden = (
        r"\d|\b(why|cause|causes|caused|reason|reasons|predict|prediction|predictions|"
        r"forecast|forecasts|will|expect|expected|probability|chance|projection|projections|"
        r"live|current|currently|today|now|tomorrow|yesterday|tonight|"
        r"recent|future|next|last|safe|safer|safest|safety|risk|dangerous|danger|"
        r"rate|rates|density|densities|percent|percentage|average|median|trend|trends|"
        r"night|nights|daytime|hour|hourly|daily|monthly|annual|yearly|year|years|"
        r"month|months|week|weeks|since|before|after|citywide|unique|across|"
        r"victim|victims|suspect|suspects|age|gender|arrest|arrests|conviction|"
        r"violent|property|robbery|robberies|burglary|burglaries|theft|thefts|"
        r"assault|assaults|battery|batteries|homicide|homicides|vandalism|"
        r"shooting|shootings|weapon|weapons|sexual|rape|rapes|shoplifting|stolen|fraud|arson|"
        r"transit|traffic|rail|bus|hospital|hospitals|fire|police|"
        r"invent|ignore|pretend|fabricate)\b"
    )
    return bool(
        re.search(forbidden, residual)
        or re.search(r"\b(all|every) (venues|venue|sites|site)\b", residual)
        or re.search(r"\b(how many|number of|count of) (?:crime )?(venues|sites|category|categories|type|types)\b", residual)
        or (
            re.search(r"\b(compare|comparison|versus|vs|difference)\b", residual)
            and re.search(r"\b(category|categories|type|types|most common)\b", residual)
        )
    )


def _intent(message: str, mentions: list) -> str | None:
    # Local fallback rejects unknown words rather than ignoring qualifiers.
    residual = _question_text(message, mentions)
    if re.search(r"\b(all|every) (venues|venue|sites|site)\b", residual):
        return None
    if re.search(r"\b(how many|number of|count of) (venues|sites)\b", residual):
        return None
    allowed = set(
        "a an the what which how many much is are was were has have had do does "
        "did can could you please tell me show give about at near nearby around "
        "within of for by from in on to and between with versus vs v than or "
        "this that these those it here two both venue venues site sites stadium "
        "crime crimes incident incidents report reports reported recorded "
        "count counts total totals number numbers all category categories type "
        "types most common frequent frequently often occurs occurred occurrence "
        "occurrences top leading highest compare comparison difference more "
        "fewer higher lower largest lapd data full period radius over during "
        "been there".split()
    )
    if set(residual.split()) - allowed:
        return None
    category_query = re.search(r"\b(category|categories|type|types)\b", residual)
    if re.search(r"\b(compare|comparison|versus|vs|difference|more|fewer|higher|lower)\b", residual):
        if category_query or re.search(r"\b(most|top|leading|common|frequent|frequently)\b", residual):
            return None
        return "compare"
    if re.search(r"\b(most|top|leading|highest|largest)\b", residual) and re.search(
        r"\b(category|categories|type|types|common|frequent|frequently|often)\b", residual
    ):
        return "top_category"
    if re.search(r"\b(count|counts|total|totals|number|how many)\b", residual) and re.search(
        r"\b(crime|crimes|incident|incidents|report|reports)\b", residual
    ):
        if category_query and not re.search(r"\ball categories\b", residual):
            return None
        return "total"
    return None


def suggested_questions() -> dict:
    venues = load_summary()["venues"]
    questions = []
    if venues:
        first = venues[0]["venue_name"]
        questions.extend([
            f"What is the most common crime category near {first}?",
            f"How many incidents were reported near {first}?",
        ])
    if len(venues) >= 2:
        questions.append(f"Compare the crime counts near {first} and {venues[1]['venue_name']}.")
    return {"questions": questions}


def answer_question(message: str, venue_id: str | None = None) -> dict:
    engine = {"engine": "data", "model": None, "engine_note": None}
    response = _answer_question(message, venue_id, engine)
    response.update(engine)
    return response


def _answer_question(message: str, venue_id: str | None, engine: dict) -> dict:
    try:
        summary = load_summary()
    except DatasetNotFound:
        return _reply("unavailable", "The processed crime data is missing. Run `python -m pipeline.run` first.")

    venues = summary["venues"]
    if (
        summary["meta"]["buffer_radius_m"] != RADIUS_M
        or summary["meta"]["sources"]["crime"] != SOURCE_FILE
        or any(venue["buffer_radius_m"] != RADIUS_M for venue in venues)
    ):
        return _reply(
            "unavailable",
            "The processed data does not match this demo's required source and 800 m radius. "
            "Rebuild it with `python -m pipeline.run --radius-m 800` before asking for counts.",
        )

    normalized = _normalize(message)
    mentions = _mentions(normalized, venues)
    intent = _intent(normalized, mentions)
    if _outside_scope(normalized, mentions):
        intent = None
    elif settings()["claude_configured"]:
        try:
            interpretation = interpret_question(message, venues, venue_id)
            engine.update(engine="claude", model=settings()["model"])
            if not interpretation.scope_supported or interpretation.intent == "unsupported":
                intent = None
            elif intent is None or intent == interpretation.intent:
                intent = interpretation.intent
            else:
                # A known local intent must not be changed into another metric.
                engine.update(engine="fallback", model=None, engine_note="Claude's interpretation did not match the supported calculation; using the data parser.")
        except ClaudeUnavailable:
            engine.update(engine="fallback", engine_note="Claude is unavailable; using the data parser.")
    if intent is None:
        return _reply("unsupported", f"I cannot answer that question from the supported processed-data calculations. {HELP}")

    for start, end, candidates in mentions:
        if len(candidates) > 1:
            choices = [
                {
                    "venue_id": venue["venue_id"],
                    "venue_name": venue["venue_name"],
                    "message": normalized[:start] + _normalize(venue["venue_name"]) + normalized[end:],
                }
                for venue in candidates
            ]
            names = ", ".join(venue["venue_name"] for venue in candidates)
            return _reply(
                "clarification", f"Which venue do you mean by “{normalized[start:end]}”? {names}.",
                choices=choices, intent=intent,
            )

    resolved = {items[0]["venue_id"]: items[0] for _, _, items in mentions}
    # Map/chat context is used only when the question names no venue. An
    # ambiguous explicit name always requires clarification, even with context.
    if not resolved and venue_id:
        context = next((venue for venue in venues if venue["venue_id"] == venue_id), None)
        if context is None:
            return _reply("clarification", "That selected venue is not in the processed roster. Please name a venue.", intent=intent)
        resolved[venue_id] = context

    if intent == "compare" and len(resolved) != 2:
        return _reply("clarification", "Please name exactly two venues to compare their full-period crime counts.", intent=intent)
    if not resolved:
        choices = [
            {
                "venue_id": venue["venue_id"],
                "venue_name": venue["venue_name"],
                "message": f"{message} near {venue['venue_name']}",
            }
            for venue in venues
        ]
        return _reply("clarification", "Which venue do you mean? Select a venue below or include its full name.", choices=choices, intent=intent)
    if intent != "compare" and len(resolved) != 1:
        return _reply("clarification", "Please name one venue for that question, or ask to compare two venue counts.", intent=intent)

    selected = list(resolved.values())
    if any(sum(venue["crime_by_category"].values()) != venue["crime_count_nearby"] for venue in selected):
        return _reply(
            "unavailable",
            "The processed category counts do not match the incident total for this venue. "
            "I cannot calculate a reliable answer until the data is rebuilt.",
            intent=intent,
        )
    if intent == "top_category":
        venue = selected[0]
        categories = venue["crime_by_category"]
        if not categories:
            return _reply("answered", f"{venue['venue_name']} has no reported incidents, so there is no most common category.", results=[_result(venue, "All categories", 0)], intent=intent)
        highest = max(categories.values())
        winners = sorted(category for category, count in categories.items() if count == highest)
        results = [_result(venue, category, highest) for category in winners]
        labels = "; ".join(f"{category}: {highest:,} reports" for category in winners)
        answer = f"The most common crime category near {venue['venue_name']} is {labels}."
        if len(winners) > 1:
            answer = f"The most common categories near {venue['venue_name']} are tied: {labels}."
        return _reply("answered", answer, results=results, intent=intent)

    results = [_result(venue, "All categories", venue["crime_count_nearby"]) for venue in selected]
    answer = "\n".join(
        f"{row['venue_name']} — {row['category']}: {row['count']:,} reported incidents."
        for row in results
    )
    if intent == "compare":
        first, second = results
        difference = abs(first["count"] - second["count"])
        if difference:
            larger = max(results, key=lambda row: row["count"])
            answer += f"\n{larger['venue_name']} has {difference:,} more reports (absolute difference)."
        else:
            answer += "\nThe counts are equal (absolute difference: 0 reports)."
        answer += (
            "\nVenue areas can overlap, so the same incident may appear under both venues. "
            "These counts are not added into a unique citywide total."
        )
    return _reply("answered", answer, results=results, intent=intent)


def _result(venue: dict, category: str, count: int) -> dict:
    return {
        "venue_id": venue["venue_id"],
        "venue_name": venue["venue_name"],
        "category": category,
        "count": count,
    }
