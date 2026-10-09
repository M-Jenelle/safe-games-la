"""Build any processed file a fresh checkout is missing, then register the data check.

Existing files are left as they are. Starting the server does not reread the
crime extracts when the outputs are already on disk.

    python -m pipeline.prepare
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pipeline.nibrs import REPO_ROOT
from pipeline.refresh import present

PROCESSED = REPO_ROOT / "data" / "processed"


@dataclass
class Step:
    name: str
    outputs: tuple[Path, ...]
    action: Callable[[], None]
    inputs_ready: Callable[[], bool]
    skip_reason: str


def run_missing(steps: list[Step]) -> list[str]:
    notes = []
    for step in steps:
        if all(present(path) for path in step.outputs):
            notes.append(f"ready  {step.name}")
            print(notes[-1], flush=True)
            continue
        if not step.inputs_ready():
            notes.append(f"skip   {step.name}: {step.skip_reason}")
            print(notes[-1], flush=True)
            continue
        print(f"build  {step.name}", flush=True)
        try:
            step.action()
        except Exception as exc:
            notes.append(f"skip   {step.name}: {exc}")
            print(notes[-1], flush=True)
            continue
        notes.append(f"build  {step.name}")
    return notes


def _crime_csv_ready() -> bool:
    from pipeline.run import find_crime_csv, resolve_data_dir

    try:
        find_crime_csv(resolve_data_dir(REPO_ROOT))
    except FileNotFoundError:
        return False
    return True


def _file_ready(path: Path) -> Callable[[], bool]:
    return lambda: present(path)


def default_steps() -> list[Step]:
    points = PROCESSED / "crime_points_by_venue.json"
    summary = PROCESSED / "venue_summary.json"
    permits = REPO_ROOT / "prediction_data" / "ladbs_tse_permits.csv"
    events = REPO_ROOT / "prediction_data" / "ticketmaster_events.csv"
    nibrs = REPO_ROOT / "data" / "raw" / "nibrs" / "nibrs_current.csv"
    permit_days = PROCESSED / "permit_event_days.csv"

    def buffers() -> None:
        from pipeline.run import main

        if main(["--radius-m", "800"]) != 0:
            raise RuntimeError("venue buffer build failed")

    def time_of_day() -> None:
        from pipeline.crime_time import main

        main()

    def distance() -> None:
        from pipeline.crime_distance import main

        main()

    def permit_days_build() -> None:
        from pipeline.permit_event_days import main

        main()

    def nibrs_sync() -> None:
        from pipeline.nibrs import main

        main([])

    def merged() -> None:
        from pipeline.merge_crime import main

        main()

    def charts() -> None:
        from pipeline.nibrs_charts import main

        main()

    def baseline() -> None:
        from pipeline.city_baseline import main

        main()

    def weather() -> None:
        from pipeline.weather import main

        main()

    def listings() -> None:
        from pipeline.ticketmaster_listings import main

        main()

    return [
        Step("venue buffers", (summary, points), buffers, _crime_csv_ready, "Crime_Data_from_2020_to_2024.csv is not in data/"),
        Step("time of day", (PROCESSED / "crime_time_of_day.json",), time_of_day, _file_ready(points), "crime points are missing"),
        Step("distance", (PROCESSED / "crime_by_distance.json",), distance, _file_ready(summary), "venue summary is missing"),
        Step("permit days", (permit_days, PROCESSED / "permit_event_lift.json"), permit_days_build, lambda: present(permits) and present(points), "permit file or crime points are missing"),
        Step("NIBRS extract", (nibrs,), nibrs_sync, lambda: True, ""),
        Step("merged crime", (PROCESSED / "crime_merged.json",), merged, lambda: present(points) and present(nibrs), "crime points or the NIBRS extract are missing"),
        Step("NIBRS charts", (PROCESSED / "nibrs_charts.json",), charts, _file_ready(nibrs), "the NIBRS extract is missing"),
        Step("city baseline", (PROCESSED / "city_baseline.json",), baseline, lambda: _crime_csv_ready() and present(nibrs), "the 2020–2024 crime file or the NIBRS extract is missing"),
        Step("weather", (PROCESSED / "weather_daily.csv", PROCESSED / "venue_days_with_weather.csv"), weather, lambda: present(summary) and present(permit_days), "venue summary or permit days are missing"),
        Step("Ticketmaster listings", (PROCESSED / "ticketmaster_listings.json",), listings, lambda: present(events) and present(summary), "Ticketmaster export or venue summary is missing"),
    ]


def ensure_schedule() -> str:
    """Register the daily 06:15 check when this machine can host it and it is absent."""
    if os.name != "nt":
        note = "skip   NIBRS schedule: this is not Windows"
        print(note, flush=True)
        return note
    from pipeline.nibrs import install_schedule, task_installed

    if task_installed():
        note = "ready  NIBRS schedule (daily at 06:15)"
        print(note, flush=True)
        return note
    try:
        install_schedule()
    except SystemExit as exc:
        note = f"skip   NIBRS schedule: {exc}"
        print(note, flush=True)
        return note
    return "build  NIBRS schedule"


def prepare() -> list[str]:
    notes = run_missing(default_steps())
    notes.append(ensure_schedule())
    return notes


def main() -> int:
    prepare()
    return 0


if __name__ == "__main__":
    sys.exit(main())
