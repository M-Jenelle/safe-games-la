"""Weekday-adjusted event gap and a negative-binomial count model.

The published permit and home-game percentages stay as they are. These
figures use the same daily rows, hold the weekday fixed, and fit one
count model. They are a past association, not a forecast.
"""

from __future__ import annotations

import csv
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln

from backend.datasets import REPO_ROOT, get_crime_points

SERIES_BREAK = date(2024, 3, 7)
HOME_GAMES_CSV = REPO_ROOT / "data" / "raw" / "mlb" / "dodgers_home_games_2020_2024.csv"
IN_SEASON_MONTHS = {3, 4, 5, 6, 7, 8, 9, 10}
MIN_EVENT_DAYS = 8
_Z = 1.96

_cache: dict[tuple, dict] = {}
_home_rows: list[dict] | None = None
_future_game_dates: list[str] | None = None


def _parse_day(value: str) -> date | None:
    text = str(value or "")[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def weekday_standardized(rows: list[dict], event_field: str, count_field: str = "incident_count") -> dict | None:
    """Compare event days with other days of the same weekday.

    The other-day mean is weighted by how the event days fall across the week,
    so a Saturday event is compared with other Saturdays.
    """
    buckets: dict[int, dict[str, list[int]]] = {
        weekday: {"event": [], "other": []} for weekday in range(7)
    }
    for row in rows:
        day = _parse_day(row.get("date"))
        if day is None:
            continue
        side = "event" if int(row.get(event_field) or 0) else "other"
        buckets[day.weekday()][side].append(int(row.get(count_field) or 0))
    event_days = 0
    event_total = 0.0
    other_total = 0.0
    for bucket in buckets.values():
        if not bucket["event"] or not bucket["other"]:
            continue
        count = len(bucket["event"])
        event_days += count
        event_total += (sum(bucket["event"]) / count) * count
        other_total += (sum(bucket["other"]) / len(bucket["other"])) * count
    if event_days < MIN_EVENT_DAYS or other_total <= 0:
        return None
    event_mean = event_total / event_days
    other_mean = other_total / event_days
    difference = event_mean - other_mean
    return {
        "event_day_mean": round(event_mean, 2),
        "other_day_mean": round(other_mean, 2),
        "absolute_difference": round(difference, 2),
        "lift_pct": round(difference / other_mean * 100, 1),
        "event_day_count": event_days,
    }


def _design(rows: list[dict], event_field: str, count_field: str) -> tuple[np.ndarray, np.ndarray, int] | None:
    parsed = []
    for row in rows:
        day = _parse_day(row.get("date"))
        if day is None:
            continue
        parsed.append((
            day,
            int(row.get(count_field) or 0),
            int(bool(int(row.get(event_field) or 0))),
            int(day >= SERIES_BREAK),
        ))
    if len(parsed) < MIN_EVENT_DAYS * 2:
        return None
    weekdays = sorted({item[0].weekday() for item in parsed})
    months = sorted({item[0].month for item in parsed})
    weekday_levels = weekdays[1:]
    month_levels = months[1:]
    use_break = len({item[3] for item in parsed}) > 1
    columns = 1 + len(weekday_levels) + len(month_levels) + 1 + int(use_break)
    event_index = 1 + len(weekday_levels) + len(month_levels)
    matrix = np.zeros((len(parsed), columns), dtype=float)
    counts = np.zeros(len(parsed), dtype=float)
    for index, (day, count, event, after_break) in enumerate(parsed):
        matrix[index, 0] = 1.0
        cursor = 1
        for level in weekday_levels:
            matrix[index, cursor] = float(day.weekday() == level)
            cursor += 1
        for level in month_levels:
            matrix[index, cursor] = float(day.month == level)
            cursor += 1
        matrix[index, cursor] = event
        cursor += 1
        if use_break:
            matrix[index, cursor] = after_break
        counts[index] = count
    if counts.sum() <= 0 or matrix[:, event_index].min() == matrix[:, event_index].max():
        return None
    return matrix, counts, event_index


def _poisson_start(matrix: np.ndarray, counts: np.ndarray) -> np.ndarray:
    beta = np.zeros(matrix.shape[1])
    beta[0] = math.log(max(float(counts.mean()), 0.1))
    for _step in range(25):
        eta = np.clip(matrix @ beta, -20, 20)
        mu = np.exp(eta)
        weights = np.sqrt(np.maximum(mu, 1e-8))
        target = (eta + (counts - mu) / np.maximum(mu, 1e-8)) * weights
        updated, *_rest = np.linalg.lstsq(matrix * weights[:, None], target, rcond=None)
        if np.max(np.abs(updated - beta)) < 1e-8:
            return updated
        beta = updated
    return beta


def _fit_negative_binomial(matrix: np.ndarray, counts: np.ndarray, event_index: int) -> dict | None:
    start = np.zeros(matrix.shape[1] + 1)
    start[:-1] = _poisson_start(matrix, counts)
    start[-1] = 0.0

    def negative_log_likelihood(theta: np.ndarray) -> float:
        beta = theta[:-1]
        alpha = math.exp(float(np.clip(theta[-1], -8, 8)))
        mu = np.exp(np.clip(matrix @ beta, -20, 20))
        dispersion = 1.0 / alpha
        log_likelihood = (
            gammaln(counts + dispersion)
            - gammaln(dispersion)
            - gammaln(counts + 1)
            + dispersion * np.log(dispersion / (dispersion + mu))
            + counts * np.log(mu / (dispersion + mu))
        )
        if not np.isfinite(log_likelihood).all():
            return 1e12
        return float(-log_likelihood.sum())

    result = minimize(negative_log_likelihood, start, method="BFGS")
    gradient = float(np.linalg.norm(result.jac)) if result.jac is not None else math.inf
    # BFGS often stops on precision loss after the gradient is already flat.
    if (not result.success and gradient > 1) or not np.isfinite(result.fun):
        return None
    coefficient = float(result.x[event_index])
    hessian = result.hess_inv
    covariance = hessian.todense() if hasattr(hessian, "todense") else np.asarray(hessian)
    variance = float(covariance[event_index, event_index])
    if not math.isfinite(coefficient) or not math.isfinite(variance) or variance <= 0:
        return None
    error = math.sqrt(variance)
    if error > 2:
        return None
    low = math.exp(coefficient - _Z * error)
    high = math.exp(coefficient + _Z * error)
    multiplier = math.exp(coefficient)
    if not all(math.isfinite(value) for value in (multiplier, low, high)):
        return None
    if multiplier < 0.05 or multiplier > 20 or low > high:
        return None
    return {
        "multiplier": round(multiplier, 2),
        "low": round(low, 2),
        "high": round(high, 2),
    }


def fit_count_model(rows: list[dict], event_field: str, count_field: str = "incident_count") -> dict | None:
    designed = _design(rows, event_field, count_field)
    if designed is None:
        return None
    return _fit_negative_binomial(*designed)


def describe_baseline(
    rows: list[dict],
    event_field: str,
    kind: str,
    count_field: str = "incident_count",
    unit: str = "reports",
) -> dict:
    """kind is 'permit' or 'home game'. Cached by the row dates and event flags."""
    key = (
        kind,
        count_field,
        unit,
        tuple((row.get("date"), int(row.get(event_field) or 0), int(row.get(count_field) or 0)) for row in rows),
    )
    cached = _cache.get(key)
    if cached is not None:
        return cached
    weekday = weekday_standardized(rows, event_field, count_field)
    model = fit_count_model(rows, event_field, count_field) if weekday else None
    label = "Permit days" if kind == "permit" else "Home-game days"
    other = "other days of the same weekday"
    sentences = []
    if weekday:
        difference = float(weekday["absolute_difference"])
        lift = float(weekday["lift_pct"])
        sentences.append(
            f"Holding the weekday fixed, {label.lower()} average {weekday['event_day_mean']:.2f} {unit} "
            f"and {other} average {weekday['other_day_mean']:.2f}, "
            f"a difference of {difference:+.2f}/day · {lift:+.1f}%."
        )
    if model:
        dates = [parsed for row in rows if (parsed := _parse_day(row.get("date")))]
        holds_break = any(day < SERIES_BREAK for day in dates) and any(day >= SERIES_BREAK for day in dates)
        break_clause = (
            " and giving days on or after March 7, 2024 their own level"
            if holds_break else ""
        )
        sentences.append(
            "A negative-binomial model of the daily count, holding weekday and month fixed"
            f"{break_clause}, "
            f"puts the multiplier at {model['multiplier']:.2f} "
            f"(95% range {model['low']:.2f} to {model['high']:.2f})."
        )
    if sentences:
        sentences.append("These figures do not replace the percentage in the table. They are a past association, not a forecast.")
    payload = {"weekday": weekday, "model": model, "text": " ".join(sentences)}
    _cache[key] = payload
    return payload


def scale_month(count: float, days_in_month: int, game_days: int, multiplier: float) -> int:
    """Raise a no-event month total for listed game days. Zero game days leave it unchanged."""
    if days_in_month <= 0 or game_days <= 0 or multiplier == 1:
        return int(round(count))
    share = min(game_days, days_in_month) / days_in_month
    return int(round(count * (1 + share * (multiplier - 1))))


def _calendar_days(start: date, end: date) -> list[date]:
    days = []
    cursor = start
    while cursor <= end:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def home_game_dates() -> set[str]:
    if not HOME_GAMES_CSV.exists():
        return set()
    dates = set()
    with HOME_GAMES_CSV.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("venue_name") or "") != "Dodger Stadium":
                continue
            day = _parse_day(row.get("date") or "")
            if day is None or day.month not in IN_SEASON_MONTHS:
                continue
            if str(row.get("status") or "Final").lower() not in {"final", "completed", "game over"}:
                continue
            dates.add(day.isoformat())
    return dates


def future_home_game_dates() -> list[str]:
    """Home-game dates after the published 2020–2024 comparison. The stored file has none."""
    global _future_game_dates
    if _future_game_dates is None:
        _future_game_dates = sorted(day for day in home_game_dates() if day > "2024-12-31")
    return list(_future_game_dates)


def home_game_rows() -> list[dict]:
    """Daily report counts for March–October 2020–2024, flagged on completed home games."""
    global _home_rows
    if _home_rows is not None:
        return _home_rows
    games = home_game_dates()
    block = get_crime_points("V01") or {}
    totals: dict[str, int] = {}
    for point in block.get("points") or []:
        day = str(point.get("date") or "")[:10]
        parsed = _parse_day(day)
        if parsed is None or parsed.month not in IN_SEASON_MONTHS or not (2020 <= parsed.year <= 2024):
            continue
        totals[day] = totals.get(day, 0) + 1
    rows = []
    for year in range(2020, 2025):
        for month in sorted(IN_SEASON_MONTHS):
            start = date(year, month, 1)
            if month == 12:
                end = date(year, 12, 31)
            else:
                end = date(year, month + 1, 1) - timedelta(days=1)
            for day in _calendar_days(start, end):
                key = day.isoformat()
                rows.append({
                    "date": key,
                    "incident_count": totals.get(key, 0),
                    "is_home_game": int(key in games),
                })
    _home_rows = rows
    return _home_rows
