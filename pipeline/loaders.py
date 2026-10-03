"""Load and clean each raw CSV.

Each loader returns a frame with a shared coordinate schema (``latitude``,
``longitude``) plus the columns the aggregator needs. Invalid coordinates are
dropped here so later joins can assume every row is mappable.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

logger = logging.getLogger("pipeline")

# Wide enough for LA County, including the port, the Valley, and Catalina.
LAT_MIN, LAT_MAX = 33.2, 34.9
LON_MIN, LON_MAX = -119.0, -117.4

LAPD_AGENCY_NAMES = {"los angeles police department", "lapd"}
# CHP and federal offices are not local venue jurisdiction.
LOCAL_STATION_TYPES = {"municipal_police", "sheriff", "law_enforcement"}


@dataclass
class LoadReport:
    name: str
    rows_in: int
    rows_out: int
    dropped_invalid_coords: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def dropped(self) -> int:
        return self.rows_in - self.rows_out


def _read_table(path: Path, **kwargs) -> pd.DataFrame:
    try:
        return pd.read_csv(path, encoding="utf-8", **kwargs)
    except UnicodeDecodeError:
        return pd.read_csv(path, encoding="latin-1", **kwargs)


def _coerce_lat_lon(df: pd.DataFrame, lat_col: str, lon_col: str) -> pd.DataFrame:
    out = df.copy()
    out[lat_col] = pd.to_numeric(out[lat_col], errors="coerce")
    out[lon_col] = pd.to_numeric(out[lon_col], errors="coerce")
    return out


def _valid_coord_mask(lat: pd.Series, lon: pd.Series) -> pd.Series:
    return lat.between(LAT_MIN, LAT_MAX) & lon.between(LON_MIN, LON_MAX)


def _drop_invalid_coordinates(
    df: pd.DataFrame,
    lat_col: str,
    lon_col: str,
) -> tuple[pd.DataFrame, int]:
    """Keep rows inside the LA County box. Drops nulls and 0,0 placeholders."""
    cleaned = _coerce_lat_lon(df, lat_col, lon_col)
    mask = _valid_coord_mask(cleaned[lat_col], cleaned[lon_col])
    dropped = int((~mask).sum())
    return cleaned.loc[mask].copy(), dropped


def _repair_venue_row(row: list[str], width: int) -> list[str] | None:
    """Stitch a venue row whose verification_status contains an unquoted comma."""
    if len(row) == width:
        return row
    if len(row) != width + 1:
        return None
    status = f"{row[10].strip().strip(chr(34))}, {row[11].strip().strip(chr(34))}"
    return row[:10] + [status.strip()] + row[12:]


def load_venues(path: Path) -> tuple[pd.DataFrame, LoadReport]:
    """Anchor venues. Repairs known broken quoting, then validates coordinates.

    Uses ``latitude``/``longitude``. If those are missing or outside LA County,
    falls back to ``geocoded_lat``/``geocoded_lon`` when that pair is valid.
    """
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        raw_rows: list[list[str]] = []
        repaired = 0
        skipped = 0
        for line_no, row in enumerate(reader, start=2):
            if not any(cell.strip() for cell in row):
                continue
            fixed = _repair_venue_row(row, len(header))
            if fixed is None:
                skipped += 1
                logger.warning(
                    "venues: skipped line %s (%s fields, expected %s)",
                    line_no,
                    len(row),
                    len(header),
                )
                continue
            if len(row) != len(header):
                repaired += 1
            raw_rows.append(fixed)

    report = LoadReport(name="venues", rows_in=len(raw_rows) + skipped, rows_out=0)
    if skipped:
        report.notes.append(f"skipped {skipped} unparseable rows")
    if repaired:
        report.notes.append(
            f"repaired {repaired} rows with an unquoted comma in verification_status"
        )

    frame = pd.DataFrame(raw_rows, columns=header)
    frame = _coerce_lat_lon(frame, "latitude", "longitude")
    if "geocoded_lat" in frame.columns:
        frame = _coerce_lat_lon(frame, "geocoded_lat", "geocoded_lon")

    primary_ok = _valid_coord_mask(frame["latitude"], frame["longitude"])
    used_fallback = 0
    if "geocoded_lat" in frame.columns:
        fallback_ok = _valid_coord_mask(frame["geocoded_lat"], frame["geocoded_lon"])
        take_fallback = ~primary_ok & fallback_ok
        used_fallback = int(take_fallback.sum())
        frame.loc[take_fallback, "latitude"] = frame.loc[take_fallback, "geocoded_lat"]
        frame.loc[take_fallback, "longitude"] = frame.loc[take_fallback, "geocoded_lon"]
        if used_fallback:
            ids = ", ".join(frame.loc[take_fallback, "venue_id"].astype(str))
            report.notes.append(f"used geocoded coordinates for {ids}")

    kept = _valid_coord_mask(frame["latitude"], frame["longitude"])
    dropped = int((~kept).sum())
    if dropped:
        bad_ids = ", ".join(frame.loc[~kept, "venue_id"].astype(str))
        report.notes.append(f"dropped venues with no usable coordinates: {bad_ids}")
    frame = frame.loc[kept].copy()
    report.dropped_invalid_coords = dropped
    report.rows_out = len(frame)

    keep_cols = [
        "venue_id",
        "venue_name",
        "address",
        "city",
        "state",
        "zip",
        "olympic_zone",
        "sports",
        "latitude",
        "longitude",
        "verification_status",
        "geocode_result",
    ]
    present = [col for col in keep_cols if col in frame.columns]
    return frame[present].reset_index(drop=True), report


def load_crime(path: Path) -> tuple[pd.DataFrame, LoadReport]:
    """LAPD incident file. Keeps location, category, and date only.

    Victim demographics and premise details stay out of the pipeline so the
    map outputs are density layers, not person-level records.
    """
    columns = ["DR_NO", "DATE OCC", "Crm Cd Desc", "LAT", "LON"]
    raw = _read_table(
        path,
        usecols=columns,
        dtype={"DR_NO": "string", "DATE OCC": "string", "Crm Cd Desc": "string"},
        low_memory=False,
    )
    report = LoadReport(name="crime", rows_in=len(raw), rows_out=0)
    cleaned, dropped = _drop_invalid_coordinates(raw, "LAT", "LON")
    report.dropped_invalid_coords = dropped

    occurred = pd.to_datetime(
        cleaned["DATE OCC"],
        format="%m/%d/%Y %I:%M:%S %p",
        errors="coerce",
    )
    still_missing = occurred.isna() & cleaned["DATE OCC"].notna()
    still_missing &= cleaned["DATE OCC"].str.strip().ne("")
    if still_missing.any():
        occurred.loc[still_missing] = pd.to_datetime(
            cleaned.loc[still_missing, "DATE OCC"],
            errors="coerce",
        )
    undated = int(occurred.isna().sum())
    if undated:
        report.notes.append(
            f"{undated:,} incidents have no parseable occurred date "
            "(kept for counts, omitted from crime_by_month)"
        )

    out = pd.DataFrame(
        {
            "incident_id": cleaned["DR_NO"].astype("string"),
            "category": cleaned["Crm Cd Desc"].fillna("Unknown").str.strip(),
            "occurred_at": occurred,
            "latitude": cleaned["LAT"],
            "longitude": cleaned["LON"],
        }
    )
    out["category"] = out["category"].replace("", "Unknown")
    report.rows_out = len(out)
    return out.reset_index(drop=True), report


def load_rail_stations(path: Path) -> tuple[pd.DataFrame, LoadReport]:
    raw = _read_table(path)
    report = LoadReport(name="rail_stations", rows_in=len(raw), rows_out=0)
    cleaned, dropped = _drop_invalid_coordinates(raw, "latitude", "longitude")
    report.dropped_invalid_coords = dropped
    out = cleaned[["station_id", "station_name", "lines", "latitude", "longitude"]].copy()
    out["lines"] = out["lines"].fillna("").astype(str)
    report.rows_out = len(out)
    return out.reset_index(drop=True), report


def load_bus_stops(path: Path) -> tuple[pd.DataFrame, LoadReport]:
    raw = _read_table(path, dtype={"bus_line": "string", "stop_id": "string"})
    report = LoadReport(name="bus_stops", rows_in=len(raw), rows_out=0)
    cleaned, dropped = _drop_invalid_coordinates(raw, "latitude", "longitude")
    report.dropped_invalid_coords = dropped
    out = cleaned[["stop_id", "bus_line", "latitude", "longitude"]].copy()
    out["bus_line"] = out["bus_line"].fillna("").astype(str)
    report.rows_out = len(out)
    return out.reset_index(drop=True), report


def load_fire_stations(path: Path) -> tuple[pd.DataFrame, LoadReport]:
    raw = _read_table(path)
    report = LoadReport(name="fire_stations", rows_in=len(raw), rows_out=0)
    if "department" in raw.columns:
        is_fire = raw["department"].astype(str).str.strip().str.upper().eq("FIRE")
        excluded = int((~is_fire).sum())
        if excluded:
            report.notes.append(f"excluded {excluded} non-FIRE rows")
        raw = raw.loc[is_fire].copy()
    cleaned, dropped = _drop_invalid_coordinates(raw, "latitude", "longitude")
    report.dropped_invalid_coords = dropped
    out = cleaned[["station_id", "station_name", "latitude", "longitude"]].copy()
    report.rows_out = len(out)
    return out.reset_index(drop=True), report


def load_hospitals(path: Path) -> tuple[pd.DataFrame, LoadReport]:
    """Load LA County hospitals and normalize them to venue join fields."""
    raw = _read_table(path)
    report = LoadReport(name="hospitals", rows_in=len(raw), rows_out=0)
    if "COUNTY_NAME" in raw.columns:
        in_county = raw["COUNTY_NAME"].astype(str).str.strip().str.upper().eq("LOS ANGELES")
        excluded = int((~in_county).sum())
        if excluded:
            report.notes.append(f"excluded {excluded} hospitals outside Los Angeles County")
        raw = raw.loc[in_county].copy()
    cleaned, dropped = _drop_invalid_coordinates(raw, "LATITUDE", "LONGITUDE")
    report.dropped_invalid_coords = dropped
    out = cleaned.rename(
        columns={
            "FACID": "station_id",
            "FACNAME": "station_name",
            "ADDRESS": "address",
            "City": "city",
            "ZIP Code": "zip_code",
            "Emergency Room?": "emergency_room",
            "FAC_TYPE_CODE": "hospital_type",
            "CAPACITY": "bed_capacity",
            "LICENSE_STATUS_DESCRIPTION": "license_status",
            "LATITUDE": "latitude",
            "LONGITUDE": "longitude",
        }
    )
    keep_cols = [
        "station_id",
        "station_name",
        "address",
        "city",
        "zip_code",
        "latitude",
        "longitude",
        "emergency_room",
        "hospital_type",
        "bed_capacity",
        "license_status",
    ]
    out = out[[column for column in keep_cols if column in out.columns]].copy()
    report.rows_out = len(out)
    return out.reset_index(drop=True), report


def load_police_stations(path: Path) -> tuple[pd.DataFrame, LoadReport]:
    """Local municipal and sheriff stations used for the jurisdiction flag.

    California Highway Patrol and federal offices are dropped from this frame.
    They do not answer "LAPD or LASD" for a venue.
    """
    raw = _read_table(path)
    report = LoadReport(name="police_stations", rows_in=len(raw), rows_out=0)
    if "station_type" in raw.columns:
        local = raw["station_type"].astype(str).str.strip().str.lower().isin(LOCAL_STATION_TYPES)
        excluded = int((~local).sum())
        report.notes.append(
            f"excluded {excluded} state/federal stations from jurisdiction matching"
        )
        raw = raw.loc[local].copy()
    cleaned, dropped = _drop_invalid_coordinates(raw, "latitude", "longitude")
    report.dropped_invalid_coords = dropped
    out = cleaned[
        ["station_id", "station_name", "agency", "station_type", "latitude", "longitude"]
    ].copy()
    out["agency"] = out["agency"].fillna("").astype(str).str.strip()
    report.rows_out = len(out)
    return out.reset_index(drop=True), report


def is_lapd_agency(agency: str) -> bool:
    key = " ".join(str(agency).casefold().split())
    return key in LAPD_AGENCY_NAMES
