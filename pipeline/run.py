"""Build venue buffer outputs for the Safe Games LA map.

Usage (from the repo root):

    python -m pipeline.run
    python -m pipeline.run --radius-m 800
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from pipeline.aggregate import build_venue_outputs
from pipeline.geo import DEFAULT_BUFFER_RADIUS_M
from pipeline.loaders import (
    LoadReport,
    load_bus_stops,
    load_crime,
    load_fire_stations,
    load_police_stations,
    load_rail_stations,
    load_venues,
)

logger = logging.getLogger("pipeline")

REPO_ROOT = Path(__file__).resolve().parents[1]


def resolve_data_dir(root: Path) -> Path:
    """Prefer data/raw when the venue file lives there, otherwise data/."""
    raw = root / "data" / "raw"
    if (raw / "la28_venues.csv").exists():
        return raw
    return root / "data"


def find_crime_csv(data_dir: Path) -> Path:
    preferred = data_dir / "Crime_Data_from_2020_to_2024.csv"
    if preferred.exists():
        return preferred
    matches = sorted(data_dir.glob("*rime*.csv"))
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise FileNotFoundError(
            f"No crime CSV in {data_dir}. Place the data.lacity.org extract there "
            "(expected Crime_Data_from_2020_to_2024.csv)."
        )
    names = ", ".join(path.name for path in matches)
    raise FileNotFoundError(f"Multiple crime CSVs in {data_dir}: {names}")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def _log_report(report: LoadReport) -> None:
    logger.info(
        "%s: %s rows in, %s rows out, %s dropped for invalid coordinates",
        report.name,
        f"{report.rows_in:,}",
        f"{report.rows_out:,}",
        f"{report.dropped_invalid_coords:,}",
    )
    for note in report.notes:
        logger.info("%s: %s", report.name, note)


def _log_venue_flags(summary: dict, radius_m: float) -> None:
    logger.info("--- venues with no nearby records (check radius or coordinates) ---")
    any_empty = False
    for venue in summary["venues"]:
        empty_flags = [
            flag for flag in venue["data_quality_flags"] if flag.startswith("no_")
        ]
        if not empty_flags:
            continue
        any_empty = True
        logger.warning(
            "%s %s: %s within %.0f m",
            venue["venue_id"],
            venue["venue_name"],
            ", ".join(empty_flags),
            radius_m,
        )
    if not any_empty:
        logger.info("none")

    logger.info("--- other review flags ---")
    any_other = False
    for venue in summary["venues"]:
        other = [flag for flag in venue["data_quality_flags"] if not flag.startswith("no_")]
        if not other:
            continue
        any_other = True
        logger.warning(
            "%s %s: %s",
            venue["venue_id"],
            venue["venue_name"],
            ", ".join(other),
        )
    if not any_other:
        logger.info("none")


def _log_preview(summary: dict) -> None:
    logger.info("--- venue preview ---")
    for venue in summary["venues"]:
        police = venue["nearest_police_station"]
        if police:
            police_text = f"{police['agency']} / {police['station_name']} ({police['distance_m']} m)"
        else:
            police_text = "none"
        logger.info(
            "%s  crimes=%s  rail=%s  bus_stops=%s  bus_lines=%s  lapd=%s  police=%s",
            venue["venue_id"],
            f"{venue['crime_count_nearby']:,}",
            venue["rail_stations_nearby"]["count"],
            venue["bus_stops_nearby"]["count"],
            len(venue["bus_stops_nearby"]["lines"]),
            venue["lapd_jurisdiction"],
            police_text,
        )


def build(
    data_dir: Path,
    output_dir: Path,
    radius_m: float,
) -> tuple[Path, Path]:
    venue_path = data_dir / "la28_venues.csv"
    crime_path = find_crime_csv(data_dir)
    rail_path = data_dir / "la_metro_rail_stations.csv"
    bus_path = data_dir / "la_metro_bus_stops.csv"
    fire_path = data_dir / "lafd_fire_stations.csv"
    police_path = data_dir / "la_county_police_stations.csv"

    venues, venue_report = load_venues(venue_path)
    crime, crime_report = load_crime(crime_path)
    rail, rail_report = load_rail_stations(rail_path)
    bus, bus_report = load_bus_stops(bus_path)
    fire, fire_report = load_fire_stations(fire_path)
    police, police_report = load_police_stations(police_path)

    for report in (
        venue_report,
        crime_report,
        rail_report,
        bus_report,
        fire_report,
        police_report,
    ):
        _log_report(report)

    source_names = {
        "venues": venue_path.name,
        "crime": crime_path.name,
        "rail_stations": rail_path.name,
        "bus_stops": bus_path.name,
        "fire_stations": fire_path.name,
        "police_stations": police_path.name,
    }
    summary, points = build_venue_outputs(
        venues,
        crime,
        rail,
        bus,
        fire,
        police,
        radius_m=radius_m,
        source_names=source_names,
    )
    _log_venue_flags(summary, radius_m)
    _log_preview(summary)

    summary_path = output_dir / "venue_summary.json"
    points_path = output_dir / "crime_points_by_venue.json"
    _write_json(summary_path, summary)
    _write_json(points_path, points)
    logger.info("wrote %s", summary_path)
    logger.info("wrote %s", points_path)
    return summary_path, points_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Directory of raw CSVs. Default: data/raw if present, else data/.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Where to write JSON. Default: <data-dir parent>/processed, or data/processed.",
    )
    parser.add_argument(
        "--radius-m",
        type=float,
        default=DEFAULT_BUFFER_RADIUS_M,
        help=f"Buffer radius in meters (default {DEFAULT_BUFFER_RADIUS_M:.0f}).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args(argv)
    data_dir = args.data_dir or resolve_data_dir(REPO_ROOT)
    if args.output_dir is not None:
        output_dir = args.output_dir
    elif data_dir.name == "raw":
        output_dir = data_dir.parent / "processed"
    else:
        output_dir = data_dir / "processed"

    logger.info("data directory: %s", data_dir)
    logger.info("buffer radius: %.0f m", args.radius_m)
    try:
        build(data_dir, output_dir, args.radius_m)
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
