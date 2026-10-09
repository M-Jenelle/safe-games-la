"""Small data-backed analyst, optionally interpreted by Claude.

Crime calculations and supporting venue context come from processed data.
Unknown filters are rejected rather than ignored.
"""

from __future__ import annotations

from datetime import date, timedelta
import calendar
import contextvars
import csv
import math
import re
import unicodedata

from backend.claude import ClaudeUnavailable, Interpretation, ToolArguments, explain_figures, explanation_uses_only, interpret_question, settings
from backend.event_baseline import scale_month
from backend.context import CONTEXT_INTENTS, ContextUnavailable, context_answer, source_text
from backend.datasets import REPO_ROOT, _load_home_games, crime_time_for, load_city_baseline, load_merged, load_permit_day_rows
from backend.store import DatasetNotFound, get_crime_points, get_venue, home_game_comparison, load_summary, permit_comparison
from pipeline.crime_groups import GROUP_LABELS, crime_group, nibrs_group
from pipeline.geo import haversine_m

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
SLICE_INTENTS = {"year_count", "group_count", "nibrs_total", "since_count"}
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
    "I can compare named offense groups at two venues, rank venues by one offense group, by density, or by the change in records, "
    "and show the 2020–present part-of-day chart, including one part such as night or evening. "
    "The citywide total is every usable LAPD record in Los Angeles, not the 14 venue circles added together. "
    "I can also show nearby rail/bus transit, nearest recorded fire/police stations "
    "and hospitals (with the recorded emergency-room flag), and listed venue sports. "
    "I can count LAPD reports and NIBRS offenses whose pin falls within 200 m of a rail station recorded inside the venue circle. "
    "That is the neighborhood of the station pin, not a crime on a train and not a safety judgment. "
    "I can give a seasonal estimate for the next three months at one venue: the average of that month in earlier years. "
    "That figure is not recorded crime and not a certainty. "
    "I cannot answer a count of permits, Ticketmaster listings, tonight, hourly counts, other distances, traffic, schedules, fares, "
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
    names = [venue["venue_name"], *(venue.get("former_names") or [])]
    parts = []
    for name in names:
        parts.append(name)
        parts.append(re.sub(r"\([^)]*\)", "", name))
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
        if "stadium" in normalized:
            ballpark = normalized.replace("stadium", "ballpark")
            aliases.add(ballpark)
            aliases.update(
                word for word in ballpark.split()
                if len(word) >= 3 and word not in uninformative
            )
        # Short names such as "Dodger", "Coliseum", and "Venice". Shared
        # tokens such as "stadium" deliberately yield a clarification.
        aliases.update(
            word for word in normalized.split()
            if len(word) >= 3 and word not in uninformative
        )
    # "Dodgers' stadium" is the same place as "Dodger Stadium".
    pluralized = set()
    for alias in aliases:
        words = alias.split()
        if len(words) < 2 or words[0].endswith("s"):
            continue
        pluralized.add(" ".join([f"{words[0]}s", *words[1:]]))
    return (aliases | pluralized) - {""}


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


def _expand_relative_time(message: str) -> str:
    """Turn "last year" into the year before the latest year stored in the files."""
    if not re.search(r"\blast year\b", message):
        return message
    previous = max(_COUNT_YEARS) - 1
    if previous not in _COUNT_YEARS:
        return message
    return re.sub(r"\blast year\b", str(previous), message, count=1)


def _since_span(message: str) -> tuple[int, int] | None:
    """A start year through the latest stored year. Two explicit years stay a pair."""
    if _year_pair(message) or _hard_block(message):
        return None
    match = re.search(r"\bsince (20\d\d)\b", message)
    if not match:
        return None
    start = int(match.group(1))
    end = max(_COUNT_YEARS)
    if start not in _COUNT_YEARS or start > end:
        return None
    return start, end


def _named_day(message: str) -> str | None:
    found = []
    for word, day in {
        "monday": "monday", "mondays": "monday",
        "tuesday": "tuesday", "tuesdays": "tuesday",
        "wednesday": "wednesday", "wednesdays": "wednesday",
        "thursday": "thursday", "thursdays": "thursday",
        "friday": "friday", "fridays": "friday",
        "saturday": "saturday", "saturdays": "saturday",
        "sunday": "sunday", "sundays": "sunday",
    }.items():
        if re.search(rf"\b{word}\b", message) and day not in found:
            found.append(day)
    if len(found) == 1:
        return found[0]
    return "conflict" if found else None


def _single_year(message: str) -> str | None:
    """One supported calendar year, ``bad`` when the year is outside the files, or None."""
    if _reports_span(message) or _explicit_present(message) or _since_span(message):
        return None
    years = re.findall(r"\b(20\d\d)\b", message)
    if not years:
        return None
    if len(years) != 1 or int(years[0]) not in _COUNT_YEARS:
        return "bad"
    return years[0]


def _year_pair(message: str) -> tuple[int, int] | None:
    years = [int(year) for year in re.findall(r"\b(20\d\d)\b", message)]
    if len(years) != 2:
        return None
    start, end = years
    if start > end or start not in _COUNT_YEARS or end not in _COUNT_YEARS:
        return None
    return start, end


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


def _group_ids(message: str) -> list[str]:
    found = []
    for word in re.findall(r"\b[a-z]+\b", message):
        group_id = _GROUP_WORDS.get(word)
        if group_id and group_id not in found:
            found.append(group_id)
    return found


def _group_id(message: str) -> str | None:
    found = _group_ids(message)
    if not found:
        return None
    if len(found) > 1:
        return "conflict"
    return found[0]


_FOLLOW_WORDS = {
    "a", "an", "also", "and", "about", "for", "how", "now", "please",
    "question", "same", "the", "there", "too", "venue", "what",
}
_ROSE_RE = re.compile(
    r"\b(rose|risen|rising|rise|grew|grown|increased|increase|climbing|climbed|climb)\b"
    r"|\b(?:gone|went) up\b"
)
_CHANGE_RE = re.compile(
    _ROSE_RE.pattern
    + r"|\b(change|changed|trend)\b"
)


def _seasonal_question(message: str) -> bool:
    """Next-month seasonal average only. 2028, safety, and "will" stay refused."""
    if re.search(
        r"\b(2028|will|safe|safer|safest|safety|risk|why|cause|causes|caused|"
        r"shooting|shootings|today|tonight|density|rate|rates)\b",
        message,
    ):
        return False
    return bool(
        re.search(r"\b(next|upcoming)\b", message)
        and re.search(r"\bmonths?\b", message)
        and re.search(
            r"\b(estimate|estimates|estimated|expect|expected|expectation|predict|prediction|forecast)\b",
            message,
        )
    )


def _hard_block(message: str) -> bool:
    return bool(re.search(
        r"\b(why|cause|causes|caused|predict|prediction|forecast|will|2028|today|tonight|"
        r"ticketmaster|shooting|shootings|safe|safer|safest|safety|risk|"
        r"invent|ignore|pretend|fabricate)\b",
        message,
    )) or bool(re.search(r"\b500 ?m\b", message))


_PERIOD_WORDS = {
    "night": "night", "nights": "night",
    "morning": "morning", "mornings": "morning",
    "afternoon": "afternoon", "afternoons": "afternoon",
    "evening": "evening", "evenings": "evening",
}


def _period_id(message: str) -> str | None:
    found = []
    for word, period in _PERIOD_WORDS.items():
        if re.search(rf"\b{word}\b", message) and period not in found:
            found.append(period)
    if not found:
        return None
    if len(found) > 1:
        return "conflict"
    return found[0]


def _period_question(message: str) -> bool:
    """One part-of-day slice from the 2020–2024 clock file. Tonight stays refused."""
    if _hard_block(message) or _period_id(message) in (None, "conflict"):
        return False
    return _single_year(message) != "bad"


def _mentions_time_of_day(message: str) -> bool:
    return bool(re.search(r"\b(?:times?|part) of day\b", message))


def _wants_time_of_day(message: str) -> bool:
    """The published four-part chart. A named part of day is answered on its own."""
    if not _mentions_time_of_day(message) or _hard_block(message) or _period_id(message):
        return False
    if _group_id(message) or _single_year(message) or _reports_span(message) or _explicit_present(message):
        return False
    return not re.search(r"\b(rate|rates|hour|hourly|density|densities)\b", message)


def _group_compare_shape(message: str) -> bool:
    if _hard_block(message) or _year_pair(message) or _single_year(message) == "bad":
        return False
    if len(_group_ids(message)) < 2 or re.search(r"\bdensit", message):
        return False
    if re.search(r"\b(most common|categor(?:y|ies))\b", message):
        return False
    return bool(re.search(r"\b(compare|compared|comparing|comparison|versus|vs|against|difference)\b", message))


def _density_compare_shape(message: str) -> bool:
    if _hard_block(message) or _group_id(message) or not re.search(r"\bdensit", message):
        return False
    return bool(re.search(r"\b(compare|compared|comparing|comparison|versus|vs|against)\b", message))


def _weekday_group_all(message: str) -> bool:
    if _group_id(message) in (None, "conflict"):
        return False
    return bool(re.search(r"\b(all days|each day|every day|day of week)\b", message))


def _area_change_shape(message: str) -> bool:
    if _hard_block(message) or _group_id(message) or _year_pair(message):
        return False
    return bool(
        re.search(r"\b(changed|change|changes)\b", message)
        and re.search(r"\b(areas|venues|sites)\b", message)
    )


def _is_venue_fragment(message: str, mentions: list) -> bool:
    """Glue words plus one venue, such as "and Dodger Stadium?"."""
    if len(mentions) != 1 or len(mentions[0][2]) != 1:
        return False
    return not (set(_question_text(message, mentions).split()) - _FOLLOW_WORDS)


def _rewrite_follow_up(message: str, prior: str, venues: list) -> str | None:
    """Swap the venue in the previous question. A new question is left as written."""
    if not prior:
        return None
    mentions = _mentions(message, venues)
    if len(mentions) != 1 or len(mentions[0][2]) != 1:
        return None
    if set(_question_text(message, mentions).split()) - _FOLLOW_WORDS:
        return None
    prior_mentions = [item for item in _mentions(prior, venues) if len(item[2]) == 1]
    if len(prior_mentions) != 1:
        return None
    start, end, _previous = prior_mentions[0]
    rewritten = " ".join(f"{prior[:start]}{_normalize(mentions[0][2][0]['venue_name'])}{prior[end:]}".split())
    return rewritten


def _rewrite_year_follow_up(message: str, prior: str, venues: list) -> str | None:
    """Keep the one remembered question's venue when the follow-up only changes the year."""
    if not prior or _mentions(message, venues):
        return None
    year = _single_year(message)
    if not year or year == "bad":
        return None
    if set(message.split()) - _FOLLOW_WORDS - {year, "in", "during"}:
        return None
    prior_mentions = [item for item in _mentions(prior, venues) if len(item[2]) == 1]
    if len(prior_mentions) != 1:
        return None
    if _single_year(prior) and _single_year(prior) != "bad":
        rewritten = re.sub(r"\b20\d\d\b", year, prior, count=1)
    else:
        rewritten = f"{prior} in {year}"
    rewritten = " ".join(rewritten.split())
    if rewritten == prior:
        return None
    return rewritten


def _split_clauses(message: str) -> list[str] | None:
    parts = [part.strip() for part in re.split(r"\band (?=(?:what|how|which)\b)", message) if part.strip()]
    if len(parts) != 2:
        return None
    return parts


def _with_venue(clause: str, donor: str, venues: list) -> str:
    if _mentioned_ids(_mentions(clause, venues)):
        return clause
    donor_ids = _mentioned_ids(_mentions(donor, venues))
    if len(donor_ids) != 1:
        return clause
    venue = _roster_venue(venues, donor_ids[0])
    if venue is None:
        return clause
    return f"{clause} near {_normalize(venue['venue_name'])}"


_PARTIAL_NOTICE = "I answered the first part; ask the second separately."


def _visible_answer(answer: str) -> tuple[str, str]:
    for marker in ("\n\nSource:", "\n\nCrime context only", "\n\nNo answer was calculated"):
        at = answer.find(marker)
        if at != -1:
            return answer[:at].strip(), answer[at:]
    return answer.strip(), ""


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
    if limit or re.search(r"\b(?:most common|top) (?:crime )?(?:categories|groups|types)\b", residual) or re.search(r"\btop crimes?\b", residual):
        return "top_groups"
    if re.search(r"\b(?:biggest|largest) crime problem\b", residual) or re.search(r"\bmost common crimes?\b(?!\s+categor)", residual):
        return "top_groups"
    if re.search(r"\b(weekend|weekends|weekday|weekdays|day of(?: the)? week|busiest day)\b", residual):
        if _weekday_group_all(residual):
            return None
        if _day_type_span(residual):
            return "weekend_groups"
        return "weekend"
    if _ROSE_RE.search(residual) and re.search(
        r"\b(most|crime|crimes|category|categories|group|groups|which)\b", residual
    ):
        if _year_pair(residual) or _since_span(residual):
            return None
        return "rose"
    if re.search(r"\bbusiest month\b", residual):
        return "busiest_month"
    if re.search(r"\bcity\b|\bcitywide\b", residual) and re.search(
        r"\b(compare|comparison|compared|versus|vs|against|above|below|higher|lower|relative|rate|average)\b", residual
    ):
        return "city"
    if re.search(r"\b(densit(?:y|ies)|dense|denser)\b", residual):
        if re.search(r"\brank\b", residual) and re.search(r"\bvenues\b", residual):
            return None
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
        r"\bcompare to the city average\b",
        r"\bcompare to the city\b",
        r"\bcompared to the city average\b",
        r"\bversus the city\b",
        r"\bvs the city\b",
        r"\bagainst the city\b",
        r"\bcitywide rate\b",
        r"\bcitywide\b",
        r"\bthe city\b",
        r"\bcity average\b",
        r"\btop 5\b",
        r"\btop five\b",
        r"\bday of the week\b",
        r"\bday of week\b",
        r"\bbusiest day\b",
        r"\bweekdays?\b",
        r"\bweekends?\b",
    )
    cleaned = residual
    for phrase in phrases:
        cleaned = re.sub(phrase, " ", cleaned)
    return " ".join(cleaned.split())


def _tool_opening(message: str) -> bool:
    """A tool shape the word list does not parse. Forecasts and other hard blocks stay closed."""
    if _hard_block(message):
        return False
    if re.search(r"\b(how many|number of|count of)\b", message) and re.search(r"\bpermits?\b", message):
        return False
    if re.search(r"\bdensit", message) and _group_id(message):
        return False
    if _year_pair(message) and _CHANGE_RE.search(message):
        return _group_id(message) != "conflict"
    if re.search(r"\brank\b", message) and re.search(r"\bvenues\b", message):
        return True
    day = _named_day(message)
    if day == "conflict" or (day and _period_id(message)):
        return False
    if day and _group_id(message) != "conflict" and (
        _group_id(message) or re.search(r"\b(crime|crimes|incident|incidents|report|reports|group|groups|type|types|categor)\b", message)
    ):
        return True
    if _since_span(message) and _CHANGE_RE.search(message):
        return True
    return bool(
        _density_compare_shape(message)
        or _group_compare_shape(message)
        or _weekday_group_all(message)
        or _area_change_shape(message)
    )


def _tool_from_question(message: str) -> Interpretation | None:
    """Arguments Python can read when the question already has a supported tool shape."""
    pair = _year_pair(message)
    if pair and _CHANGE_RE.search(message):
        if _group_id(message) == "conflict":
            return None
        return Interpretation(
            intent="tool", scope_supported=True, tool="trend",
            arguments=ToolArguments(from_year=pair[0], to_year=pair[1], group=_group_id(message)),
        )
    if _area_change_shape(message):
        return Interpretation(intent="tool", scope_supported=True, tool="rank_venues", arguments=ToolArguments(metric="change"))
    if re.search(r"\brank\b", message) and re.search(r"\bvenues\b", message):
        metric = "present_count" if re.search(r"\b(count|counts|records|incidents)\b", message) and not re.search(r"\bdensit", message) else "density"
        return Interpretation(intent="tool", scope_supported=True, tool="rank_venues", arguments=ToolArguments(metric=metric))
    if _density_compare_shape(message):
        return Interpretation(intent="tool", scope_supported=True, tool="compare", arguments=ToolArguments(metric="density"))
    if _group_compare_shape(message):
        year = _single_year(message)
        return Interpretation(
            intent="tool", scope_supported=True, tool="compare",
            arguments=ToolArguments(groups=_group_ids(message), year=int(year) if year else None),
        )
    if _weekday_group_all(message):
        return Interpretation(
            intent="tool", scope_supported=True, tool="weekday_pattern",
            arguments=ToolArguments(days="all", group=_group_id(message)),
        )
    since = _since_span(message)
    if since and _CHANGE_RE.search(message):
        if _group_id(message) == "conflict":
            return None
        return Interpretation(
            intent="tool", scope_supported=True, tool="trend",
            arguments=ToolArguments(from_year=since[0], to_year=since[1], group=_group_id(message)),
        )
    day = _named_day(message)
    day_topic = _group_id(message) or re.search(r"\b(crime|crimes|incident|incidents|report|reports|group|groups|type|types|categor)\b", message)
    if day and day != "conflict" and not _period_id(message) and _group_id(message) != "conflict" and day_topic:
        return Interpretation(
            intent="tool", scope_supported=True, tool="weekday_pattern",
            arguments=ToolArguments(days=day, group=_group_id(message)),
        )
    return None


_STATION_RADIUS_M = 200.0
_rail_coords: dict[str, tuple[float, float]] | None = None
_nibrs_pins: tuple[list[float], list[float], list[int]] | None = None


def _rail_coordinates() -> dict[str, tuple[float, float]]:
    """Station pins from the Metro file. The venue summary stores distance, not coordinates."""
    global _rail_coords
    if _rail_coords is None:
        coords: dict[str, tuple[float, float]] = {}
        path = REPO_ROOT / "data" / "la_metro_rail_stations.csv"
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                coords[row["station_id"]] = (float(row["latitude"]), float(row["longitude"]))
        _rail_coords = coords
    return _rail_coords


def _nibrs_station_pins() -> tuple[list[float], list[float], list[int]]:
    """City NIBRS pins from March 7, 2024 on. Each stored point is already rounded to 4 decimals."""
    global _nibrs_pins
    if _nibrs_pins is None:
        heat = (load_merged() or {}).get("nibrs_heat") or {}
        latitudes: list[float] = []
        longitudes: list[float] = []
        weights: list[int] = []
        for point in heat.get("points") or []:
            latitudes.append(float(point["latitude"]))
            longitudes.append(float(point["longitude"]))
            weights.append(int(point["weight"]))
        _nibrs_pins = (latitudes, longitudes, weights)
    return _nibrs_pins


def _pins_within(origin_lat: float, origin_lon: float, latitudes: list[float], longitudes: list[float], weights: list[int] | None, radius_m: float) -> int:
    if not latitudes:
        return 0
    distances = haversine_m(origin_lat, origin_lon, latitudes, longitudes)
    if weights is None:
        return int((distances <= radius_m).sum())
    return int(sum(weight for distance, weight in zip(distances, weights) if distance <= radius_m))


def _station_crime_question(message: str) -> bool:
    """Crime near a recorded rail station, or the walk to one. Not a general transit question."""
    transit = re.search(r"\b(metro|rail|train|trains|subway)\b", message)
    place = re.search(r"\b(station|stations|line|lines|walk|walking)\b", message)
    crime = re.search(r"\b(crime|crimes|incident|incidents|record|records|offense|offenses|offence|offences)\b", message)
    walk = re.search(r"\b(walk|walking)\b", message) and re.search(r"\b(metro|rail|train|trains|subway|station)\b", message)
    return bool((transit and place and crime) or walk)


def _victim_age_question(message: str) -> bool:
    return bool(
        re.search(r"\b(age|ages|old)\b", message)
        and re.search(r"\b(victim|victims|pickpocket|pickpockets|crime|crimes|theft|thefts)\b", message)
    )


def _victim_age_answer() -> dict:
    return _reply(
        "answered",
        "Victim age is not in these files. Each processed record keeps the offense, the date, and a rounded location. "
        "Age and the rest of the victim record were left out, so I cannot group crimes by age.",
        intent="victim_age",
    )


def _station_crime_answer(venue: dict, message: str) -> dict:
    """Reports whose stored pin is within 200 m of a rail station inside the venue circle."""
    field = venue.get("rail_stations_nearby") or {}
    stations = [station for station in (field.get("stations") or []) if station.get("station_name")]
    name = venue["venue_name"]
    radius = float(venue["buffer_radius_m"])
    if not stations:
        return _reply(
            "answered",
            f"No rail station is recorded within {radius:g} m of {name}. "
            "The crime pins used here are the ones inside that circle, the same points behind the venue map. "
            "I cannot count records at a station farther away, along a track, or on a train. "
            "The files have station coordinates, not the track, and no field that says a crime happened on transit.",
            intent="station_crime",
            results=[_result(venue, "Near a rail station", 0)],
        )
    named = None
    for station in stations:
        if _normalize(station["station_name"]) in message:
            named = station
            break
    station = named or min(stations, key=lambda item: float(item["distance_m"]))
    pin = _rail_coordinates().get(str(station.get("station_id")))
    if pin is None:
        return _reply(
            "unavailable",
            f"{station['station_name']} is recorded near {name}, but its coordinates are missing, so I cannot count nearby reports.",
            intent="station_crime",
        )
    points = (get_crime_points(venue["venue_id"]) or {}).get("points") or []
    kept = [
        point for point in points
        if str(point.get("date") or "")[:10] and str(point["date"])[:10] < "2024-03-07"
    ]
    report_count = _pins_within(
        pin[0], pin[1],
        [float(point["latitude"]) for point in kept],
        [float(point["longitude"]) for point in kept],
        None,
        _STATION_RADIUS_M,
    )
    nibrs_lat, nibrs_lon, nibrs_weight = _nibrs_station_pins()
    nibrs_count = _pins_within(pin[0], pin[1], nibrs_lat, nibrs_lon, nibrs_weight, _STATION_RADIUS_M)
    lines = station.get("lines") or "not recorded"
    judgment = ""
    if re.search(r"\b(safe|safer|safest|safety|walk|walking)\b", message):
        judgment = " This is not a safety assessment, and it is not a count of crimes on the sidewalk between the venue and the station."
    return _reply(
        "answered",
        f"Near {name}, pins within {_STATION_RADIUS_M:g} m of {station['station_name']}: "
        f"{report_count:,} LAPD reports through March 6, 2024, and {nibrs_count:,} NIBRS offenses from March 7, 2024. "
        f"Lines: {lines}. The station is {float(station['distance_m']):,.1f} m from the venue pin, inside the {radius:g} m circle. "
        "Locations are rounded to about 11 m, so this is the neighborhood of the station pin, not a crime on a train. "
        "The files have no track geometry and no premise field for transit. "
        f"One NIBRS case can count more than once.{judgment}",
        results=[_result(venue, station["station_name"], report_count + nibrs_count)],
        intent="station_crime",
    )


def _other_contents_question(message: str) -> bool:
    """What landed in the leftover Other group. Permit 'other days' is not this."""
    if re.search(r"\bother days?\b", message):
        return False
    return bool(
        re.search(r"\bother\b", message)
        and re.search(
            r"\b(entail|entails|include|includes|included|mean|means|inside|contain|contains|consist|consists|bucket|group|groups|category|categories)\b",
            message,
        )
    )


def _top_labels(counts: dict[str, int], limit: int = 8) -> list[tuple[str, int]]:
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return ranked[:limit]


def _other_contents_answer(venue: dict) -> dict:
    """List the report labels and NIBRS descriptions that the Other bucket kept."""
    from collections import Counter

    reports: Counter[str] = Counter()
    for point in (get_crime_points(venue["venue_id"]) or {}).get("points") or []:
        occurred = str(point.get("date") or "")[:10]
        if not occurred or occurred >= "2024-03-07":
            continue
        label = str(point.get("category") or "").strip() or "Unknown"
        if crime_group(label) == "other":
            reports[label] += 1
    nibrs: Counter[str] = Counter()
    detail = get_venue(venue["venue_id"]) or {}
    for label, count in ((detail.get("nibrs") or {}).get("by_category") or {}).items():
        if crime_group(str(label)) == "other":
            nibrs[str(label)] += int(count)
    name = venue["venue_name"]
    report_total = sum(reports.values())
    nibrs_total = sum(nibrs.values())
    if not report_total and not nibrs_total:
        return _reply(
            "answered",
            f"No records near {name} fell into Other.",
            intent="other_contents",
            sources=[dict(PRESENT_SOURCE)],
            provenance_text=PRESENT_PROVENANCE,
        )
    lines = [
        f"Other near {name} is not one crime. It is every record the nine group rules did not claim: "
        f"{report_total:,} LAPD reports through March 6, 2024, and {nibrs_total:,} NIBRS offenses from March 7, 2024."
    ]
    rows = []
    if reports:
        shown = _top_labels(reports)
        lines.append("Reports through March 6, 2024:")
        lines.extend(f"{label} — {count:,}." for label, count in shown)
        hidden = len(reports) - len(shown)
        if hidden:
            lines.append(f"{hidden} more report labels are in this bucket.")
        rows.extend([label, _comma(count), "Reports through Mar 6"] for label, count in shown)
    if nibrs:
        shown = _top_labels(nibrs)
        lines.append("NIBRS offenses from March 7, 2024:")
        lines.extend(f"{label} — {count:,}." for label, count in shown)
        hidden = len(nibrs) - len(shown)
        if hidden:
            lines.append(f"{hidden} more NIBRS descriptions are in this bucket.")
        rows.extend([label, _comma(count), "NIBRS from Mar 7"] for label, count in shown)
    table = _pattern_table(
        venue["venue_id"], "categories", "Open incident types on the venue page",
        ["Description", "Records", "Series"], rows,
    )
    return _reply(
        "answered",
        " ".join(lines[:1]) + "\n" + "\n".join(lines[1:]),
        results=[_result(venue, "Other", report_total + nibrs_total)],
        intent="other_contents",
        sources=[dict(PRESENT_SOURCE)],
        provenance_text=PRESENT_PROVENANCE,
        table=table,
    )


def _nearest_rail_answer(venue: dict, meta: dict) -> dict:
    """Closest rail station recorded inside the 800 m buffer."""
    field = venue.get("rail_stations_nearby")
    source_file = (meta.get("sources") or {}).get("rail_stations")
    if not isinstance(field, dict) or not isinstance(source_file, str) or not source_file:
        return _reply(
            "unavailable",
            "The rail station list is missing. I cannot calculate a reliable answer until the data is rebuilt.",
            intent="rail",
        )
    radius = venue["buffer_radius_m"]
    stations = field.get("stations") or []
    name = venue["venue_name"]
    if not stations:
        answer = f"No rail station is recorded within {radius:g} m of {name}."
        nearest = None
    else:
        nearest = min(stations, key=lambda station: float(station["distance_m"]))
        lines = nearest.get("lines") or "not recorded"
        answer = (
            f"Nearest recorded rail station within {radius:g} m of {name}: {nearest['station_name']}. "
            f"Lines: {lines}. Straight-line distance: {float(nearest['distance_m']):,.1f} m. "
            "This is the closest station in the 800 m list, not a search of the whole rail system."
        )
    sources = [{
        "name": "LA Metro GTFS rail stations",
        "file": source_file,
        "period": None,
        "radius_m": radius,
        "scope": f"Within an {radius:g} m radius around the venue.",
        "coverage_date": None,
        "processed_at": meta.get("generated_at"),
    }]
    return _reply(
        "answered",
        answer,
        results=[{
            "venue_id": venue["venue_id"],
            "venue_name": name,
            "topic": "rail",
            "count": 1 if nearest else 0,
            "stations": [nearest] if nearest else [],
        }],
        intent="rail",
        sources=sources,
    )


def _outside_scope(message: str, mentions: list) -> bool:
    """Code rejects explicit unsupported qualifiers even if Claude drops them."""
    if _seasonal_question(message) or _other_contents_question(message) or _station_crime_question(message) or _victim_age_question(message):
        return False
    residual = _question_text(message, mentions)
    year = _single_year(message)
    group = _group_id(residual)
    headline = _headline_intent(residual, mentions)
    if _top_limit(residual) == -1:
        return True
    if group == "conflict" and not _group_compare_shape(message):
        return True
    if _mentions_time_of_day(message) and not _wants_time_of_day(message) and not _period_question(message):
        return True
    if _period_id(message) == "conflict" or _named_day(message) == "conflict":
        return True
    if _named_day(message) and _period_id(message):
        return True
    if year == "bad" and not _tool_opening(message):
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
    if _tool_opening(message):
        return False
    if _since_span(message) and (
        _CHANGE_RE.search(message)
        or _group_id(message) not in (None, "conflict")
        or re.search(r"\b(how many|number of|count of|counts?|totals?)\b", message)
    ):
        return False
    if _period_question(message) and not re.search(r"\b(rate|rates|densit)\b", message):
        return False
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
        if re.search(r"\b(directions|route to|routes to|route from|routes from)\b", residual):
            return "unsupported"
        if re.search(r"\b(nearest|closest)\b", residual) and "rail" not in topics and "transit" not in topics:
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


def _blocked_by_qualifier(message: str, intent: str | None) -> bool:
    """A day or part of day the chosen calculation would ignore."""
    day = _named_day(message)
    period = _period_id(message)
    if day == "conflict" or period == "conflict":
        return intent is not None
    if day and period:
        return True
    if day and intent is not None:
        return True
    if period and intent not in (None, "period", "period_groups", "time_of_day"):
        return True
    return False


def _intent(message: str, mentions: list) -> str | None:
    chosen = _choose_intent(message, mentions)
    if _blocked_by_qualifier(message, chosen):
        return None
    return chosen


def _choose_intent(message: str, mentions: list) -> str | None:
    # Local fallback rejects unknown words rather than ignoring qualifiers.
    if _seasonal_question(message):
        return "seasonal_estimate"
    residual = _question_text(message, mentions)
    if _wants_citywide(message):
        return "citywide"
    if re.search(r"\bwhich venues?\b", message) and re.search(r"\b(most|highest|largest)\b", message):
        group = _group_id(message)
        if group == "conflict":
            return None
        if group:
            return "rank_group"
        if re.search(r"\b(incidents?|records?|reports?|counts?)\b", message):
            return "rank_count"
        return "rank_group_missing"
    if _wants_event_lift(message):
        return "event_lift"
    if _other_contents_question(message):
        return "other_contents"
    if _victim_age_question(message):
        return "victim_age"
    if _station_crime_question(message):
        return "station_crime"
    headline = _headline_intent(residual, mentions)
    if headline:
        return headline
    if _wants_time_of_day(message):
        return "time_of_day"
    if _period_id(message) not in (None, "conflict") and re.search(r"\b(worse|worst|better|best|compared|comparison)\b", message):
        return "time_of_day"
    if _period_question(message) and re.search(
        r"\b(?:which|what) (?:crimes|kinds|types)\b"
        r"|\b(?:kinds|types|categories|groups) of (?:crime|crimes)\b"
        r"|\bcrimes (?:happen|happened|occur|occurred|show)\b"
        r"|\btypes of crime\b|\bmost common\b",
        message,
    ):
        return "period_groups"
    if _period_question(message):
        return "period"
    if _since_span(message):
        if _CHANGE_RE.search(message):
            return None
        if re.search(r"\b(how many|number of|count of|counts?|totals?)\b", message):
            return "since_count"
    if _weekday_group_all(message):
        return None
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


_TOOL_GROUPS = set(_GROUP_WORDS.values()) | {"other"}
_DAY_LABELS = {
    "monday": "Monday", "tuesday": "Tuesday", "wednesday": "Wednesday", "thursday": "Thursday",
    "friday": "Friday", "saturday": "Saturday", "sunday": "Sunday",
}


def _mentioned_ids(mentions: list) -> list[str]:
    return [candidates[0]["venue_id"] for _, _, candidates in mentions if len(candidates) == 1]


def _roster_venue(venues: list, venue_id: str | None) -> dict | None:
    if not venue_id:
        return None
    return next((venue for venue in venues if venue["venue_id"] == venue_id), None)


def _tool_venue(args, venues: list, selected_id: str | None, mentions: list) -> dict | None:
    mentioned = _mentioned_ids(mentions)
    if len(mentioned) > 1:
        return None
    target = args.venue_id
    if mentioned:
        if target and target != mentioned[0]:
            return None
        target = mentioned[0]
    elif selected_id:
        if target and target != selected_id:
            return None
        target = target or selected_id
    return _roster_venue(venues, target)


def _tool_refused() -> dict:
    return _reply("unsupported", "I can't answer that from these tables. Ask about one venue, a comparison, or a number on the page.")


def _present_figures(venue: dict) -> tuple[int, float] | None:
    present = (get_venue(venue["venue_id"]) or {}).get("present")
    if not present or "count" not in present or "crime_per_km2" not in present:
        return None
    return int(present["count"]), float(present["crime_per_km2"])


def _tool_top_groups(venue: dict, args) -> dict | None:
    if args.period not in (None, "present"):
        return None
    if args.n is not None and not 1 <= args.n <= 10:
        return None
    if args.group is not None:
        return None
    detail = get_venue(venue["venue_id"]) or {}
    months = detail.get("merged_by_month") or {}
    if not months:
        return _reply(
            "unavailable",
            "The 2020–present offense groups are missing. Rebuild them with `python -m pipeline.merge_crime`.",
            intent="top_groups",
            sources=[dict(PRESENT_SOURCE)],
            provenance_text=PRESENT_PROVENANCE,
        )
    limit = args.n or 5
    totals = _group_totals(months)
    ranked = sorted(totals.items(), key=lambda item: (-item[1], _group_label(detail, item[0])))[:limit]
    total = sum(totals.values())
    rows, results = [], []
    for group_id, count in ranked:
        label = _group_label(detail, group_id)
        share = int(math.floor((count / total) * 100 + 0.5)) if total else 0
        rows.append([label, _comma(count), f"{share}%"])
        results.append(_result(venue, label, count))
    caption = (
        f"Top {len(ranked)} offense groups near {venue['venue_name']}, 2020–present, ranked by record count."
    )
    table = _pattern_table(venue["venue_id"], "categories", "Open incident types on the venue page", ["Group", "Records", "Share"], rows)
    return _reply(
        "answered", caption, results=results, intent="top_groups",
        sources=[dict(PRESENT_SOURCE)], provenance_text=PRESENT_PROVENANCE, table=table,
    )


def _bounded_trend(tool_call, message: str):
    """Keep a named year span. The model cannot swap in a different pair."""
    if tool_call.tool != "trend":
        return tool_call
    pair = _year_pair(message)
    args = tool_call.arguments or ToolArguments()
    if pair and (args.from_year, args.to_year) not in {pair, (None, None)}:
        return None
    if pair and args.from_year is None:
        args = args.model_copy(update={"from_year": pair[0], "to_year": pair[1]})
        return tool_call.model_copy(update={"arguments": args})
    return tool_call


def _tool_trend(venue: dict, args) -> dict | None:
    if args.from_year is None or args.to_year is None:
        return None
    if not (2020 <= args.from_year <= args.to_year <= 2026):
        return None
    if args.group is not None and args.group not in _TOOL_GROUPS:
        return None
    detail = get_venue(venue["venue_id"]) or {}
    months = detail.get("merged_by_month") or {}
    if not months:
        return _reply(
            "unavailable",
            "The 2020–present offense groups are missing. Rebuild them with `python -m pipeline.merge_crime`.",
            intent="trend",
            sources=[dict(PRESENT_SOURCE)],
            provenance_text=PRESENT_PROVENANCE,
        )
    if _spans_switch(args.from_year, args.to_year):
        return _switch_group_answer(venue, detail, args.from_year, args.to_year, "trend", args.group)
    years = [str(year) for year in range(args.from_year, args.to_year + 1)]
    yearly = {year: _group_totals(months, year) for year in years}
    start, end = years[0], years[-1]
    earlier, later = yearly[start], yearly[end]
    group_ids = [args.group] if args.group else sorted(set(earlier) | set(later))
    changes = []
    for group_id in group_ids:
        change = later.get(group_id, 0) - earlier.get(group_id, 0)
        if args.group or change > 0:
            changes.append((change, _group_label(detail, group_id), group_id))
    changes.sort(key=lambda item: (-item[0], item[1]))
    rising = changes if args.group else changes[:5]
    rows = []
    for change, label, group_id in rising:
        sign = "+" if change > 0 else ""
        rows.append([label, *[_comma(yearly[year].get(group_id, 0)) for year in years], f"{sign}{_comma(change)}"])
    results = [_result(venue, label, change) for change, label, _group_id in rising]
    baseline = load_city_baseline() or {}
    through = str((baseline.get("present") or {}).get("through") or "")
    partial = f" {end} runs through {through}, not a full year." if through.startswith(end) else ""
    name = venue["venue_name"]
    if rows:
        caption = (
            f"Offense groups near {name} from {start} to {end}, ranked by the change in records.{partial}"
        )
    else:
        caption = f"No offense group near {name} had more records in {end} than in {start}."
    table = _pattern_table(
        venue["venue_id"], "months", "Open incidents by month on the venue page",
        ["Group", *years, "Change"], rows,
    )
    return _reply(
        "answered", caption, results=results, intent="trend",
        sources=[dict(PRESENT_SOURCE)], provenance_text=PRESENT_PROVENANCE, table=table,
    )


def _tool_weekday(venue: dict, args) -> dict | None:
    if args.days is None:
        return None
    if args.group is not None and args.group not in _TOOL_GROUPS:
        return None
    detail = get_venue(venue["venue_id"]) or {}
    block = detail.get("merged_weekday") or {}
    days = [day for day in (block.get("days") or []) if day.get("label")]
    if not days:
        return _reply(
            "unavailable",
            "The 2020–present day-of-week counts are missing. Rebuild them with `python -m pipeline.merge_crime` and `python -m pipeline.nibrs_charts`.",
            intent="weekday_pattern",
            sources=[dict(PRESENT_SOURCE)],
            provenance_text=PRESENT_PROVENANCE,
        )
    if args.days == "weekend":
        wanted = _WEEKEND_DAYS
        when = "Saturday and Sunday"
    elif args.days == "weekday":
        wanted = _WEEKDAY_DAYS
        when = "Monday through Friday"
    elif args.days == "all":
        wanted = {day["label"] for day in days}
        when = "each day of the week"
    else:
        wanted = {_DAY_LABELS[args.days]}
        when = _DAY_LABELS[args.days]
    selected_days = [day for day in days if day.get("label") in wanted]
    name = venue["venue_name"]
    if args.days == "all" and args.group is None:
        rows = [[day["label"], _comma(int(day.get("count") or 0))] for day in selected_days]
        results = [_result(venue, day["label"], int(day.get("count") or 0)) for day in selected_days]
        caption = f"Day counts near {name}, 2020–present."
        columns = ["Day", "Records"]
    elif args.group:
        label = _group_label(detail, args.group)
        total = 0
        rows = []
        for day in selected_days:
            count = sum(int(group.get("count") or 0) for group in (day.get("groups") or []) if group.get("id") == args.group)
            total += count
            rows.append([day["label"], _comma(count)])
        results = [_result(venue, label, total)]
        caption = f"{label} on {when} near {name}, 2020–present: {total:,} records."
        columns = ["Day", "Records"]
    else:
        totals: dict[str, int] = {}
        for day in selected_days:
            for group in day.get("groups") or []:
                group_id = str(group.get("id") or "")
                if group_id:
                    totals[group_id] = totals.get(group_id, 0) + int(group.get("count") or 0)
        if not totals:
            return _reply(
                "unavailable",
                "The day-of-week offense groups are missing. Rebuild them with `python -m pipeline.merge_crime` and `python -m pipeline.nibrs_charts`.",
                intent="weekday_pattern",
                sources=[dict(PRESENT_SOURCE)],
                provenance_text=PRESENT_PROVENANCE,
            )
        ranked = sorted(totals.items(), key=lambda item: (-item[1], _group_label(detail, item[0])))[:5]
        total = sum(totals.values())
        rows, results = [], []
        for group_id, count in ranked:
            label = _group_label(detail, group_id)
            share = int(math.floor((count / total) * 100 + 0.5)) if total else 0
            rows.append([label, _comma(count), f"{share}%"])
            results.append(_result(venue, label, count))
        caption = (
            f"Offense groups on {when} near {name}, 2020–present, ranked by record count. "
            "Shares are of those days only."
        )
        columns = ["Group", "Records", "Share"]
    table = _pattern_table(venue["venue_id"], "weekday", "Open day of week on the venue page", columns, rows)
    return _reply(
        "answered", caption, results=results, intent="weekday_pattern",
        sources=[dict(PRESENT_SOURCE)], provenance_text=PRESENT_PROVENANCE, table=table,
    )


def _compare_pair(venues: list, args, mentions: list) -> list | None:
    """The two venues named in the question. A model list cannot add a third."""
    mentioned = _mentioned_ids(mentions)
    ids = list(args.venue_ids or [])
    if len(mentioned) == 2 and len(set(mentioned)) == 2:
        ids = mentioned
    elif len(ids) != 2 or len(set(ids)) != 2:
        return None
    elif mentioned and not set(mentioned) <= set(ids):
        return None
    pair = [_roster_venue(venues, venue_id) for venue_id in ids]
    if any(venue is None for venue in pair):
        return None
    return pair


def _year_count_clause(year: str | None) -> str:
    if year is None:
        return ""
    if year <= "2023":
        return f"{year} counts are LAPD reports."
    if year == "2024":
        return ""
    return f"{year} counts are NIBRS offenses."


def _tool_compare_groups(pair: list, args) -> dict | None:
    groups = list(args.groups or [])
    if not groups or len(groups) > 4 or len(set(groups)) != len(groups):
        return None
    if any(group_id not in _TOOL_GROUPS for group_id in groups):
        return None
    if args.metric not in (None,):
        return None
    year = None
    if args.year is not None:
        if args.year not in _COUNT_YEARS:
            return None
        year = str(args.year)
    packed = []
    for venue in pair:
        detail = get_venue(venue["venue_id"]) or {}
        months = detail.get("merged_by_month") or {}
        if not months:
            return _reply(
                "unavailable",
                "The 2020–present offense groups are missing. Rebuild them with `python -m pipeline.merge_crime`.",
                intent="compare",
                sources=[dict(PRESENT_SOURCE)],
                provenance_text=PRESENT_PROVENANCE,
            )
        packed.append((venue, detail, _group_totals(months, year)))
    left, right = packed
    rows, results = [], []
    for group_id in groups:
        label = _group_label(left[1], group_id)
        counts = [totals.get(group_id, 0) for _venue, _detail, totals in packed]
        rows.append([label, *[_comma(count) for count in counts]])
        for venue, count in zip(pair, counts):
            results.append(_result(venue, label, count))
    names = f"{left[0]['venue_name']} and {right[0]['venue_name']}"
    label_text = " and ".join(_group_label(left[1], group_id) for group_id in groups)
    when = f" in {year}" if year else ", 2020–present"
    clause = _year_count_clause(year)
    caption = " ".join(part for part in (
        f"{label_text} near {names}{when}.",
        clause,
        "Venue areas can overlap, so the same incident may appear under both venues.",
    ) if part)
    table = {
        "columns": ["Group", left[0]["venue_name"], right[0]["venue_name"]],
        "rows": rows,
    }
    return _reply(
        "answered", caption, results=results, intent="compare",
        sources=[dict(PRESENT_SOURCE)], provenance_text=PRESENT_PROVENANCE, table=table,
    )


def _tool_compare(venues: list, args, mentions: list) -> dict | None:
    if args.metric not in (None, "present_count", "density"):
        return None
    pair = _compare_pair(venues, args, mentions)
    if pair is None:
        return None
    if args.groups or args.year is not None:
        if args.metric is not None:
            return None
        return _tool_compare_groups(pair, args)
    figures = [_present_figures(venue) for venue in pair]
    if any(item is None for item in figures):
        return _reply(
            "unavailable",
            "The 2020–present venue figures are missing. Rebuild them with `python -m pipeline.merge_crime` and `python -m pipeline.city_baseline`.",
            intent="compare",
            sources=[dict(PRESENT_SOURCE)],
            provenance_text=PRESENT_PROVENANCE,
        )
    metric = args.metric or "present_count"
    lines = []
    results = []
    for venue, (count, rate) in zip(pair, figures):
        results.append(_result(venue, "2020–present", count))
        if metric == "density":
            lines.append(f"{venue['venue_name']} — {rate:,.1f} per km², {count:,} records, 2020–present.")
        else:
            lines.append(f"{venue['venue_name']} — {count:,} records, 2020–present.")
    if metric == "density":
        left_rate, right_rate = figures[0][1], figures[1][1]
        difference = abs(left_rate - right_rate)
        larger = pair[0] if left_rate >= right_rate else pair[1]
        gap = f"\n{larger['venue_name']} is higher by {difference:,.1f} per km²." if difference else "\nThe densities are equal."
    else:
        difference = abs(figures[0][0] - figures[1][0])
        larger = max(zip(pair, figures), key=lambda item: item[1][0])[0]
        gap = f"\n{larger['venue_name']} has {difference:,} more records." if difference else "\nThe counts are equal."
    answer = "\n".join(lines) + gap + (
        "\nVenue areas can overlap, so the same incident may appear under both venues. "
        "These counts are not added into a unique citywide total."
    )
    return _reply(
        "answered", answer, results=results, intent="compare",
        sources=[dict(PRESENT_SOURCE)], provenance_text=PRESENT_PROVENANCE,
    )


def _tool_rank_change(venues: list) -> dict | None:
    baseline = load_city_baseline() or {}
    through = str((baseline.get("present") or {}).get("through") or "")
    end = through[:4] if len(through) >= 4 and through[:4].isdigit() else "2026"
    packed = []
    for venue in venues:
        detail = get_venue(venue["venue_id"]) or {}
        months = detail.get("merged_by_month") or {}
        if not months:
            return _reply(
                "unavailable",
                "The 2020–present offense groups are missing. Rebuild them with `python -m pipeline.merge_crime`.",
                intent="rank_venues",
                sources=[dict(PRESENT_SOURCE)],
                provenance_text=PRESENT_PROVENANCE,
            )
        start_count = sum(_group_totals(months, "2020").values())
        end_count = sum(_group_totals(months, end).values())
        packed.append((venue, start_count, end_count, end_count - start_count))
    packed.sort(key=lambda item: (-item[3], item[0]["venue_name"]))
    partial = f" {end} runs through {through}, not a full year." if through.startswith(end) else ""
    caption = (
        f"Venues ranked by the change in records from 2020 to {end} inside the 800 m circle.{partial}"
    )
    rows, results = [], []
    for index, (venue, start_count, end_count, change) in enumerate(packed, start=1):
        sign = "+" if change > 0 else ""
        rows.append([str(index), venue["venue_name"], _comma(start_count), _comma(end_count), f"{sign}{_comma(change)}"])
        results.append(_result(venue, f"{end} change", change))
    return _reply(
        "answered", caption, results=results, intent="rank_venues",
        sources=[dict(PRESENT_SOURCE)], provenance_text=PRESENT_PROVENANCE,
        table={"columns": ["Rank", "Venue", "2020", end, "Change"], "rows": rows},
    )


def _tool_rank(venues: list, args) -> dict | None:
    if args.metric not in (None, "present_count", "density", "change"):
        return None
    if args.metric == "change":
        return _tool_rank_change(venues)
    metric = args.metric or "density"
    packed = []
    for venue in venues:
        figures = _present_figures(venue)
        if figures is None:
            return _reply(
                "unavailable",
                "The 2020–present venue figures are missing. Rebuild them with `python -m pipeline.merge_crime` and `python -m pipeline.city_baseline`.",
                intent="rank_venues",
                sources=[dict(PRESENT_SOURCE)],
                provenance_text=PRESENT_PROVENANCE,
            )
        packed.append((venue, figures[0], figures[1]))
    if metric == "density":
        packed.sort(key=lambda item: (-item[2], item[0]["venue_name"]))
        caption = "Venues ranked by 2020–present crime density inside the 800 m circle."
    else:
        packed.sort(key=lambda item: (-item[1], item[0]["venue_name"]))
        caption = "Venues ranked by 2020–present record count inside the 800 m circle."
    rows, results = [], []
    for index, (venue, count, rate) in enumerate(packed, start=1):
        rows.append([str(index), venue["venue_name"], _comma(count), f"{rate:,.1f}"])
        results.append(_result(venue, "2020–present", count))
    table = {
        "columns": ["Rank", "Venue", "Records", "Per km²"],
        "rows": rows,
    }
    return _reply(
        "answered", caption, results=results, intent="rank_venues",
        sources=[dict(PRESENT_SOURCE)], provenance_text=PRESENT_PROVENANCE, table=table,
    )


def _run_tool(interpretation, venues: list, selected_id: str | None, mentions: list, summary: dict, message: str) -> dict:
    if interpretation.tool == "trend":
        interpretation = _bounded_trend(interpretation, message)
        if interpretation is None:
            return _tool_refused()
    args = interpretation.arguments or ToolArguments()
    name = interpretation.tool
    if name == "rank_venues":
        reply = _tool_rank(venues, args)
        return reply or _tool_refused()
    if name == "compare":
        if len(set(_mentioned_ids(mentions))) > 2:
            return _reply("clarification", "Please name exactly two venues to compare.", intent="compare")
        reply = _tool_compare(venues, args, mentions)
        return reply or _tool_refused()
    venue = _tool_venue(args, venues, selected_id, mentions)
    if venue is None:
        return _tool_refused()
    if name == "top_groups":
        reply = _tool_top_groups(venue, args)
    elif name == "trend":
        reply = _tool_trend(venue, args)
    elif name == "weekday_pattern":
        reply = _tool_weekday(venue, args)
    elif name == "event_lift":
        event_type = (args.event_type or "both")
        if event_type not in {"permit", "home", "both"}:
            return _tool_refused()
        phrase = {"permit": "permit days", "home": "home games", "both": "event day lift"}[event_type]
        return _event_lift_answer(venue, phrase)
    elif name == "nearest_facility":
        if args.facility_type not in {"fire", "police", "hospital"}:
            return _tool_refused()
        try:
            answer, results, sources = context_answer(args.facility_type, venue, summary["meta"])
        except ContextUnavailable as exc:
            return _reply("unavailable", f"{exc} I cannot calculate a reliable answer until the data is rebuilt.", intent=args.facility_type)
        return _reply("answered", answer, results=results, intent=args.facility_type, sources=sources)
    else:
        return _tool_refused()
    return reply or _tool_refused()


def _align_tool(tool_call, message: str, engine: dict):
    """A question Python can read completely keeps those arguments."""
    parsed = _tool_from_question(message)
    if parsed is None:
        return tool_call
    if tool_call is None:
        return parsed
    if tool_call.tool != parsed.tool:
        engine.update(engine="fallback", model=None, engine_note="Verified by local rules.")
        return parsed
    current = tool_call.arguments or ToolArguments()
    incoming = parsed.arguments or ToolArguments()
    updates = {}
    for field in ("metric", "days", "group", "groups", "from_year", "to_year", "year"):
        value = getattr(incoming, field)
        if value is not None and getattr(current, field) != value:
            updates[field] = value
    if not updates:
        return tool_call
    return tool_call.model_copy(update={"arguments": current.model_copy(update=updates)})


def _present_time_block(venue_id: str) -> dict:
    """Part-of-day counts through the latest month, not the 2020–2024 file alone."""
    merged = (get_venue(venue_id) or {}).get("merged_time") or {}
    if merged.get("periods"):
        return merged
    return crime_time_for(venue_id) or {}


def _time_of_day_answer(venue: dict) -> dict:
    block = _present_time_block(venue["venue_id"])
    periods = [period for period in (block.get("periods") or []) if period.get("label")]
    if not periods:
        return _reply(
            "unavailable",
            "The part-of-day counts are missing. Rebuild them with `python -m pipeline.crime_time`.",
            intent="time_of_day",
            sources=[dict(SOURCE)],
            provenance_text=PROVENANCE,
        )
    usable = sum(int(period.get("count") or 0) for period in periods)
    rows, results = [], []
    for period in periods:
        count = int(period.get("count") or 0)
        share = int(math.floor((count / usable) * 100 + 0.5)) if usable else 0
        rows.append([str(period["label"]), _comma(count), f"{share}%"])
        results.append(_result(venue, str(period["label"]), count))
    caption = (
        f"Part of day near {venue['venue_name']}: {usable:,} records with a usable hour, 2020–present. "
        "12:00 is often an unknown hour."
    )
    return _reply(
        "answered", caption, results=results, intent="time_of_day",
        sources=[dict(PRESENT_SOURCE)], provenance_text=PRESENT_PROVENANCE,
        table={"columns": ["Part of day", "Records", "Share"], "rows": rows},
    )


def _period_count(block: dict, period_id: str, year: str | None, group: str | None) -> int | None:
    scopes = []
    if year:
        months = block.get("by_month") or {}
        if not months:
            return None
        scopes = [scoped for month, scoped in months.items() if str(month).startswith(year)]
    else:
        scopes = [block]
    total = 0
    found = False
    for scope in scopes:
        for period in scope.get("periods") or []:
            if period.get("id") != period_id:
                continue
            found = True
            if group:
                total += sum(int(item.get("count") or 0) for item in (period.get("groups") or []) if item.get("id") == group)
            else:
                total += int(period.get("count") or 0)
    if year and not found:
        return 0
    return total if found or year else None


def _period_answer(venue: dict, message: str) -> dict:
    block = _present_time_block(venue["venue_id"])
    period_id = _period_id(message)
    periods = [period for period in (block.get("periods") or []) if period.get("id") == period_id]
    if not periods:
        return _reply(
            "unavailable",
            "The part-of-day counts are missing. Rebuild them with `python -m pipeline.crime_time`.",
            intent="period",
            sources=[dict(SOURCE)],
            provenance_text=PROVENANCE,
        )
    year = _single_year(message)
    if year == "bad":
        year = None
    group = _group_id(message)
    if group == "conflict":
        group = None
    count = _period_count(block, period_id, year, group if group else None)
    if count is None:
        return _reply(
            "unavailable",
            "The part-of-day counts are missing. Rebuild them with `python -m pipeline.crime_time`.",
            intent="period",
            sources=[dict(SOURCE)],
            provenance_text=PROVENANCE,
        )
    label = str(periods[0].get("label") or period_id)
    detail = get_venue(venue["venue_id"]) or {}
    subject = _group_label(detail, group) if group else "Records"
    when = f" in {year}" if year else ", 2020–present"
    caption = (
        f"{subject} during {label} near {venue['venue_name']}{when}: {count:,}. "
        "12:00 is often an unknown hour."
    )
    return _reply(
        "answered", caption, results=[_result(venue, subject if group else label, count)], intent="period",
        sources=[dict(PRESENT_SOURCE)], provenance_text=PRESENT_PROVENANCE,
    )


def _period_groups_answer(venue: dict, message: str) -> dict:
    block = _present_time_block(venue["venue_id"])
    period_id = _period_id(message)
    periods = [period for period in (block.get("periods") or []) if period.get("id") == period_id]
    groups = list((periods[0].get("groups") if periods else None) or [])
    if not groups:
        return _reply(
            "unsupported",
            "I can give the part-of-day chart, but offense groups for that part of day are missing. "
            f"{HELP}",
            intent="period_groups",
        )
    label = str(periods[0].get("label") or period_id)
    ranked = sorted(groups, key=lambda item: (-int(item.get("count") or 0), str(item.get("label") or "")))
    total = sum(int(item.get("count") or 0) for item in ranked)
    rows, results = [], []
    for item in ranked:
        count = int(item.get("count") or 0)
        share = int(math.floor((count / total) * 100 + 0.5)) if total else 0
        rows.append([str(item.get("label") or item.get("id")), _comma(count), f"{share}%"])
        results.append(_result(venue, str(item.get("label") or item.get("id")), count))
    caption = (
        f"Offense groups during {label} near {venue['venue_name']}, 2020–present. "
        "Shares are of that part of day only. "
        "12:00 is often an unknown hour."
    )
    return _reply(
        "answered", caption, results=results, intent="period_groups",
        sources=[dict(PRESENT_SOURCE)], provenance_text=PRESENT_PROVENANCE,
        table={"columns": ["Group", "Records", "Share"], "rows": rows},
    )


def _rank_by_group(venues: list, group_id: str | None) -> dict:
    if not group_id or group_id not in _TOOL_GROUPS:
        return _reply(
            "unsupported",
            "I can rank the venues by one offense group, such as robbery, for 2020–present. Name the group. "
            f"{HELP}",
        )
    packed = []
    label = group_id
    for venue in venues:
        detail = get_venue(venue["venue_id"]) or {}
        months = detail.get("merged_by_month") or {}
        if not months:
            return _reply(
                "unavailable",
                "The 2020–present offense groups are missing. Rebuild them with `python -m pipeline.merge_crime`.",
                intent="rank_group",
                sources=[dict(PRESENT_SOURCE)],
                provenance_text=PRESENT_PROVENANCE,
            )
        label = _group_label(detail, group_id)
        packed.append((venue, _group_totals(months).get(group_id, 0)))
    packed.sort(key=lambda item: (-item[1], item[0]["venue_name"]))
    rows, results = [], []
    for index, (venue, count) in enumerate(packed, start=1):
        rows.append([str(index), venue["venue_name"], _comma(count)])
        results.append(_result(venue, label, count))
    caption = (
        f"Venues ranked by {label} records, 2020–present, inside the 800 m circle."
    )
    return _reply(
        "answered", caption, results=results, intent="rank_group",
        sources=[dict(PRESENT_SOURCE)], provenance_text=PRESENT_PROVENANCE,
        table={"columns": ["Rank", "Venue", "Records"], "rows": rows},
    )


def answer_question(message: str, venue_id: str | None = None, prior_message: str | None = None) -> dict:
    engine = {"engine": "data", "model": None, "engine_note": None}
    normalized = _expand_relative_time(_normalize(message))
    response = _answer_question(normalized, venue_id, engine, prior_message)
    response.update(engine)
    response.setdefault("resolved_message", normalized)
    return _note_switch(response, normalized)


def answer_deterministic(message: str, venue_id: str | None = None, prior_message: str | None = None) -> dict:
    """The local parser and its templates, with no model call and no narration."""
    return _answer_closed(message, venue_id, prior_message)


def answer_routed(
    message: str,
    venue_id: str | None = None,
    *,
    intent: str | None = None,
    tool=None,
) -> dict:
    """Run one already-validated intent or tool. No model call and no narration."""
    return _answer_closed(message, venue_id, preset_intent=intent, preset_tool=tool)


def _answer_closed(
    message: str,
    venue_id: str | None = None,
    prior_message: str | None = None,
    *,
    preset_intent: str | None = None,
    preset_tool=None,
) -> dict:
    engine = {"engine": "data", "model": None, "engine_note": None}
    normalized = _expand_relative_time(_normalize(message))
    token = skip_explanation.set(True)
    try:
        response = _answer_question(
            normalized,
            venue_id,
            engine,
            prior_message,
            deterministic=True,
            preset_intent=preset_intent,
            preset_tool=preset_tool,
        )
    finally:
        skip_explanation.reset(token)
    response.update(engine)
    response.setdefault("resolved_message", normalized)
    return _note_switch(response, normalized)


def _join_answers(parts: list[dict]) -> dict:
    """Show every part that was calculated. A dropped part is stated, not skipped."""
    first = parts[0]
    if first["status"] != "answered":
        return first
    visible, provenance = _visible_answer(first["answer"])
    results = list(first.get("results") or [])
    table = first.get("table")
    for part in parts[1:]:
        if part["status"] != "answered":
            if _PARTIAL_NOTICE not in visible:
                visible = f"{visible} {_PARTIAL_NOTICE}"
            continue
        more, _ignored = _visible_answer(part["answer"])
        visible = f"{visible}\n{more}"
        results.extend(part.get("results") or [])
        if table is None and part.get("table"):
            table = part["table"]
    body = _reply(
        "answered",
        visible,
        results=results,
        intent=first.get("question_type"),
        sources=first.get("sources"),
        provenance_text=provenance.lstrip() or None,
    )
    if table:
        body["table"] = table
    if first.get("explanation"):
        body["explanation"] = first["explanation"]
    return body


def _answer_question(message: str, venue_id: str | None, engine: dict, prior_message: str | None = None, *, skip_split: bool = False, deterministic: bool = False, preset_intent: str | None = None, preset_tool=None) -> dict:
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
    if not skip_split:
        clauses = _split_clauses(normalized)
        if clauses:
            shared = clauses[:]
            for index, clause in enumerate(shared):
                donor = next((other for other in shared if other is not clause and _mentioned_ids(_mentions(other, venues))), None)
                if donor:
                    shared[index] = _with_venue(clause, donor, venues)
            answered = [
                _answer_question(clause, venue_id, engine, skip_split=True, deterministic=deterministic)
                for clause in shared
            ]
            response = _join_answers(answered)
            response["resolved_message"] = normalized
            return response
    if prior_message and not venue_id and not mentions:
        rewritten = _rewrite_year_follow_up(normalized, _normalize(prior_message), venues)
        if rewritten:
            response = _answer_question(rewritten, venue_id, engine, skip_split=True, deterministic=deterministic)
            response["resolved_message"] = rewritten
            return response
    if prior_message and _is_venue_fragment(normalized, mentions):
        rewritten = _rewrite_follow_up(normalized, _normalize(prior_message), venues)
        if rewritten:
            response = _answer_question(rewritten, venue_id, engine, skip_split=True, deterministic=deterministic)
            response["resolved_message"] = rewritten
            return response
        return _reply(
            "clarification",
            "I only remember the previous question, and it was not about one venue. "
            "Ask a full question about this venue.",
            intent="follow_up",
        )
    intent = _intent(normalized, mentions)
    tool_call = None
    if _outside_scope(normalized, mentions):
        intent = None
    elif preset_tool is not None:
        tool_call = preset_tool
        intent = "tool"
    elif preset_intent is not None:
        intent = preset_intent
    elif deterministic:
        if intent is None and _tool_opening(normalized):
            tool_call = _tool_from_question(normalized)
    elif settings()["claude_configured"]:
        try:
            interpretation = interpret_question(message, venues, venue_id)
            engine.update(engine=settings()["provider"], model=settings()["model"])
            accepted = interpretation.scope_supported and interpretation.intent != "unsupported"
            if intent is not None:
                # A parsed question keeps its calculation. Claude only fills in wording the word list missed.
                if not accepted or interpretation.intent != intent:
                    engine.update(engine="fallback", model=None, engine_note="Verified by local rules.")
            elif accepted and interpretation.intent == "tool" and interpretation.tool:
                tool_call = interpretation
            elif _tool_opening(normalized):
                tool_call = _tool_from_question(normalized)
                if tool_call is not None:
                    engine.update(engine="fallback", model=None, engine_note="Verified by local rules.")
            elif accepted:
                intent = interpretation.intent
            else:
                intent = None
        except ClaudeUnavailable:
            engine.update(engine="fallback", engine_note="The model is unavailable; using the data parser.")
            if intent is None and _tool_opening(normalized):
                tool_call = _tool_from_question(normalized)
    elif intent is None and _tool_opening(normalized):
        tool_call = _tool_from_question(normalized)
    if tool_call is not None:
        tool_call = _align_tool(tool_call, normalized, engine)
        for start, end, candidates in mentions:
            if len(candidates) > 1:
                names = ", ".join(venue["venue_name"] for venue in candidates)
                return _reply(
                    "clarification", f"Which venue do you mean by “{normalized[start:end]}”? {names}.",
                    choices=[
                        {
                            "venue_id": venue["venue_id"],
                            "venue_name": venue["venue_name"],
                            "message": normalized[:start] + _normalize(venue["venue_name"]) + normalized[end:],
                        }
                        for venue in candidates
                    ],
                    intent=tool_call.tool,
                )
        return _run_tool(tool_call, venues, venue_id, mentions, summary, normalized)
    if intent is None:
        return _reply("unsupported", "I can't answer that from these tables. Ask about one venue, a comparison, or a number on the page.")
    if intent == "citywide":
        return _citywide_answer()
    if intent == "rank_group":
        return _rank_by_group(venues, _group_id(normalized))
    if intent == "rank_count":
        ranked = _tool_rank(venues, ToolArguments(metric="present_count"))
        return ranked or _reply("unsupported", f"I cannot rank the venues from the processed data. {HELP}")
    if intent == "rank_group_missing":
        return _reply(
            "unsupported",
            "I can rank the venues by one offense group, such as robbery, for 2020–present. Name the group. "
            f"{HELP}",
        )

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
    if intent == "seasonal_estimate":
        return _seasonal_answer(selected[0])
    if intent in SLICE_INTENTS:
        return _slice_answer(intent, selected[0], normalized)
    if intent in HEADLINE_INTENTS:
        return _headline_answer(intent, selected[0])
    if intent == "event_lift":
        return _event_lift_answer(selected[0], normalized)
    if intent == "time_of_day":
        return _time_of_day_answer(selected[0])
    if intent == "period":
        return _period_answer(selected[0], normalized)
    if intent == "period_groups":
        return _period_groups_answer(selected[0], normalized)
    if intent in PATTERN_INTENTS:
        return _pattern_answer(intent, selected[0], normalized)
    if intent == "other_contents":
        return _other_contents_answer(selected[0])
    if intent == "victim_age":
        return _victim_age_answer()
    if intent == "station_crime":
        return _station_crime_answer(selected[0], normalized)
    if intent in CONTEXT_INTENTS:
        try:
            if intent == "rail" and re.search(r"\b(nearest|closest)\b", normalized):
                return _nearest_rail_answer(selected[0], summary["meta"])
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
        answer = (
            f"The most common LAPD crime description near {venue['venue_name']} in 2020–2024 is {labels}. "
            "This is the raw report label for 2020–2024, not an offense group for 2020–present."
        )
        if len(winners) > 1:
            answer = (
                f"The most common LAPD crime descriptions near {venue['venue_name']} in 2020–2024 are tied: {labels}. "
                "These are raw report labels for 2020–2024, not offense groups for 2020–present."
            )
        return _reply("answered", answer, results=results, intent=intent)

    if intent == "compare":
        figures = [_present_figures(venue) for venue in selected]
        if any(item is None for item in figures):
            return _reply(
                "unavailable",
                "The 2020–present venue figures are missing. Rebuild them with `python -m pipeline.merge_crime`.",
                intent=intent,
                sources=[dict(PRESENT_SOURCE)],
                provenance_text=PRESENT_PROVENANCE,
            )
        results = [_result(venue, "2020–present", count) for venue, (count, _rate) in zip(selected, figures)]
        answer = "\n".join(
            f"{row['venue_name']} — {row['count']:,} records, 2020–present."
            for row in results
        )
        first, second = results
        difference = abs(first["count"] - second["count"])
        if difference:
            larger = max(results, key=lambda row: row["count"])
            answer += f"\n{larger['venue_name']} has {difference:,} more records (absolute difference)."
        else:
            answer += "\nThe counts are equal (absolute difference: 0 records)."
        answer += (
            "\nThese are the same 2020–present totals as a venue count, not the 2020–2024 report totals. "
            "Venue areas can overlap, so the same incident may appear under both venues. "
            "These counts are not added into a unique citywide total."
        )
        return _reply(
            "answered", answer, results=results, intent=intent,
            sources=[dict(PRESENT_SOURCE)], provenance_text=PRESENT_PROVENANCE,
        )

    results = [_result(venue, "All categories", venue["crime_count_nearby"]) for venue in selected]
    answer = "\n".join(
        f"{row['venue_name']} — {row['category']}: {row['count']:,} reported incidents, 2020–2024."
        for row in results
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


def _venue_month_counts(venue: dict) -> dict[str, int]:
    merged = venue.get("merged_by_month")
    if isinstance(merged, dict) and merged:
        totals: dict[str, int] = {}
        for month, groups in merged.items():
            if isinstance(groups, dict):
                totals[str(month)] = sum(int(count or 0) for count in groups.values())
        return totals
    raw = venue.get("crime_by_month") or {}
    return {str(month): int(count or 0) for month, count in raw.items()}


def seasonal_estimates(by_month: dict[str, int], horizon: int = 3) -> list[dict]:
    """Average of the same calendar month in up to the three prior years."""
    keys = sorted(key for key in by_month if re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", str(key)))
    if not keys:
        return []
    start_year, start_month = (int(part) for part in keys[0].split("-"))
    end_year, end_month = (int(part) for part in keys[-1].split("-"))
    filled: dict[str, int] = {}
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        key = f"{year:04d}-{month:02d}"
        filled[key] = int(by_month.get(key) or 0)
        month += 1
        if month == 13:
            month = 1
            year += 1
    estimates = []
    year, month = end_year, end_month
    for _step in range(horizon):
        month += 1
        if month == 13:
            month = 1
            year += 1
        samples = []
        for prior in range(year - 1, year - 4, -1):
            key = f"{prior:04d}-{month:02d}"
            if key in filled:
                samples.append(filled[key])
        if not samples:
            continue
        estimates.append({
            "key": f"{year:04d}-{month:02d}",
            "count": (sum(samples) + len(samples) // 2) // len(samples),
            "years": len(samples),
        })
    return estimates


def _with_home_game_schedule(detail: dict, estimates: list[dict]) -> tuple[list[dict], str]:
    """Scale a month only when the stored home-game schedule lists a game in it."""
    if not detail.get("home_games_available"):
        return estimates, ""
    multiplier = detail.get("home_game_multiplier")
    dates = detail.get("scheduled_home_games") or []
    applied = False
    if multiplier and dates:
        adjusted = []
        for item in estimates:
            year, month = (int(part) for part in item["key"].split("-"))
            days = calendar.monthrange(year, month)[1]
            games = sum(1 for day in dates if str(day).startswith(item["key"]))
            count = scale_month(item["count"], days, games, float(multiplier))
            applied = applied or bool(games)
            adjusted.append({**item, "count": count})
        estimates = adjusted
    if applied:
        note = " Months with a listed home game are scaled by the count-model multiplier."
    else:
        note = " No home game on the stored schedule falls in these months, so the home-game multiplier is not applied."
    return estimates, note


_seasonal_cache: dict[str, dict] = {}


_CODE_CHANGE = (
    " That split is a coding change: through March 6, 2024 a row is one police report, "
    "and from March 7 it is one NIBRS offense."
)
_SWITCH_INTENTS = {
    "present_total", "density", "city", "busiest_month", "top_groups", "weekend",
    "weekend_groups", "time_of_day", "period", "period_groups", "compare",
    "rank_venues", "rank_count", "rank_group", "citywide", "since_count", "year_count",
}
_RANK_INTENTS = {"rank_venues", "rank_count", "rank_group"}


def _spans_switch(start: int, end: int) -> bool:
    """True when the years include both sides of March 7, 2024."""
    return start <= 2024 <= end


def _nibrs_group_months(detail: dict, grouper) -> dict[str, dict[str, int]]:
    categories = ((detail.get("nibrs") or {}).get("categories_by_month") or {})
    months: dict[str, dict[str, int]] = {}
    for month, mapping in categories.items():
        groups: dict[str, int] = {}
        for label, count in (mapping or {}).items():
            group_id = grouper(str(label))
            groups[group_id] = groups.get(group_id, 0) + int(count or 0)
        months[str(month)] = groups
    return months


def _split_group_months(detail: dict) -> tuple[dict[str, dict[str, int]], dict[str, dict[str, int]]]:
    """Reports through March 6, 2024, then NIBRS from March 7. March is split, not doubled.

    The stored March total was built with the older wording, so that wording is
    what gets subtracted. The NIBRS side is then counted by offense code.
    """
    keyword_months = _nibrs_group_months(detail, crime_group)
    code_months = _nibrs_group_months(detail, nibrs_group)
    reports: dict[str, dict[str, int]] = {}
    nibrs: dict[str, dict[str, int]] = {}
    for month, groups in (detail.get("merged_by_month") or {}).items():
        text = str(month)
        counts = {str(key): int(value or 0) for key, value in (groups or {}).items()}
        if text < "2024-03":
            reports[text] = counts
        elif text == "2024-03":
            nib = keyword_months.get(text, {})
            legacy = {
                key: value - int(nib.get(key, 0))
                for key, value in counts.items()
                if value - int(nib.get(key, 0)) > 0
            }
            if legacy:
                reports[text] = legacy
            coded = code_months.get(text, {})
            if coded:
                nibrs[text] = {key: int(value) for key, value in coded.items() if int(value)}
        else:
            nibrs[text] = code_months.get(text) or counts
    return reports, nibrs


def _year_groups(months: dict[str, dict[str, int]], year: int) -> dict[str, int]:
    prefix = f"{year}-"
    totals: dict[str, int] = {}
    for month, groups in months.items():
        if str(month).startswith(prefix):
            for key, value in groups.items():
                totals[key] = totals.get(key, 0) + int(value)
    return totals


def _span_series_totals(detail: dict, start: int, end: int) -> tuple[int, int]:
    reports, nibrs = _split_group_months(detail)
    report_count = 0
    nibrs_count = 0
    for month, groups in reports.items():
        year = int(str(month)[:4])
        if start <= year <= end:
            report_count += sum(groups.values())
    for month, groups in nibrs.items():
        year = int(str(month)[:4])
        if start <= year <= end:
            nibrs_count += sum(groups.values())
    return report_count, nibrs_count


def _through_date() -> str:
    baseline = load_city_baseline() or {}
    return str((baseline.get("present") or {}).get("through") or (baseline.get("nibrs") or {}).get("end") or "")


def _switch_group_answer(venue: dict, detail: dict, start: int, end: int, intent: str, group_id: str | None = None) -> dict:
    """Report-series change through March 6, 2024, then the NIBRS counts after that date."""
    reports, nibrs = _split_group_months(detail)
    name = venue["venue_name"]
    through = _through_date()
    full_end = 2023 if start <= 2023 else None
    report_years = [str(year) for year in range(start, full_end + 1)] if full_end is not None and start <= full_end else []
    show_stub = start <= 2024 <= end
    stub_key = "2024 to Mar 6"
    nibrs_years = [str(year) for year in range(max(start, 2024), end + 1)]
    nibrs_yearly = {year: _year_groups(nibrs, int(year)) for year in nibrs_years}
    columns = list(report_years)
    yearly = {year: _year_groups(reports, int(year)) for year in report_years}
    if show_stub:
        columns.append(stub_key)
        yearly[stub_key] = _year_groups(reports, 2024)
    for year in nibrs_years:
        label = "2024 from Mar 7" if year == "2024" else year
        columns.append(label)
        yearly[label] = nibrs_yearly[year]
    rising = []
    if report_years:
        earlier = yearly[report_years[0]]
        later = yearly[report_years[-1]]
        wanted = [group_id] if group_id else sorted(set(earlier) | set(later))
        changes = []
        for item in wanted:
            change = later.get(item, 0) - earlier.get(item, 0)
            if group_id or change > 0:
                changes.append((change, _group_label(detail, item), item))
        changes.sort(key=lambda item: (-item[0], item[1]))
        rising = changes if group_id else changes[:5]
    change_header = f"Change to {report_years[-1]}" if report_years else None
    rows = []
    for change, label, item in rising:
        sign = "+" if change > 0 else ""
        rows.append([
            label,
            *[_comma(yearly[column].get(item, 0)) for column in columns],
            f"{sign}{_comma(change)}",
        ])
    partial = f" {end} runs through {through}, not a full year." if through.startswith(str(end)) else ""
    if not report_years:
        stub_total = sum(yearly.get(stub_key, {}).values()) if show_stub else 0
        lead = (
            "LAPD reports in this window run only from January 1 through March 6, 2024"
            + (f" ({_comma(stub_total)} reports)" if show_stub else "")
            + "."
        )
    elif rising:
        change, label, item = rising[0]
        sign = "+" if change > 0 else ""
        lead = (
            f"{label} rose the most from {report_years[0]} to {report_years[-1]}, "
            f"from {_comma(yearly[report_years[0]].get(item, 0))} to "
            f"{_comma(yearly[report_years[-1]].get(item, 0))} ({sign}{_comma(change)})."
        )
    else:
        lead = "no offense group rose across the full report years before March 6, 2024."
    crowd = " 2020 had little or no event crowd." if intent == "rose" and start == 2020 else ""
    stop = " The change stops at 2023." if report_years else ""
    caption = (
        f"Near {name}, {lead}{crowd}{partial}{stop} "
        "Counts through March 6, 2024 use the older report wording. "
        "From March 7, 2024 the same row uses the NIBRS offense code: theft is larceny, and motor-vehicle theft is Vehicle."
    )
    table = None
    if rows and change_header:
        table = _pattern_table(
            venue["venue_id"], "months", "Open incidents by month on the venue page",
            ["Group", *columns, change_header], rows,
        )
    return _reply(
        "answered",
        caption,
        results=[_result(venue, label, change) for change, label, _item in rising],
        intent=intent,
        sources=[dict(PRESENT_SOURCE)],
        provenance_text=PRESENT_PROVENANCE,
        table=table,
    )


def _asked_span(message: str) -> tuple[int, int] | None:
    pair = _year_pair(message)
    if pair:
        return pair
    since = _since_span(message)
    if since:
        return since
    year = _single_year(message)
    if year and year != "bad":
        return int(year), int(year)
    return None


def _note_switch(response: dict, message: str) -> dict:
    """Split a crime answer that crosses March 7, 2024 into the two series."""
    if response.get("status") != "answered":
        return response
    intent = response.get("question_type")
    if intent in {"rose", "trend"} or intent not in _SWITCH_INTENTS:
        return response
    if "March 7, 2024" in (response.get("answer") or ""):
        return response
    span = _asked_span(message)
    if span is not None and not _spans_switch(*span):
        return response
    if span is None and intent == "year_count":
        return response
    if intent in {"city", "density"}:
        return response
    results = response.get("results") or []
    venue_ids = []
    for row in results:
        venue_id = row.get("venue_id")
        if venue_id and venue_id not in venue_ids:
            venue_ids.append(venue_id)
    through = _through_date()
    start, end = span if span else (2020, max(_COUNT_YEARS))
    if through.startswith(str(end)):
        ended = f" through {through}"
    else:
        ended = f" through {end}"
    if intent == "citywide":
        present = (load_city_baseline() or {}).get("present") or {}
        reports = present.get("legacy_count")
        nibrs = present.get("nibrs_offense_count")
        if reports is None or nibrs is None:
            clause = _CODE_CHANGE
        else:
            clause = (
                f" {int(reports):,} are LAPD reports through March 6, 2024, and "
                f"{int(nibrs):,} are NIBRS offenses from March 7, 2024{ended}."
                + _CODE_CHANGE
            )
    elif intent in _RANK_INTENTS or len(venue_ids) > 2:
        clause = (
            " This ranking crosses March 7, 2024. Counts through March 6, 2024 are LAPD reports, "
            f"and counts from March 7, 2024{ended} are NIBRS offenses."
            + _CODE_CHANGE
        )
    else:
        pieces = []
        for venue_id in venue_ids:
            detail = get_venue(venue_id) or {}
            reports, nibrs = _span_series_totals(detail, start, end)
            name = next((row.get("venue_name") for row in results if row.get("venue_id") == venue_id), venue_id)
            pieces.append(
                f"{name}: {reports:,} LAPD reports from {start} through March 6, 2024, and "
                f"{nibrs:,} NIBRS offenses from March 7, 2024{ended}"
            )
        clause = ((" " + ". ".join(pieces) + ".") if pieces else "") + _CODE_CHANGE
    answer = response.get("answer") or ""
    marker = "\n\nSource:"
    if marker in answer:
        visible, provenance = answer.split(marker, 1)
        response["answer"] = f"{visible.rstrip()}{clause}{marker}{provenance}"
    else:
        response["answer"] = f"{answer.rstrip()}{clause}"
    return response


def _seasonal_answer(venue: dict) -> dict:
    cached = _seasonal_cache.get(venue["venue_id"])
    if cached is not None:
        return cached
    detail = get_venue(venue["venue_id"]) or venue
    estimates = seasonal_estimates(_venue_month_counts(detail))
    name = venue["venue_name"]
    if not estimates:
        reply = _reply(
            "answered",
            f"There are not enough earlier years to estimate the next months near {name}.",
            intent="seasonal_estimate",
        )
        _seasonal_cache[venue["venue_id"]] = reply
        return reply
    estimates, schedule_note = _with_home_game_schedule(detail, estimates)
    lines = [
        (
            f"{_month_label(item['key'])}: {_comma(item['count'])} estimated records, "
            f"averaged from {item['years']} earlier {'year' if item['years'] == 1 else 'years'}."
        )
        for item in estimates
    ]
    answer = (
        f"Near {name}, the next {len(estimates)} months are seasonal estimates, not recorded crime and not a certainty. "
        "Each figure is the average of that calendar month in up to the three most recent earlier years, rounded to a whole number. "
        + " ".join(lines)
        + schedule_note
        + " This does not say a crime will occur, and it is not a forecast for 2028."
    )
    results = [_result(venue, f"Estimate {item['key']}", item["count"]) for item in estimates]
    reply = _reply(
        "answered",
        answer,
        results=results,
        intent="seasonal_estimate",
        sources=[dict(PRESENT_SOURCE)],
        provenance_text=PRESENT_PROVENANCE,
    )
    _seasonal_cache[venue["venue_id"]] = reply
    return reply


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


skip_explanation: contextvars.ContextVar[bool] = contextvars.ContextVar("skip_chat_explanation", default=False)


def _maybe_explain(venue_name: str, caption: str, table: dict) -> str | None:
    if skip_explanation.get() or not settings()["claude_configured"]:
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
            singular = re.search(r"\b(?:biggest|largest) crime problem\b|\bmost common crimes?\b(?!\s+categor)", message)
            limit = 1 if singular else 5
        ranked = sorted(totals.items(), key=lambda item: (-item[1], _group_label(detail, item[0])))[:limit]
        total = sum(totals.values())
        rows = []
        results = []
        for group_id, count in ranked:
            label = _group_label(detail, group_id)
            share = int(math.floor((count / total) * 100 + 0.5)) if total else 0
            rows.append([label, _comma(count), f"{share}%"])
            results.append(_result(venue, label, count))
        heading = "Top offense group" if len(ranked) == 1 else f"Top {len(ranked)} offense groups"
        caption = (
            f"{heading} near {name}, 2020–present, ranked by record count."
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
        caption = summary or f"Day of week near {name}, 2020–present."
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
            "Shares are of those days only."
        )
        table = _pattern_table(venue_id, "weekday", "Open day of week on the venue page", ["Group", "Records", "Share"], rows)
    else:
        years = _rose_years(months)
        raw_start = int(years[0]) if years else 2020
        raw_end = int(years[-1]) if years else 2020
        if _spans_switch(raw_start, raw_end):
            return _switch_group_answer(venue, detail, raw_start, raw_end, intent)
        start = years[0]
        end = years[-1]
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
            f"Offense groups near {name} that rose the most from {start} to {end}, ranked by how many more records."
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
            baseline = (view.get("baseline") or {}).get("text")
            if baseline:
                sentences.append(baseline)
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
            baseline = (games.get("baseline") or {}).get("text")
            if baseline:
                sentences.append(baseline)
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
        return ""
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
            "This is not the 2020–2024 report total."
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
    if intent == "since_count":
        span = _since_span(message)
        if span is None:
            return _reply("unsupported", "I can't answer that from these tables. Ask about one venue, a comparison, or a number on the page.")
        start, end = span
        count = 0
        for month, groups in months.items():
            text = str(month)
            if len(text) < 4 or not text[:4].isdigit() or not start <= int(text[:4]) <= end:
                continue
            if group:
                count += int(groups.get(group) or 0)
            else:
                count += sum(int(value or 0) for value in groups.values())
        partial = f" {end} runs through {through}, not a full year." if through.startswith(str(end)) else ""
        subject = label or "Records"
        answer = (
            f"{name} — {subject}: {count:,} records from {start} through {end}.{partial}"
        )
        return _reply(
            "answered", answer, results=[_result(venue, f"{start}–{end}", count)], intent=intent,
            sources=[dict(PRESENT_SOURCE)], provenance_text=PRESENT_PROVENANCE,
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
            f"{name} — {subject}: {count:,} records, 2020–present."
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
    through = present.get("through") or "the latest extract"
    answer = (
        f"City of Los Angeles, 2020–present: {count:,} records. "
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
            f"{name} — 2020–present: {count:,} records inside the 800 m circle."
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
        category = "2020–present vs City"
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
