"""Compare Gemini (ADC) and Claude (API key) on the same questions.

Python still calculates every count. Each model is scored on two things:
the intent it returns, and whether the finished answer has the expected
status, question type, and count. A question the word list already answers
keeps that calculation even when the model disagrees.

    python scripts/compare_models.py

The result file contains questions and answers only. It does not contain credentials.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.chat import answer_question  # noqa: E402
from backend.claude import ClaudeUnavailable, _INTENTS, interpret_question, provider, settings  # noqa: E402
from backend.datasets import load_summary  # noqa: E402
from backend.venues import get_venue  # noqa: E402

# route None means the intent is calculated locally and is not in the model schema.
CASES = [
    {"q": "How many incidents were reported near Dodger Stadium?", "status": "answered", "type": "present_total", "route": "present_total", "venue": "V01"},
    {"q": "Could you share the page count around Dodger Stadium?", "status": "answered", "type": "present_total", "route": "present_total", "venue": "V01"},
    {"q": "Could you check which offence shows up most around Dodger Stadium?", "status": "answered", "type": "top_category", "route": "top_category"},
    {"q": "Which day of the week is busiest near the Coliseum?", "status": "answered", "type": "weekend", "route": "weekend"},
    {"q": "Which crimes rose the most near Peacock Theater?", "status": "answered", "type": "rose", "route": "rose"},
    {"q": "Where is the nearest Metro station to Peacock Theater?", "status": "answered", "type": "rail", "route": "rail"},
    {"q": "Could you point me toward the nearest firehouse for Dodger Stadium?", "status": "answered", "type": "fire", "route": "tool", "tool": "nearest_facility"},
    {"q": "Compare crime near Peacock Theater and Dodger Stadium", "status": "answered", "type": "compare", "route": "compare"},
    {"q": "What crimes does Other entail near Peacock Theater?", "status": "answered", "type": "other_contents", "route": None},
    {"q": "How many crimes are near the Metro station at Peacock Theater?", "status": "answered", "type": "station_crime", "route": None},
    {"q": "How old are theft victims near Dodger Stadium?", "status": "answered", "type": "victim_age", "route": None},
    {"q": "Which venue is safest in 2028?", "status": "unsupported", "type": None, "route": "unsupported"},
]


def _roster() -> list[dict]:
    return [
        {"venue_id": venue["venue_id"], "venue_name": venue["venue_name"]}
        for venue in load_summary()["venues"]
    ]


def _route(question: str, venues: list[dict]) -> dict:
    try:
        found = interpret_question(question, venues, None)
    except ClaudeUnavailable as exc:
        return {"ok": False, "intent": None, "tool": None, "error": type(exc).__name__}
    return {
        "ok": True,
        "intent": found.intent,
        "tool": found.tool,
        "scope_supported": found.scope_supported,
    }


def _finished(question: str) -> dict:
    body = answer_question(question)
    counts = [row.get("count") for row in body.get("results") or [] if "count" in row]
    return {
        "status": body.get("status"),
        "question_type": body.get("question_type"),
        "engine": body.get("engine"),
        "counts": counts,
        "explanation": bool(body.get("explanation")),
        "answer": (body.get("answer") or "").split("\n", 1)[0][:240],
    }


def _route_matches(route: dict, case: dict) -> bool | None:
    expected = case["route"]
    if expected is None or expected not in _INTENTS:
        return None
    if not route.get("ok"):
        return False
    if expected == "rose" and route.get("intent") == "tool" and route.get("tool") == "trend":
        return True
    if expected == "unsupported" and (route.get("intent") == "unsupported" or route.get("scope_supported") is False):
        return True
    if route.get("intent") != expected:
        return False
    if case.get("tool") and route.get("tool") != case["tool"]:
        return False
    return True


def _answer_matches(finished: dict, case: dict) -> bool:
    if finished.get("status") != case["status"]:
        return False
    expected_type = case["type"]
    if expected_type is not None and finished.get("question_type") != expected_type:
        return False
    venue_id = case.get("venue")
    if venue_id and case["type"] == "present_total":
        expected_count = get_venue(venue_id)["present"]["count"]
        return finished.get("counts") == [expected_count]
    return True


def main() -> None:
    venues = _roster()
    if not venues:
        raise SystemExit("Venue summary is missing.")
    rows = []
    totals = {"gemini": 0, "claude": 0}
    possible = {"gemini": 0, "claude": 0}
    for name in ("gemini", "claude"):
        token = provider.set(name)
        try:
            ready = settings()
            for case in CASES:
                route_expected = _route_matches({"ok": True, "intent": case["route"], "tool": case.get("tool"), "scope_supported": True}, case)
                points_possible = 1 + (1 if route_expected is not None else 0)
                if not ready["claude_configured"]:
                    record = {
                        "provider": name,
                        "question": case["q"],
                        "route": {"ok": False, "intent": None, "error": "not configured"},
                        "answer": {"status": "skipped"},
                        "route_match": False if route_expected is not None else None,
                        "answer_match": False,
                        "points": 0,
                    }
                    rows.append(record)
                    possible[name] += points_possible
                    continue
                route = _route(case["q"], venues)
                finished = _finished(case["q"])
                route_match = _route_matches(route, case)
                answer_match = _answer_matches(finished, case)
                points = int(answer_match) + (int(route_match) if route_match is not None else 0)
                totals[name] += points
                possible[name] += points_possible
                rows.append({
                    "provider": name,
                    "model": ready["model"],
                    "question": case["q"],
                    "expected_type": case["type"],
                    "expected_route": case["route"],
                    "route": route,
                    "answer": finished,
                    "route_match": route_match,
                    "answer_match": answer_match,
                    "points": points,
                })
                mark = "ok" if answer_match and route_match is not False else "miss"
                print(f"{name:7} {mark:4} {case['q'][:72]}", flush=True)
        finally:
            provider.reset(token)

    if totals["gemini"] == totals["claude"]:
        winner = "tie"
    else:
        winner = "gemini" if totals["gemini"] > totals["claude"] else "claude"
    summary = {
        "gemini": totals["gemini"],
        "claude": totals["claude"],
        "possible": possible,
        "winner": winner,
    }
    out = ROOT / "scripts" / "model_compare_results.json"
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=2), encoding="utf-8")
    print(f"Gemini {totals['gemini']} / {possible['gemini']}")
    print(f"Claude {totals['claude']} / {possible['claude']}")
    print(f"Better: {winner}")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
