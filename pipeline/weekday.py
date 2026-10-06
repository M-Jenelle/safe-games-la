"""Day-of-week counts from YYYY-MM-DD dates.

Monday is 0, matching ``date.weekday``. Saturday and Sunday are the weekend.
"""

from __future__ import annotations

from datetime import date

DAYS = (
    ("mon", "Monday"),
    ("tue", "Tuesday"),
    ("wed", "Wednesday"),
    ("thu", "Thursday"),
    ("fri", "Friday"),
    ("sat", "Saturday"),
    ("sun", "Sunday"),
)


def _parse(value: str) -> date | None:
    text = str(value or "")[:10]
    if len(text) != 10:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def weekday_counts(dates) -> dict:
    """Counts for one set of dates. Undated or invalid values are left out."""
    totals = {day_id: 0 for day_id, _label in DAYS}
    for raw in dates:
        parsed = _parse(raw)
        if parsed is None:
            continue
        totals[DAYS[parsed.weekday()][0]] += 1
    days = [{"id": day_id, "label": label, "count": totals[day_id]} for day_id, label in DAYS]
    weekend = totals["sat"] + totals["sun"]
    total = sum(totals.values())
    return {
        "total": total,
        "weekday_count": total - weekend,
        "weekend_count": weekend,
        "days": days,
    }


def weekday_from_dates(dates) -> dict:
    """Overall counts plus a block for each YYYY-MM that has a date."""
    kept: list[str] = []
    buckets: dict[str, list[str]] = {}
    for raw in dates:
        parsed = _parse(raw)
        if parsed is None:
            continue
        text = parsed.isoformat()
        kept.append(text)
        buckets.setdefault(text[:7], []).append(text)
    block = weekday_counts(kept)
    block["by_month"] = {month: weekday_counts(rows) for month, rows in buckets.items()}
    return block


def _day_counts(block: dict | None) -> list[int]:
    if not block:
        return [0] * len(DAYS)
    by_id = {day["id"]: int(day["count"]) for day in block.get("days") or []}
    return [by_id.get(day_id, 0) for day_id, _label in DAYS]


def combine_weekday(*blocks: dict | None) -> dict:
    """Add day counts. Month blocks are added only when a block has ``by_month``."""
    sums = [0] * len(DAYS)
    for block in blocks:
        for index, count in enumerate(_day_counts(block)):
            sums[index] += count
    days = [
        {"id": day_id, "label": label, "count": sums[index]}
        for index, (day_id, label) in enumerate(DAYS)
    ]
    weekend = sums[5] + sums[6]
    total = sum(sums)
    result = {
        "total": total,
        "weekday_count": total - weekend,
        "weekend_count": weekend,
        "days": days,
    }
    months: set[str] = set()
    for block in blocks:
        months.update((block or {}).get("by_month") or {})
    if months:
        result["by_month"] = {
            month: combine_weekday(
                *[
                    ((block or {}).get("by_month") or {}).get(month)
                    for block in blocks
                ]
            )
            for month in months
        }
    return result
