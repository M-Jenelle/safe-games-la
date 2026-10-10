"""Rebuild the files the site reads after a new NIBRS download.

The Cloud Scheduler job checks the city portal on Tuesday evenings. A download
happens only when that portal timestamp changes. This module then rebuilds the
merged counts, the NIBRS charts, the city baseline, and the daily crime counts
on the weather table. An unchanged check does no rebuild.
"""

from __future__ import annotations

from pathlib import Path

from pipeline.merge_crime import NIBRS_PATH
from pipeline.nibrs import REPO_ROOT

PROCESSED = REPO_ROOT / "data" / "processed"
DERIVED = (
    PROCESSED / "crime_merged.json",
    PROCESSED / "nibrs_charts.json",
    PROCESSED / "city_baseline.json",
)


def present(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def after_sync(
    manifest: dict,
    *,
    rebuild=None,
    outputs: tuple[Path, ...] = DERIVED,
    nibrs_path: Path = NIBRS_PATH,
) -> bool:
    """Rebuild derived files when NIBRS was just downloaded or a file is missing."""
    current = (manifest.get("datasets") or {}).get("k7nn-b2ep") or {}
    missing = [path for path in outputs if not present(path)]
    if current.get("status") != "downloaded" and not missing:
        print("NIBRS derived files: unchanged", flush=True)
        return False
    if not present(nibrs_path):
        print("NIBRS derived files: skipped, the current extract is not on disk", flush=True)
        return False
    print("NIBRS derived files: rebuilding", flush=True)
    (rebuild or rebuild_derived)()
    return True


def refresh_venue_days() -> None:
    """Rewrite daily crime counts from the new extract. Weather itself stays cached."""
    from pipeline.weather import WEATHER_PATH, join_saved_weather

    if not WEATHER_PATH.is_file():
        print("weather join: skipped, weather_daily.csv is not on disk", flush=True)
        return
    join_saved_weather()
    print("weather join: updated daily crime counts", flush=True)


def rebuild_derived() -> None:
    from pipeline.city_baseline import main as baseline_main
    from pipeline.merge_crime import main as merge_main
    from pipeline.nibrs_charts import main as charts_main

    merge_main()
    charts_main()
    try:
        baseline_main()
    except FileNotFoundError as exc:
        print(f"city baseline: skipped ({exc})", flush=True)
    refresh_venue_days()
