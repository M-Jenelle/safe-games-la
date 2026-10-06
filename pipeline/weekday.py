"""Day-of-week counts from YYYY-MM-DD dates.

Monday is 0, matching ``date.weekday``. Saturday and Sunday are the weekend.
"""

from __future__ import annotations

from datetime import date

from pipeline.crime_groups import GROUP_LABELS

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


def _item(raw) -> tuple[date | None, str]:
    if isinstance(raw, dict):
        return _parse(raw.get("date")), str(raw.get("group") or "")
    return _parse(raw), ""


def _group_rows(counts: dict[str, int]) -> list[dict]:
    rows = [
        {"id": group_id, "label": GROUP_LABELS.get(group_id, group_id), "count": count}
        for group_id, count in counts.items()
        if count
    ]
    rows.sort(key=lambda item: (-item["count"], item["label"]))
    return rows


def weekday_counts(dates) -> dict:
    """Counts for one set of dates. Undated or invalid values are left out.

    A date may be a string, or ``{"date", "group"}`` when offense groups
    should be kept on each day.
    """
    totals = {day_id: 0 for day_id, _label in DAYS}
    grouped = {day_id: {} for day_id, _label in DAYS}
    for raw in dates:
        parsed, group = _item(raw)
        if parsed is None:
            continue
        day_id = DAYS[parsed.weekday()][0]
        totals[day_id] += 1
        if group:
            bucket = grouped[day_id]
            bucket[group] = bucket.get(group, 0) + 1
    days = []
    for day_id, label in DAYS:
        day = {"id": day_id, "label": label, "count": totals[day_id]}
        groups = _group_rows(grouped[day_id])
        if groups:
            day["groups"] = groups
        days.append(day)
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
    kept = []
    buckets: dict[str, list] = {}
    for raw in dates:
        parsed, _group = _item(raw)
        if parsed is None:
            continue
        text = parsed.isoformat()
        kept.append(raw if isinstance(raw, dict) else text)
        buckets.setdefault(text[:7], []).append(kept[-1])
    block = weekday_counts(kept)
    block["by_month"] = {month: weekday_counts(rows) for month, rows in buckets.items()}
    return block


def _days_by_id(block: dict | None) -> dict[str, dict]:
    if not block:
        return {}
    return {day["id"]: day for day in block.get("days") or []}


def _day_counts(block: dict | None) -> list[int]:
    by_id = _days_by_id(block)
    return [int((by_id.get(day_id) or {}).get("count") or 0) for day_id, _label in DAYS]


def _combined_groups(blocks: tuple[dict | None, ...], day_id: str) -> list[dict]:
    counts: dict[str, int] = {}
    for block in blocks:
        day = _days_by_id(block).get(day_id) or {}
        for group in day.get("groups") or []:
            group_id = str(group.get("id") or "")
            if not group_id:
                continue
            counts[group_id] = counts.get(group_id, 0) + int(group.get("count") or 0)
    return _group_rows(counts)


def combine_weekday(*blocks: dict | None) -> dict:
    """Add day counts. Month blocks are added only when a block has ``by_month``."""
    sums = [0] * len(DAYS)
    for block in blocks:
        for index, count in enumerate(_day_counts(block)):
            sums[index] += count
    days = []
    for index, (day_id, label) in enumerate(DAYS):
        day = {"id": day_id, "label": label, "count": sums[index]}
        groups = _combined_groups(blocks, day_id)
        if groups:
            day["groups"] = groups
        days.append(day)
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
