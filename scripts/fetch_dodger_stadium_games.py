#!/usr/bin/env python3
"""Download Dodger Stadium home games from the MLB Stats API.

The 2020–2024 comparison file is left as it is. This writes later seasons
in the same columns, and the venue page reads them as their own row.
A game is kept when the Dodgers are the home team and the venue is
Dodger Stadium or UNIQLO Field at Dodger Stadium.

Example:
  python scripts/fetch_dodger_stadium_games.py --start-year 2025 --end-year 2026
"""

from __future__ import annotations

import argparse
import csv
import json
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPO_ROOT / "data" / "raw" / "mlb" / "dodgers_stadium_games_2025_2026.csv"
API = "https://statsapi.mlb.com/api/v1/schedule"
TEAM_ID = 119
# MLB renamed the ballpark on the 2026 schedule.
VENUE_NAMES = {"Dodger Stadium", "UNIQLO Field at Dodger Stadium"}
COLUMNS = (
    "date",
    "game_pk",
    "home_team",
    "home_team_id",
    "away_team",
    "venue_name",
    "status",
    "detailed_status",
    "game_type",
    "doubleheader",
)


def fetch_season(year: int) -> list[dict]:
    query = urllib.parse.urlencode({
        "sportId": 1,
        "teamId": TEAM_ID,
        "season": year,
        "hydrate": "team,venue",
    })
    request = urllib.request.Request(
        f"{API}?{query}",
        headers={"User-Agent": "safe-games-la-dodger-schedule/1.0"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.load(response)
    rows = []
    for block in payload.get("dates") or []:
        for game in block.get("games") or []:
            home = ((game.get("teams") or {}).get("home") or {}).get("team") or {}
            away = ((game.get("teams") or {}).get("away") or {}).get("team") or {}
            venue = game.get("venue") or {}
            if home.get("id") != TEAM_ID or venue.get("name") not in VENUE_NAMES:
                continue
            status = game.get("status") or {}
            official = str(game.get("officialDate") or game.get("gameDate") or "")[:10]
            rows.append({
                "date": official,
                "game_pk": game.get("gamePk") or "",
                "home_team": home.get("name") or "",
                "home_team_id": home.get("id") or "",
                "away_team": away.get("name") or "",
                "venue_name": venue.get("name") or "",
                "status": status.get("abstractGameState") or "",
                "detailed_status": status.get("detailedState") or "",
                "game_type": game.get("gameType") or "",
                "doubleheader": game.get("doubleHeader") or "",
            })
    rows.sort(key=lambda row: (row["date"], row["game_pk"]))
    return rows


def write_rows(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Download Dodger Stadium home games from the MLB Stats API.")
    parser.add_argument("--start-year", type=int, default=2025)
    parser.add_argument("--end-year", type=int, default=2026)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.end_year < args.start_year:
        raise SystemExit("--end-year must be on or after --start-year")
    rows = []
    for year in range(args.start_year, args.end_year + 1):
        season = fetch_season(year)
        print(f"{year}: {len(season)} Dodger Stadium games")
        rows.extend(season)
    write_rows(args.output, rows)
    if rows:
        print(f"Wrote {len(rows)} games, {rows[0]['date']} through {rows[-1]['date']}, to {args.output}")
    else:
        print(f"Wrote 0 games to {args.output}")


if __name__ == "__main__":
    main()
