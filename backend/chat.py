"""Small data-backed analyst, optionally interpreted by Claude.

Crime calculations and supporting venue context come from processed data.
Unknown filters are rejected rather than ignored.
"""

from __future__ import annotations

from datetime import date, timedelta
import math
import re
import unicodedata

from backend.claude import ClaudeUnavailable, explain_figures, explanation_uses_only, interpret_question, settings
from backend.context import CONTEXT_INTENTS, ContextUnavailable, context_answer, source_text
from backend.datasets import _load_home_games, load_city_baseline, load_permit_day_rows
from backend.store import DatasetNotFound, get_venue, home_game_comparison, load_summary, permit_comparison
from pipeline.crime_groups import GROUP_LABELS, crime_group

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
HEADLINE_INTENTS = {"present_total", "density", "city", "busiest_month"}
PATTERN_INTENTS = {"top_groups", "weekend", "weekend_groups", "rose"}
_WEEKEND_DAYS = {"Saturday", "Sunday"}
_WEEKDAY_DAYS = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday"}
SLICE_INTENTS = {"year_count", "group_count", "nibrs_total"}
_COUNT_YEARS = range(2020, 2027)
_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_GROUP_WORDS = {
    "robbery": "robbery", "robberies": "robbery",
    "burglary": "burglary", "burglaries": "burglary",
    "theft": "theft", "thefts": "theft", "shoplifting": "theft",
    "assault": "assault", "assaults": "assault", "battery": "assault", "batteries": "assault",
    "homicide": "homicide", "homicides": "homicide", "murder": "homicide", "murders": "homicide",
    "vandalism": "vandalism",
    "rape": "sexual", "rapes": "sexual", "sexual": "sexual",
    "weapon": "weapons", "weapons": "weapons",
    "vehicle": "vehicle", "vehicles": "vehicle",
}
_GROUP_ALIASES = {"battery", "batteries", "rape", "rapes", "shoplifting", "murder", "murders"}
PRESENT_SOURCE = {
    "name": "LAPD crime reports via the LA Open Data Portal, then LAPD NIBRS offenses",
    "file": "crime_merged.json",
    "period": "2020–present",
    "radius_m": RADIUS_M,
}
PRESENT_PROVENANCE = (
    "Source: LAPD crime reports via the LA Open Data Portal, then LAPD NIBRS offenses "
    "(crime_merged.json). Period: 2020–present. Analysis: 800 m radius around the venue. "
    "Reports dated before March 7, 2024, then one row per NIBRS offense. "
    "This is the venue page headline, not the 2020–2024 report total."
)
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
HELP = (
    "I can answer a venue's 2020–present record count, a single year from 2020 through 2026, "
    "the 2020–2024 report total when that period is named, NIBRS offense totals, and offense-group "
    "counts such as robbery. I can also answer the most common LAPD category, comparisons between "
    "two named venues, density, the city comparison, the busiest month, the top offense groups, "
    "the weekend pattern, offense groups on weekend or weekday days, and which groups rose most from 2020 to present. "
    "I can also quote the page's permit-day comparison and, for Dodger Stadium, the home-game comparison. "
    "The citywide total is every usable LAPD record in Los Angeles, not the 14 venue circles added together. "
    "I can also show nearby rail/bus transit, nearest recorded fire/police stations "
    "and hospitals (with the recorded emergency-room flag), and listed venue sports. "
    "I cannot answer a count of permits, Ticketmaster listings, time of day, other distances, traffic, schedules, fares, "
    "nearest emergency-room hospitals, travel/response times, crime causes, live "
    "conditions, safety assessments, or 2028 predictions."
)


def _normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).casefold()
    value = re.sub(r"\b(what|where)['’]s\b", r"\1 is", value)
    return " ".join(re.findall(r"[a-z0-9]+", value))


def _reply(status: str, answer: str, *, results=None, choices=None, intent=None, sources=None, provenance_text=None, table=None, explanation=None) -> dict:
    if provenance_text:
        provenance = provenance_text
    elif sources:
        provenance = source_text(sources) + "\n\nCrime context only — " + PROVENANCE
    else:
        provenance = ("Crime context only — " if intent in CONTEXT_INTENTS else "") + PROVENANCE
    return {
        "status": status,
        "answer": f"{answer}\n\n{provenance}",
        "source": sources[0] if sources else dict(SOURCE),
        "sources": sources or [dict(SOURCE)],
        "question_type": intent,
        "results": results or [],
        "choices": choices or [],
        **({"table": table} if table else {}),
        **({"explanation": explanation} if explanation else {}),
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


def _reports_span(message: str) -> bool:
    return bool(re.search(r"\b2020 (?:(?:to|through) )?2024\b", message))


def _explicit_present(message: str) -> bool:
    return bool(
        re.search(r"\b2020(?: to| through)?(?: the)? present\b", message)
        or re.search(r"\bpresent (?:total|count|incidents|incident|records|record)\b", message)
    )


def _single_year(message: str) -> str | None:
    """One supported calendar year, ``bad`` when the year is outside the files, or None."""
    if _reports_span(message) or _explicit_present(message):
        return None
    years = re.findall(r"\b(20\d\d)\b", message)
    if not years:
        return None
    if len(years) != 1 or int(years[0]) not in _COUNT_YEARS:
        return "bad"
    return years[0]


def _top_limit(message: str) -> int | None:
    """1–10, ``-1`` when top-N is out of range, or None when the question has no top-N."""
    match = re.search(r"\btop (one|two|three|four|five|six|seven|eight|nine|ten|\d+)\b", message)
    if not match:
        return None
    token = match.group(1)
    limit = _NUMBER_WORDS[token] if token in _NUMBER_WORDS else int(token)
    if 1 <= limit <= 10:
        return limit
    return -1


def _group_id(message: str) -> str | None:
    found = {
        group_id for word, group_id in _GROUP_WORDS.items()
        if re.search(rf"\b{word}\b", message)
    }
    if not found:
        return None
    if len(found) > 1:
        return "conflict"
    return next(iter(found))


def _wants_nibrs(message: str) -> bool:
    return bool(re.search(r"\bnibrs\b", message))


def _wants_citywide(message: str) -> bool:
    if re.search(
        r"\b(compare|comparison|compared|versus|vs|against|above|below|higher|lower|relative|rate|rates|densit)\b",
        message,
    ):
        return False
    if re.search(r"\bcitywide\b", message):
        return True
    if re.search(r"\bunique\b", message) and re.search(r"\b(total|count|crime|crimes|incident|incidents)\b", message):
        return True
    return bool(re.search(r"\b(?:across|at|for|in) (?:all|every) (?:the )?(?:venues|venue|sites|site)\b", message))


def _strip_answered(residual: str, message: str) -> str:
    cleaned = residual
    year = _single_year(message)
    if year and year != "bad":
        cleaned = re.sub(rf"\b{year}\b", " ", cleaned)
        cleaned = re.sub(r"\byears?\b", " ", cleaned)
    if _top_limit(message) not in (None, -1):
        cleaned = re.sub(r"\btop (?:one|two|three|four|five|six|seven|eight|nine|ten|\d+)\b", " ", cleaned)
    group = _group_id(message)
    if group and group != "conflict":
        for word, group_id in _GROUP_WORDS.items():
            if group_id == group:
                cleaned = re.sub(rf"\b{word}\b", " ", cleaned)
    if _wants_nibrs(message):
        cleaned = re.sub(r"\bnibrs\b", " ", cleaned)
    if _wants_citywide(message):
        cleaned = re.sub(r"\bcitywide\b|\bunique\b|\bacross\b", " ", cleaned)
        cleaned = re.sub(r"\b(?:at|for|in|across) (?:all|every) (?:the )?(?:venues|venue|sites|site)\b", " ", cleaned)
        cleaned = re.sub(r"\b(?:all|every) (?:venues|venue|sites|site)\b", " ", cleaned)
    return " ".join(cleaned.split())


def _day_type_span(message: str) -> str | None:
    """Weekend or weekday offense groups. A plain day-of-week question stays on day counts."""
    if not re.search(r"\b(types?|categories|groups|kinds)\b|\bmost common\b", message):
        return None
    weekend = re.search(r"\bweekends?\b", message)
    weekday = re.search(r"\bweekdays?\b", message)
    if weekend and not weekday:
        return "weekend"
    if weekday and not weekend:
        return "weekday"
    return None


def _headline_intent(residual: str, mentions: list) -> str | None:
    """Venue-page headline questions. Two named venues stay on the count comparison."""
    if len(mentions) > 1:
        return None
    if re.search(r"\b(unique|across)\b", residual) or re.search(r"\b(all|every) (venues|venue|sites|site)\b", residual):
        return None
    limit = _top_limit(residual)
    if limit == -1:
        return None
    if limit or re.search(r"\b(?:most common|top) (?:crime )?(?:categories|groups|types)\b", residual):
        return "top_groups"
    if re.search(r"\b(weekend|weekends|weekday|weekdays|day of week)\b", residual):
        if _day_type_span(residual):
            return "weekend_groups"
        return "weekend"
    if re.search(r"\b(rose|risen|grew|grown|increased)\b", residual) and re.search(
        r"\b(most|crime|crimes|category|categories|group|groups|which)\b", residual
    ):
        return "rose"
    if re.search(r"\bbusiest month\b", residual):
        return "busiest_month"
    if re.search(r"\bcity\b|\bcitywide\b", residual) and re.search(
        r"\b(compare|comparison|compared|versus|vs|against|above|below|higher|lower|relative|rate)\b", residual
    ):
        return "city"
    if re.search(r"\b(densit(?:y|ies)|dense|denser)\b", residual):
        return "density"
    if re.search(r"\b2020(?: to| through)?(?: the)? present\b", residual) or re.search(
        r"\bpresent (?:total|count|incidents|incident|records|record)\b", residual
    ):
        return "present_total"
    return None


def _strip_headline_phrases(residual: str) -> str:
    phrases = (
        r"\b2020(?: to| through)?(?: the)? present\b",
        r"\bpresent (?:total|count|incidents|incident|records|record)\b",
        r"\bbusiest month\b",
        r"\bdensit(?:y|ies)\b",
        r"\b(?:dense|denser)\b",
        r"\bcompared with the city\b",
        r"\bcompared to the city\b",
        r"\bcompare with the city\b",
        r"\bcompare to the city\b",
        r"\bversus the city\b",
        r"\bvs the city\b",
        r"\bagainst the city\b",
        r"\bcitywide rate\b",
        r"\bcitywide\b",
        r"\bthe city\b",
        r"\btop 5\b",
        r"\btop five\b",
        r"\bday of week\b",
        r"\bweekdays?\b",
        r"\bweekends?\b",
    )
    cleaned = residual
    for phrase in phrases:
        cleaned = re.sub(phrase, " ", cleaned)
    return " ".join(cleaned.split())


def _outside_scope(message: str, mentions: list) -> bool:
    """Code rejects explicit unsupported qualifiers even if Claude drops them."""
    residual = _question_text(message, mentions)
    year = _single_year(message)
    group = _group_id(residual)
    headline = _headline_intent(residual, mentions)
    if year == "bad" or _top_limit(residual) == -1 or group == "conflict":
        return True
    # A year, group, or NIBRS wording has to be the question, not a leftover filter.
    if year and year != "bad" and (
        _wants_event_lift(message)
        or headline in {"busiest_month", "density", "city", "weekend", "weekend_groups", "top_groups", "present_total", "rose"}
    ):
        return True
    if group and group != "conflict" and headline:
        return True
    if _wants_nibrs(message) and headline:
        return True
    context = _context_intent(residual)
    answerable = bool(
        headline
        or (year and year != "bad")
        or _top_limit(residual) not in (None, -1)
        or (group and group != "conflict")
        or _wants_nibrs(message)
        or _wants_citywide(message)
        or _wants_event_lift(message)
        or _reports_span(message)
    )
    if answerable:
        if headline:
            residual = _strip_headline_phrases(residual)
        if _wants_event_lift(message):
            residual = _strip_event_phrases(residual)
        residual = _strip_answered(residual, message)
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
        r"traffic|schedule|schedules|timetable|timetables|departure|departures|arrival|arrivals|"
        r"frequency|frequencies|fare|fares|ticket|tickets|cost|costs|price|prices|"
        r"walking|driving|walk|drive|travel|response|minutes|hours|open|closed|available|"
        r"availability|capacity|beds|phone|telephone|hotline|jurisdiction|boundary|boundaries|"
        r"fastest|cheapest|directions|accessible|accessibility|wheelchair|trauma|pediatric|icu|"
        r"nibrs|ticketmaster|permit|permits|event|events|"
        r"invent|ignore|pretend|fabricate)\b"
    )
    return bool(
        re.search(forbidden, residual)
        or context == "unsupported"
        # Crime years cannot be silently applied to static supporting data.
        or (context and re.search(r"\b2020 (?:(?:to|through) )?2024\b", message))
        or (context in {"fire", "police", "hospital", "services", "sports"}
            and re.search(r"\b800 ?(?:m|meters|metres)\b", message))
        or re.search(r"\b(all|every) (venues|venue|sites|site)\b", residual)
        or re.search(r"\b(how many|number of|count of) (?:crime )?(venues|sites|category|categories|type|types)\b", residual)
        or (
            re.search(r"\b(compare|comparison|versus|vs|difference)\b", residual)
            and re.search(r"\b(category|categories|type|types|most common)\b", residual)
        )
    )


def _context_intent(residual: str) -> str | None:
    """Find layer requests and reject metrics the summary cannot supply."""
    topics = set()
    for topic, pattern in {
        "rail": r"\b(rail|train|trains|subway)\b",
        "bus": r"\b(bus|buses)\b",
        "transit": r"\b(transit|transport|transportation)\b",
        "fire": r"\bfire\b", "police": r"\b(police|sheriff)\b",
        "hospital": r"\b(hospital|hospitals|er)\b|\bemergency room\b",
        "sports": r"\b(sport|sports|sporting)\b",
        "services": r"\bemergency (services|facilities)\b",
    }.items():
        if re.search(pattern, residual):
            topics.add(topic)
    if "bus" not in topics and re.search(r"\bmetro\b", residual):
        topics.add("rail")
    if not topics:
        return None
    if re.search(r"\b(crime|crimes|incident|incidents|offence|offense|compare|comparison|versus|vs|difference|more|fewer|higher|lower|best|better|most|top|highest|largest|common|frequent|busiest)\b", residual):
        return "unsupported"
    if topics <= {"rail", "bus", "transit"}:
        if re.search(r"\b(nearest|closest|directions|route to|routes to|route from|routes from)\b", residual):
            return "unsupported"
        if "bus" in topics and re.search(r"\b(names|addresses|locations|individual)\b|\b(list|which|where) (?:the )?bus stops\b", residual):
            return "unsupported"
        return "transit" if "transit" in topics or len(topics) > 1 else next(iter(topics))
    if topics <= {"fire", "police", "hospital", "services"}:
        # Only the nearest hospital overall is stored, not a nearest-ER search.
        if re.search(r"\b(nearest|closest) (?:\w+ )?(emergency room|er)\b", residual):
            return "unsupported"
        if re.search(r"\b(?:with|without) (?:an? )?(emergency room|er)\b|\bemergency hospital\b|\b(?:that|which) (?:has|provides|offers)\b", residual):
            return "unsupported"
        if re.search(r"\b(emergency room|er)\b", residual) and not re.search(r"\b(nearest|closest) hospital\b", residual):
            return "unsupported"
        if re.search(r"\b(how many|count|counts|list all|all hospitals|all stations)\b", residual):
            return "unsupported"
        if "services" in topics or len(topics) > 1:
            return "services"
        return next(iter(topics))
    return "sports" if topics == {"sports"} else "unsupported"


def _wants_event_lift(message: str) -> bool:
    """The page's past event-day comparison. A permit count or a forecast stays refused."""
    if re.search(r"\b(predict|prediction|forecast|will|2028|ticketmaster|upcoming)\b", message):
        return False
    if re.search(r"\b(how many|number of|count of)\b", message):
        return False
    if re.search(r"\b(home games?|game days?|permit days?|event days?)\b", message):
        return True
    if re.search(r"\blift\b", message) and re.search(r"\b(permit|event|game|home)\b", message):
        return True
    return bool(
        re.search(r"\bpermits?\b", message)
        and re.search(r"\b(compare|comparison|versus|vs|difference|against)\b", message)
    )


def _event_focus(message: str) -> set[str]:
    home = bool(re.search(r"\b(home games?|game days?)\b", message))
    permit = bool(re.search(r"\bpermits?\b|\bpermit days?\b", message))
    if home and not permit and not re.search(r"\bevent days?\b|\blift\b", message):
        return {"home"}
    if permit and not home and not re.search(r"\bevent days?\b|\blift\b", message):
        return {"permit"}
    return {"permit", "home"}


def _strip_event_phrases(residual: str) -> str:
    phrases = (
        r"\bpermit day lift\b",
        r"\bevent day lift\b",
        r"\bhome games?\b",
        r"\bgame days?\b",
        r"\bpermit days?\b",
        r"\bevent days?\b",
        r"\bpermits?\b",
        r"\blift\b",
    )
    cleaned = residual
    for phrase in phrases:
        cleaned = re.sub(phrase, " ", cleaned)
    return " ".join(cleaned.split())


def _intent(message: str, mentions: list) -> str | None:
    # Local fallback rejects unknown words rather than ignoring qualifiers.
    residual = _question_text(message, mentions)
    if _wants_citywide(message):
        return "citywide"
    if _wants_event_lift(message):
        return "event_lift"
    headline = _headline_intent(residual, mentions)
    if headline:
        return headline
    year = _single_year(message)
    group = _group_id(residual)
    if year == "bad" or _top_limit(residual) == -1 or group == "conflict":
        return None
    if _wants_nibrs(message):
        return "nibrs_total"
    if group:
        return "group_count"
    if year:
        return "year_count"
    if re.search(r"\b(all|every) (venues|venue|sites|site)\b", residual):
        return None
    if re.search(r"\b(how many|number of|count of) (venues|sites)\b", residual):
        return None
    context = _context_intent(residual)
    allowed = set(
        "a an the what which how many much is are was were has have had do does "
        "did can could you please tell me show give about at near nearby around "
        "within of for by from in on to and between with versus vs v than or "
        "this that these those it here two both venue venues site sites stadium "
        "crime crimes incident incidents report reports reported recorded "
        "count counts total totals number numbers all category categories type "
        "types most common frequent frequently often occurs occurred occurrence happened "
        "occurrences top leading highest compare comparison difference more "
        "fewer higher lower largest lapd data full period radius over during "
        "been there".split()
    )
    if context:
        allowed.update(
            "where list nearest closest station stations rail train trains "
            "metro subway bus buses stop stops route routes line lines transit transport "
            "transportation options access fire police sheriff hospital hospitals emergency "
            "room er services facilities service facility distance far away located "
            "sport sports sporting listed hosted host hosts take place played play held "
            "happening happen".split()
        )
    if set(residual.split()) - allowed:
        return None
    if context:
        return context if context != "unsupported" else None
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
        if _reports_span(message) or (
            re.search(r"\breports?\b", residual)
            and not re.search(r"\b(incident|incidents|crime|crimes)\b", residual)
        ):
            return "total"
        return "present_total"
    return None


def suggested_questions() -> dict:
    venues = load_summary()["venues"]
    questions = []
    if venues:
        first = venues[0]["venue_name"]
        questions.extend([
            f"What is the most common crime category near {first}?",
            f"How many incidents were reported near {first}?",
            f"What transit is nearby {first}?",
            f"What is the nearest hospital to {first}?",
            f"Which sports are listed at {first}?",
            f"What is the crime density near {first}?",
            f"What is the busiest month near {first}?",
            f"How does {first} compare to the city?",
            f"What are the top five crime categories near {first}?",
            f"Show the top 3 crime categories near {first}",
            f"How many robberies near {first}?",
            f"How many incidents occurred in 2020 near {first}?",
            f"How many NIBRS offenses near {first}?",
            f"What is the weekend pattern near {first}?",
            f"What types of crime are most common on weekends near {first}?",
            f"Which crimes rose most from 2020 to present near {first}?",
            f"How do permit days compare with other days near {first}?",
            f"How do home games compare near {first}?",
        ])
    if len(venues) >= 2:
        questions.insert(2, f"Compare the crime counts near {first} and {venues[1]['venue_name']}.")
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
        return _reply("unavailable", "The processed venue data is missing. Run `python -m pipeline.run` first.")

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
            accepted = interpretation.scope_supported and interpretation.intent != "unsupported"
            if intent is not None:
                # A parsed question keeps its calculation. Claude only fills in wording the word list missed.
                if not accepted or interpretation.intent != intent:
                    engine.update(engine="fallback", model=None, engine_note="Claude's interpretation did not match the supported calculation; using the data parser.")
            elif accepted:
                intent = interpretation.intent
            else:
                intent = None
        except ClaudeUnavailable:
            engine.update(engine="fallback", engine_note="Claude is unavailable; using the data parser.")
    if intent is None:
        return _reply("unsupported", f"I cannot answer that question from the supported processed-data calculations. {HELP}")
    if intent == "citywide":
        return _citywide_answer()

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
    if intent in SLICE_INTENTS:
        return _slice_answer(intent, selected[0], normalized)
    if intent in HEADLINE_INTENTS:
        return _headline_answer(intent, selected[0])
    if intent == "event_lift":
        return _event_lift_answer(selected[0], normalized)
    if intent in PATTERN_INTENTS:
        return _pattern_answer(intent, selected[0], normalized)
    if intent in CONTEXT_INTENTS:
        try:
            answer, results, sources = context_answer(intent, selected[0], summary["meta"])
        except ContextUnavailable as exc:
            return _reply("unavailable", f"{exc} I cannot calculate a reliable answer until the data is rebuilt.", intent=intent)
        return _reply("answered", answer, results=results, intent=intent, sources=sources)
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
        f"{row['venue_name']} — {row['category']}: {row['count']:,} reported incidents, 2020–2024."
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


def _group_label(detail: dict, group_id: str) -> str:
    labels = detail.get("crime_groups") or {}
    return str(labels.get(group_id) or group_id)


def _rose_years(months: dict) -> list[str]:
    found = {
        str(month)[:4]
        for month in (months or {})
        if len(str(month)) >= 4 and str(month)[:4].isdigit() and int(str(month)[:4]) in _COUNT_YEARS
    }
    return sorted(found)


def _group_totals(months: dict, year: str | None = None) -> dict[str, int]:
    totals: dict[str, int] = {}
    for month, groups in (months or {}).items():
        if year and not str(month).startswith(year):
            continue
        if not isinstance(groups, dict):
            continue
        for group_id, count in groups.items():
            totals[group_id] = totals.get(group_id, 0) + int(count or 0)
    return totals


def _comma(value: int) -> str:
    return f"{int(value):,}"


def _pattern_table(venue_id: str, section: str, label: str, columns: list[str], rows: list[list[str]]) -> dict:
    return {
        "columns": columns,
        "rows": rows,
        "href": f"#/venue/{venue_id}/{section}",
        "link_label": label,
    }


def _maybe_explain(venue_name: str, caption: str, table: dict) -> str | None:
    if not settings()["claude_configured"]:
        return None
    labels = [cell for row in table["rows"] for cell in row if not re.fullmatch(r"[\d,% ]+", cell)]
    labels.append(venue_name)
    source = " ".join([caption, *table["columns"], *(cell for row in table["rows"] for cell in row)])
    try:
        text = explain_figures(venue_name, caption, table["columns"], table["rows"])
    except ClaudeUnavailable:
        return None
    if not explanation_uses_only(text, source, labels):
        return None
    return text


def _pattern_answer(intent: str, venue: dict, message: str = "") -> dict:
    detail = get_venue(venue["venue_id"])
    months = (detail or {}).get("merged_by_month") or {}
    if not months:
        return _reply(
            "unavailable",
            "The 2020–present offense groups are missing. Rebuild them with `python -m pipeline.merge_crime`.",
            intent=intent,
            sources=[dict(PRESENT_SOURCE)],
            provenance_text=PRESENT_PROVENANCE,
        )
    name = venue["venue_name"]
    venue_id = venue["venue_id"]
    if intent == "top_groups":
        totals = _group_totals(months)
        limit = _top_limit(message)
        if limit is None or limit < 1:
            limit = 5
        ranked = sorted(totals.items(), key=lambda item: (-item[1], _group_label(detail, item[0])))[:limit]
        total = sum(totals.values())
        rows = []
        results = []
        for group_id, count in ranked:
            label = _group_label(detail, group_id)
            share = int(math.floor((count / total) * 100 + 0.5)) if total else 0
            rows.append([label, _comma(count), f"{share}%"])
            results.append(_result(venue, label, count))
        caption = (
            f"Top {len(ranked)} offense groups near {name}, 2020–present, ranked by record count. "
            "Reports before March 7, 2024, then NIBRS offenses."
        )
        table = _pattern_table(venue_id, "categories", "Open incident types on the venue page", ["Group", "Records", "Share"], rows)
    elif intent == "weekend":
        block = (detail or {}).get("merged_weekday") or {}
        days = [day for day in (block.get("days") or []) if day.get("label")]
        if not days:
            return _reply(
                "unavailable",
                "The 2020–present day-of-week counts are missing. Rebuild them with `python -m pipeline.merge_crime` and `python -m pipeline.nibrs_charts`.",
                intent=intent,
                sources=[dict(PRESENT_SOURCE)],
                provenance_text=PRESENT_PROVENANCE,
            )
        rows = [[day["label"], _comma(int(day.get("count") or 0))] for day in days]
        results = [_result(venue, day["label"], int(day.get("count") or 0)) for day in days]
        summary = block.get("summary") or ""
        caption = (
            f"{summary} Counts are 2020–present: reports before March 7, 2024, plus NIBRS offenses after that. "
            "The venue page shows those two series side by side."
        ).strip()
        table = _pattern_table(venue_id, "weekday", "Open day of week on the venue page", ["Day", "Records"], rows)
    elif intent == "weekend_groups":
        block = (detail or {}).get("merged_weekday") or {}
        days = [day for day in (block.get("days") or []) if day.get("label")]
        span = _day_type_span(message) or "weekend"
        wanted = _WEEKEND_DAYS if span == "weekend" else _WEEKDAY_DAYS
        totals: dict[str, int] = {}
        for day in days:
            if day.get("label") not in wanted:
                continue
            for group in day.get("groups") or []:
                group_id = str(group.get("id") or "")
                if not group_id:
                    continue
                totals[group_id] = totals.get(group_id, 0) + int(group.get("count") or 0)
        if not totals:
            return _reply(
                "unavailable",
                "The day-of-week offense groups are missing. Rebuild them with `python -m pipeline.merge_crime` and `python -m pipeline.nibrs_charts`.",
                intent=intent,
                sources=[dict(PRESENT_SOURCE)],
                provenance_text=PRESENT_PROVENANCE,
            )
        limit = _top_limit(message)
        if limit is None or limit < 1:
            limit = 5
        ranked = sorted(totals.items(), key=lambda item: (-item[1], _group_label(detail, item[0])))[:limit]
        total = sum(totals.values())
        rows = []
        results = []
        for group_id, count in ranked:
            label = _group_label(detail, group_id)
            share = int(math.floor((count / total) * 100 + 0.5)) if total else 0
            rows.append([label, _comma(count), f"{share}%"])
            results.append(_result(venue, label, count))
        when = "Saturday and Sunday" if span == "weekend" else "Monday through Friday"
        caption = (
            f"Offense groups on {when} near {name}, 2020–present, ranked by record count. "
            "Shares are of those days only. "
            "Reports before March 7, 2024, then NIBRS offenses."
        )
        table = _pattern_table(venue_id, "weekday", "Open day of week on the venue page", ["Group", "Records", "Share"], rows)
    else:
        years = _rose_years(months)
        start = years[0] if years else "2020"
        end = years[-1] if years else "2020"
        earlier = _group_totals(months, start)
        later = _group_totals(months, end)
        yearly = {year: _group_totals(months, year) for year in years}
        changes = []
        for group_id in set(earlier) | set(later):
            change = later.get(group_id, 0) - earlier.get(group_id, 0)
            if change > 0:
                changes.append((change, _group_label(detail, group_id), group_id))
        changes.sort(key=lambda item: (-item[0], item[1]))
        rising = changes[:5]
        rows = []
        for change, label, group_id in rising:
            rows.append([
                label,
                *[_comma(yearly[year].get(group_id, 0)) for year in years],
                f"+{_comma(change)}",
            ])
        results = [_result(venue, label, change) for change, label, _group_id in rising]
        baseline = load_city_baseline() or {}
        through = str((baseline.get("present") or {}).get("through") or (baseline.get("nibrs") or {}).get("end") or "")
        partial = f" {end} runs through {through}, not a full year." if through.startswith(end) else ""
        caption = (
            f"Offense groups near {name} that rose the most from {start} to {end}, ranked by how many more records. "
            "The span is 2020–present: LAPD reports before March 7, 2024, then NIBRS offenses. "
            "2024 mixes reports through March 6 with NIBRS offenses after that, so a 2024 count can come from that counting difference."
            f"{partial} "
            "A group can rise from the first year to the last and still sit below a peak in between. "
            "2020 had little or no event crowd."
        )
        if not rows:
            caption = f"No offense group near {name} had more records in {end} than in {start}."
        table = _pattern_table(
            venue_id,
            "months",
            "Open incidents by month on the venue page",
            ["Group", *years, "Change"],
            rows,
        )
    explanation = _maybe_explain(name, caption, table)
    return _reply(
        "answered",
        caption,
        results=results,
        intent=intent,
        sources=[dict(PRESENT_SOURCE)],
        provenance_text=PRESENT_PROVENANCE,
        table=table,
        explanation=explanation,
    )


def _weekend_percent(days: list[date]) -> int:
    if not days:
        return 0
    weekend = sum(1 for day in days if day.weekday() >= 5)
    return int(math.floor(weekend / len(days) * 100 + 0.5))


def _parse_day(value: str) -> date | None:
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _permit_weekend_sentence(rows: list[dict]) -> str:
    permit_days = []
    other_days = []
    for row in rows:
        parsed = _parse_day(row.get("date"))
        if parsed is None:
            continue
        if int(row.get("is_permit_event_day") or 0):
            permit_days.append(parsed)
        else:
            other_days.append(parsed)
    permit_share = _weekend_percent(permit_days)
    other_share = _weekend_percent(other_days)
    if permit_share > other_share:
        mix = f"Permit days fall more often on weekends ({permit_share}% of permit days, {other_share}% of other days)."
    else:
        mix = f"Permit days are {permit_share}% weekend, and other days are {other_share}% weekend."
    return f"{mix} This comparison does not hold day of week fixed."


def _home_weekend_sentence(payload: dict) -> str:
    meta = payload.get("meta") or {}
    game_days = [parsed for item in payload.get("historical_home_game_days") or [] if (parsed := _parse_day(item))]
    months = {int(month) for month in (meta.get("in_season_months") or list(range(3, 11)))}
    game_set = set(game_days)
    other_days = []
    cursor = date(2020, 1, 1)
    while cursor <= date(2024, 12, 31):
        if cursor.month in months and cursor not in game_set:
            other_days.append(cursor)
        cursor += timedelta(days=1)
    game_share = _weekend_percent(game_days)
    other_share = _weekend_percent(other_days)
    if game_share > other_share:
        mix = f"Home games fall more often on weekends ({game_share}% of game days, {other_share}% of other days in those months)."
    else:
        mix = f"Home games are {game_share}% weekend, and other days in those months are {other_share}% weekend."
    return f"{mix} This comparison does not hold day of week fixed."


def _gap_phrase(summary: dict) -> str:
    difference = float(summary["absolute_difference"])
    phrase = f"{difference:+.2f}/day"
    if summary.get("percent_shown") and summary.get("lift_pct") is not None:
        phrase += f" · {float(summary['lift_pct']):+.1f}%"
    return phrase


def _comparison_row(label: str, summary: dict) -> list[str]:
    return [
        label,
        _comma(int(summary["event_day_count"])),
        _comma(int(summary["other_day_count"])),
        f"{float(summary['event_day_mean']):.2f}",
        f"{float(summary['other_day_mean']):.2f}",
        _gap_phrase(summary),
    ]


def _event_lift_answer(venue: dict, message: str) -> dict:
    focus = _event_focus(message)
    name = venue["venue_name"]
    group = _group_id(message)
    if group == "conflict":
        group = None
    sentences = []
    rows = []
    results = []
    sources = []
    if "permit" in focus:
        view = permit_comparison(venue["venue_id"])
        summary = (view or {}).get("summary") or {}
        if view and view.get("available") and int(summary.get("event_day_count") or 0):
            rows.append(_comparison_row("Permit days", summary))
            results.append(_result(venue, "Permit days", int(summary["event_day_count"])))
            sentences.append(
                f"Permit days versus other days near {name}, 2020–2024: "
                f"{_gap_phrase(summary)}. "
                f"{_permit_weekend_sentence(load_permit_day_rows().get(venue['venue_id']) or [])}"
            )
            if group:
                match = next((item for item in (view.get("groups") or []) if item.get("group") == group), None)
                label = _group_label(get_venue(venue["venue_id"]) or {}, group)
                if match:
                    rows.append(_comparison_row(label, match))
                    sentences.append(f"{label} on permit days: {_gap_phrase(match)}.")
                else:
                    sentences.append(f"The page does not publish a {label} permit-day percentage for {name}.")
            if view.get("note"):
                sentences.append(str(view["note"]))
            sources.append({
                "name": "LADBS temporary special event permits and LAPD crime reports",
                "file": "permit_event_days.csv",
                "period": "2020–2024",
                "radius_m": RADIUS_M,
            })
        elif "home" not in focus:
            sentences.append(f"No permit-day comparison is published for {name}.")
    if "home" in focus:
        games = home_game_comparison(venue["venue_id"])
        summary = (games or {}).get("summary") or {}
        if games and games.get("available") and int(summary.get("event_day_count") or 0):
            rows.append(_comparison_row("Home games", summary))
            results.append(_result(venue, "Home games", int(summary["event_day_count"])))
            payload = _load_home_games() or {}
            sentences.append(
                f"Home games versus other days near {name}, 2020–2024: "
                f"{_gap_phrase(summary)}. "
                f"{_home_weekend_sentence(payload)} "
                "2020 games had little or no crowd."
            )
            if group:
                match = next((item for item in (games.get("groups") or []) if item.get("group") == group), None)
                label = _group_label(get_venue(venue["venue_id"]) or {}, group)
                if match:
                    rows.append(_comparison_row(label, match))
                    sentences.append(f"{label} on home-game days: {_gap_phrase(match)}.")
                else:
                    sentences.append(f"The page does not publish a {label} home-game percentage for {name}.")
            sources.append({
                "name": "MLB Dodgers regular-season home games and LAPD crime reports",
                "file": "dodger_event_risk.json",
                "period": "2020–2024",
                "radius_m": RADIUS_M,
            })
        elif focus == {"home"}:
            sentences.append("A home-game comparison is published for Dodger Stadium only.")
    if not sentences:
        sentences.append(f"No permit-day or home-game comparison is published for {name}.")
    sentences.append("This is a past association, not a forecast.")
    table = None
    if rows:
        table = {
            "columns": ["Comparison", "Event days", "Other days", "Event-day mean", "Other-day mean", "Difference"],
            "rows": rows,
        }
    notes = []
    names = []
    if any(item.get("file") == "permit_event_days.csv" for item in sources):
        names.append("LADBS temporary special event permits and LAPD crime reports")
        notes.append("Permit days are compared with other days in months that had at least one permit day.")
    if any(item.get("file") == "dodger_event_risk.json" for item in sources):
        names.append("MLB Dodgers regular-season home games and LAPD crime reports")
        notes.append("Dodger Stadium home games are compared with other days in March through October.")
    if names:
        provenance = (
            "Source: " + "; ".join(names) + ", 2020–2024, 800 m radius. "
            + " ".join(notes)
            + " This is an association, not a forecast."
        )
    else:
        provenance = (
            "Source: processed venue files, 2020–2024, 800 m radius. "
            "No permit-day or home-game comparison is published for this venue."
        )
    return _reply(
        "answered",
        " ".join(sentences),
        results=results,
        intent="event_lift",
        sources=sources or [dict(SOURCE)],
        provenance_text=provenance,
        table=table,
    )


def _year_note(year: str, through: str) -> str:
    if int(year) <= 2023:
        return f"{year} counts LAPD reports inside the 800 m circle."
    if year == "2024":
        return "2024 counts LAPD reports through March 6, then NIBRS offenses."
    if through.startswith(year):
        return f"{year} counts NIBRS offenses through {through}, not a full year."
    return f"{year} counts NIBRS offenses."


def _slice_answer(intent: str, venue: dict, message: str) -> dict:
    detail = get_venue(venue["venue_id"]) or {}
    name = venue["venue_name"]
    year = _single_year(message)
    if year == "bad":
        year = None
    group = _group_id(message)
    if group == "conflict":
        group = None
    label = _group_label(detail, group) if group else ""
    if group and any(re.search(rf"\b{word}\b", message) for word in _GROUP_ALIASES if _GROUP_WORDS[word] == group):
        asked = next(word for word in _GROUP_ALIASES if _GROUP_WORDS[word] == group and re.search(rf"\b{word}\b", message))
        label = f"{label} (includes {asked})"
    baseline = load_city_baseline() or {}
    through = str((baseline.get("present") or {}).get("through") or (baseline.get("nibrs") or {}).get("end") or "")

    if intent == "nibrs_total":
        nibrs = detail.get("nibrs") or {}
        if not nibrs:
            return _reply(
                "unavailable",
                "The NIBRS offense counts are missing. Rebuild them with `python -m pipeline.merge_crime`.",
                intent=intent,
                sources=[dict(PRESENT_SOURCE)],
                provenance_text=PRESENT_PROVENANCE,
            )
        if group and year:
            count = 0
            for month, categories in (nibrs.get("categories_by_month") or {}).items():
                if not str(month).startswith(year):
                    continue
                count += sum(int(value or 0) for category, value in categories.items() if crime_group(category) == group)
        elif group:
            count = sum(
                int(value or 0)
                for category, value in (nibrs.get("by_category") or {}).items()
                if crime_group(category) == group
            )
        elif year:
            count = sum(
                int(value or 0)
                for month, value in (nibrs.get("by_month") or {}).items()
                if str(month).startswith(year)
            )
        else:
            count = int(nibrs.get("count") or 0)
        end = str((baseline.get("nibrs") or {}).get("end") or through or "the latest extract")
        subject = label or "NIBRS offenses"
        answer = f"{name} — {subject}: {count:,} NIBRS offenses inside the 800 m circle, March 7, 2024 through {end}."
        if year:
            answer += f" {year} is only the offenses dated in that year."
        elif group:
            answer += " This is that offense group, not every NIBRS offense."
        category = label or "NIBRS offenses"
        provenance = (
            "Source: LAPD NIBRS offenses via the LA Open Data Portal (crime_merged.json). "
            f"Period: March 7, 2024 through {end}. Analysis: 800 m radius around the venue. "
            "One case can include more than one offense. This is not the 2020–2024 report total."
        )
        return _reply(
            "answered",
            answer,
            results=[_result(venue, category, count)],
            intent=intent,
            sources=[{
                "name": "LAPD NIBRS offenses via the LA Open Data Portal",
                "file": "crime_merged.json",
                "period": "Mar 2024–present",
                "radius_m": RADIUS_M,
            }],
            provenance_text=provenance,
        )

    months = detail.get("merged_by_month") or {}
    if not months:
        return _reply(
            "unavailable",
            "The 2020–present offense groups are missing. Rebuild them with `python -m pipeline.merge_crime`.",
            intent=intent,
            sources=[dict(PRESENT_SOURCE)],
            provenance_text=PRESENT_PROVENANCE,
        )
    totals = _group_totals(months, year if year else None)
    if group:
        count = int(totals.get(group, 0))
        subject = label
    else:
        count = sum(totals.values())
        subject = year or "2020–present"
    if year:
        answer = f"{name} — {subject}: {count:,} records in {year}. {_year_note(year, through)}"
        category = f"{label} {year}".strip() if group else year
    else:
        answer = (
            f"{name} — {subject}: {count:,} records, 2020–present. "
            "Reports before March 7, 2024, then NIBRS offenses."
        )
        category = label or "2020–present"
    return _reply(
        "answered",
        answer,
        results=[_result(venue, category, count)],
        intent=intent,
        sources=[dict(PRESENT_SOURCE)],
        provenance_text=PRESENT_PROVENANCE,
    )


def _citywide_answer() -> dict:
    baseline = load_city_baseline() or {}
    present = baseline.get("present") or {}
    if not present:
        return _reply(
            "unavailable",
            "The citywide total is missing. Rebuild it with `python -m pipeline.city_baseline`.",
            intent="citywide",
        )
    count = int(present["count"])
    legacy = int(present.get("legacy_count") or 0)
    nibrs = int(present.get("nibrs_offense_count") or 0)
    through = present.get("through") or "the latest extract"
    answer = (
        f"City of Los Angeles, 2020–present: {count:,} records. "
        f"That is {legacy:,} LAPD reports before March 7, 2024, then {nibrs:,} NIBRS offenses through {through}. "
        "This is every usable LAPD record in the city. It is not the 14 venue circles added together. "
        "Downtown circles overlap, so those venue counts are not a city total."
    )
    provenance = (
        "Source: LAPD crime reports via the LA Open Data Portal, then LAPD NIBRS offenses (city_baseline.json). "
        f"Period: 2020–present, through {through}. "
        "City land area: U.S. Census Bureau 2020, 469.49 square miles. "
        "This is not the 2020–2024 venue-report total and not an 800 m venue circle."
    )
    return _reply(
        "answered",
        answer,
        results=[{
            "venue_id": "",
            "venue_name": "City of Los Angeles",
            "category": "2020–present",
            "count": count,
        }],
        intent="citywide",
        sources=[{
            "name": "LAPD crime reports via the LA Open Data Portal, then LAPD NIBRS offenses",
            "file": "city_baseline.json",
            "period": "2020–present",
            "radius_m": None,
        }],
        provenance_text=provenance,
    )


def _ordinal(number: int) -> str:
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def _month_label(value: str) -> str:
    text = str(value or "")
    if len(text) >= 7 and text[4] == "-" and text[5:7].isdigit():
        month = int(text[5:7])
        if 1 <= month <= 12:
            return f"{_MONTHS[month - 1]} {text[:4]}"
    return text or "unknown"


def _headline_answer(intent: str, venue: dict) -> dict:
    detail = get_venue(venue["venue_id"])
    present = (detail or {}).get("present") or None
    if not present:
        return _reply(
            "unavailable",
            "The 2020–present venue figures are missing. Rebuild them with `python -m pipeline.merge_crime` and `python -m pipeline.city_baseline`.",
            intent=intent,
            sources=[dict(PRESENT_SOURCE)],
            provenance_text=PRESENT_PROVENANCE,
        )
    name = venue["venue_name"]
    count = int(present["count"])
    if intent == "present_total":
        answer = (
            f"{name} — 2020–present: {count:,} records inside the 800 m circle. "
            "This blends LAPD reports before March 7, 2024 with NIBRS offenses from that date on."
        )
        category = "2020–present"
    elif intent == "density":
        rate = float(present["crime_per_km2"])
        rank = present.get("density_rank") or {}
        place = ""
        if rank:
            tied = "tied for " if rank.get("tied") else ""
            place = f", {tied}{_ordinal(int(rank['rank']))} of {int(rank['of'])}"
        answer = f"Crime density near {name} is {rate:,.1f} per km², 2020–present{place}."
        category = "2020–present density"
    elif intent == "city":
        city = ((detail or {}).get("city_baseline") or {}).get("present")
        if not city:
            return _reply(
                "unavailable",
                "The city comparison is missing. Rebuild it with `python -m pipeline.city_baseline`.",
                intent=intent,
                sources=[dict(PRESENT_SOURCE)],
                provenance_text=PRESENT_PROVENANCE,
            )
        answer = f"{name} — {city['summary']} Period: {city['period']}."
        category = "2020–present vs city"
    else:
        label = _month_label(str(present.get("peak_month") or ""))
        peak = int(present.get("peak_count") or 0)
        if not present.get("peak_month"):
            answer = f"{name} has no dated records in the 2020–present blend, so there is no busiest month."
            count = 0
        else:
            answer = f"The busiest month near {name} is {label}, {peak:,} records, 2020–present."
            count = peak
        category = "2020–present busiest month"
    return _reply(
        "answered",
        answer,
        results=[_result(venue, category, count)],
        intent=intent,
        sources=[dict(PRESENT_SOURCE)],
        provenance_text=PRESENT_PROVENANCE,
    )


def _result(venue: dict, category: str, count: int) -> dict:
    return {
        "venue_id": venue["venue_id"],
        "venue_name": venue["venue_name"],
        "category": category,
        "count": count,
    }
