"""City crime grids for the map, in a second in-memory DuckDB.

This is not the chat warehouse. The chat tables only hold reports inside a
venue circle. The heatmap is every report in Los Angeles, so it scans the
LAPD extract and the NIBRS extract on its own connection, keeps the rounded
cells, and closes the connection. File access is turned off after the scan.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from backend.datasets import REPO_ROOT, DatasetNotFound, load_summary
from pipeline.crime_groups import CRIME_GROUPS
from pipeline.loaders import LAT_MAX, LAT_MIN, LON_MAX, LON_MIN
from pipeline.merge_crime import NIBRS_PATH
from pipeline.run import find_crime_csv, resolve_data_dir

_MONTH_RE = r"^20[0-9]{2}-(0[1-9]|1[0-2])$"
_LOCK = threading.Lock()
_stamp: tuple | None = None
_report: dict | None = None
_nibrs: dict | None = None
_details: dict | None = None


def _crime_csv() -> Path:
    data_dir = resolve_data_dir(REPO_ROOT)
    try:
        return find_crime_csv(data_dir)
    except FileNotFoundError:
        fallback = REPO_ROOT / "data"
        if fallback == data_dir:
            raise
        return find_crime_csv(fallback)


def grid_stamp() -> tuple:
    """File times that change the grid. Missing crime extract raises."""
    crime = _crime_csv()
    nibrs_mtime = NIBRS_PATH.stat().st_mtime if NIBRS_PATH.exists() else 0.0
    summary = REPO_ROOT / "data" / "processed" / "venue_summary.json"
    summary_mtime = summary.stat().st_mtime if summary.exists() else 0.0
    return (crime.stat().st_mtime, nibrs_mtime, summary_mtime)


def _quote_path(path: Path) -> str:
    return str(path).replace("\\", "/").replace("'", "''")


def _group_sql(column: str) -> str:
    """First matching keyword wins. Other stays NULL so type views skip it."""
    clauses = []
    for group_id, _label, needles in CRIME_GROUPS:
        checks = " OR ".join(
            f"contains({column}, '{needle.replace(chr(39), chr(39) * 2)}')"
            for needle in needles
        )
        clauses.append(f"WHEN {checks} THEN '{group_id}'")
    return "CASE " + " ".join(clauses) + " ELSE NULL END"


def _install(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        """
        CREATE MACRO heat_a(lat1, lon1, lat2, lon2) AS (
          least(1.0, greatest(0.0,
            pow(sin(radians(lat2 - lat1) / 2), 2)
            + cos(radians(lat1)) * cos(radians(lat2)) * pow(sin(radians(lon2 - lon1) / 2), 2)
          ))
        )
        """
    )
    connection.execute(
        """
        CREATE MACRO heat_meters(lat1, lon1, lat2, lon2) AS (
          6371000.0 * 2 * atan2(
            sqrt(heat_a(lat1, lon1, lat2, lon2)),
            sqrt(1.0 - heat_a(lat1, lon1, lat2, lon2))
          )
        )
        """
    )
    # Half-even, matching pandas Series.round. Exact ties stay on the even digit.
    connection.execute(
        """
        CREATE MACRO heat_round4(x) AS (
          CASE
            WHEN x IS NULL THEN NULL
            WHEN abs((x * 10000.0) - trunc(x * 10000.0)) = 0.5
                 AND mod(CAST(trunc(x * 10000.0) AS HUGEINT), 2) = 0
            THEN trunc(x * 10000.0) / 10000.0
            ELSE round(x * 10000.0) / 10000.0
          END
        )
        """
    )


def _venues_table(connection: duckdb.DuckDBPyConnection, venues: list[dict]) -> None:
    frame = pd.DataFrame(venues, columns=["latitude", "longitude"])
    if frame.empty:
        connection.execute("CREATE TABLE venues (latitude DOUBLE, longitude DOUBLE)")
        return
    connection.register("venue_frame", frame)
    connection.execute(
        "CREATE TABLE venues AS SELECT CAST(latitude AS DOUBLE) AS latitude, CAST(longitude AS DOUBLE) AS longitude FROM venue_frame"
    )


def _near_sql(lat_col: str, lon_col: str, radius_m: float) -> str:
    radius = float(radius_m)
    return f"""
    EXISTS (
      SELECT 1 FROM venues v
      WHERE {lat_col} BETWEEN v.latitude - 0.02 AND v.latitude + 0.02
        AND {lon_col} BETWEEN v.longitude - 0.02 AND v.longitude + 0.02
        AND heat_meters(v.latitude, v.longitude, {lat_col}, {lon_col}) <= {radius:.8f}
    )
    """


def _csv_columns(connection: duckdb.DuckDBPyConnection, quoted: str) -> set[str]:
    described = connection.execute(
        f"SELECT * FROM read_csv('{quoted}', header=true, all_varchar=true) LIMIT 0"
    ).description
    return {column[0] for column in described}


def _text_expr(columns: set[str], name: str) -> str:
    if name not in columns:
        return "''"
    return f"upper(trim(coalesce(\"{name}\", '')))"


def _load_lapd(connection: duckdb.DuckDBPyConnection, path: Path, radius_m: float) -> None:
    quoted = _quote_path(path)
    columns = _csv_columns(connection, quoted)
    group = _group_sql("descr")
    near = _near_sql("lat", "lon", radius_m)
    clock = 'try_cast("TIME OCC" AS INTEGER)' if "TIME OCC" in columns else "NULL"
    premis = _text_expr(columns, "Premis Desc")
    area = _text_expr(columns, "AREA NAME")
    district = f"trim(coalesce(\"Rpt Dist No\", ''))" if "Rpt Dist No" in columns else "''"
    weapon = (
        "coalesce(trim(\"Weapon Desc\"), '') <> ''"
        if "Weapon Desc" in columns
        else "FALSE"
    )
    if "Date Rptd" in columns:
        reported = """
        coalesce(
          try_strptime("Date Rptd", '%m/%d/%Y %I:%M:%S %p'),
          try_strptime("Date Rptd", '%m/%d/%Y'),
          try_cast("Date Rptd" AS TIMESTAMP)
        )
        """
    else:
        reported = "NULL"
    connection.execute(
        f"""
        CREATE TABLE lapd AS
        SELECT
          heat_round4(lat) AS latitude,
          heat_round4(lon) AS longitude,
          crime_type,
          CASE WHEN occurred IS NULL THEN NULL ELSE strftime(occurred, '%Y-%m') END AS month,
          period,
          noon,
          premise_bucket,
          premis,
          detail_kind,
          weapon,
          area_name,
          district,
          lag_days,
          CASE
            WHEN lag_days IS NULL THEN 'undated'
            WHEN lag_days < 0 THEN 'reported_before'
            WHEN lag_days = 0 THEN 'same_day'
            WHEN lag_days <= 7 THEN '1_to_7_days'
            WHEN lag_days <= 30 THEN '8_to_30_days'
            WHEN lag_days <= 90 THEN '31_to_90_days'
            ELSE 'over_90_days'
          END AS lag_bucket,
          {near} AS near
        FROM (
          SELECT
            lat,
            lon,
            occurred,
            {group} AS crime_type,
            CASE
              WHEN clock IS NULL OR clock < 0 OR clock > 2359 OR mod(clock, 100) > 59 THEN NULL
              WHEN clock < 600 THEN 'night'
              WHEN clock < 1200 THEN 'morning'
              WHEN clock < 1800 THEN 'afternoon'
              ELSE 'evening'
            END AS period,
            coalesce(clock = 1200, false) AS noon,
            CASE
              WHEN premis = '' THEN NULL
              WHEN premis = 'STREET' THEN 'street'
              WHEN starts_with(premis, 'PARKING') THEN 'parking'
              WHEN premis LIKE 'MTA BUS%' OR premis LIKE 'BUS STOP%' OR premis LIKE 'MUNICIPAL BUS%'
                OR premis LIKE 'BUS-CHARTER%' OR premis LIKE 'BUS, SCHOOL%'
                OR premis LIKE 'GREYHOUND%' OR premis LIKE 'BUS DEPOT%' THEN 'bus'
              WHEN premis LIKE 'LA UNION STATION%' OR premis LIKE 'MTA - %LINE%'
                OR premis LIKE 'METROLINK%' OR premis LIKE 'TRAIN TRACKS%'
                OR premis LIKE 'OTHER RR TRAIN%' THEN 'rail'
              WHEN premis LIKE 'MTA PROPERTY%' THEN 'mta_property'
              WHEN premis LIKE 'FIRE STATION%' THEN 'fire_station'
              ELSE NULL
            END AS premise_bucket,
            premis,
            CASE
              WHEN descr LIKE '%ASSAULT WITH DEADLY WEAPON ON POLICE OFFICER%' THEN 'officer_adw'
              WHEN descr LIKE '%BATTERY POLICE%' THEN 'officer_battery'
              WHEN descr LIKE '%BUNCO%' THEN 'bunco'
              WHEN descr LIKE '%PICKPOCKET%' THEN 'pickpocket'
              ELSE NULL
            END AS detail_kind,
            weapon,
            nullif(area_name, '') AS area_name,
            nullif(district, '') AS district,
            date_diff('day', CAST(occurred AS DATE), CAST(reported AS DATE)) AS lag_days
          FROM (
            SELECT
              try_cast(LAT AS DOUBLE) AS lat,
              try_cast(LON AS DOUBLE) AS lon,
              upper(coalesce("Crm Cd Desc", '')) AS descr,
              try_strptime("DATE OCC", '%m/%d/%Y %I:%M:%S %p') AS occurred,
              {clock} AS clock,
              {premis} AS premis,
              {weapon} AS weapon,
              {area} AS area_name,
              {district} AS district,
              {reported} AS reported
            FROM read_csv('{quoted}', header=true, all_varchar=true)
          )
          WHERE lat BETWEEN {LAT_MIN} AND {LAT_MAX}
            AND lon BETWEEN {LON_MIN} AND {LON_MAX}
            AND (occurred IS NULL OR occurred < TIMESTAMP '2024-03-07')
        )
        """
    )


def _load_nibrs(connection: duckdb.DuckDBPyConnection, path: Path, radius_m: float) -> None:
    quoted = _quote_path(path)
    # Keyword groups, same as the map. NIBRS codes are not used here.
    described = (
        "upper(CASE WHEN trim(coalesce(nibr_description, '')) = '' "
        "THEN 'Unknown' ELSE trim(nibr_description) END)"
    )
    group = _group_sql(described)
    connection.execute(
        f"""
        CREATE TABLE nibrs_raw AS
        SELECT
          heat_round4(lat) AS latitude,
          heat_round4(lon) AS longitude,
          {group} AS crime_type,
          strftime(occurred, '%Y-%m') AS month,
          CASE
            WHEN hour(occurred) < 6 THEN 'night'
            WHEN hour(occurred) < 12 THEN 'morning'
            WHEN hour(occurred) < 18 THEN 'afternoon'
            ELSE 'evening'
          END AS period,
          hour(occurred) = 0 AND minute(occurred) = 0 AS midnight,
          CASE
            WHEN regexp_extract(descr, '([0-9]{{2,3}}[A-Z]?)\\s*$', 1) IN ('35A', '35B')
            THEN regexp_extract(descr, '([0-9]{{2,3}}[A-Z]?)\\s*$', 1)
            ELSE NULL
          END AS drug_code
        FROM (
          SELECT
            try_cast(hndrdth_lat AS DOUBLE) AS lat,
            try_cast(hndrdth_lon AS DOUBLE) AS lon,
            try_cast(date_occ AS TIMESTAMP) AS occurred,
            upper(trim(coalesce(nibr_description, ''))) AS descr,
            nibr_description
          FROM read_csv('{quoted}', header=true, all_varchar=true)
        )
        WHERE lat BETWEEN {LAT_MIN} AND {LAT_MAX}
          AND lon BETWEEN {LON_MIN} AND {LON_MAX}
          AND occurred IS NOT NULL
          AND occurred >= TIMESTAMP '2024-03-07'
        """
    )
    radius = float(radius_m)
    connection.execute(
        f"""
        CREATE TABLE nibrs AS
        SELECT
          n.latitude,
          n.longitude,
          n.crime_type,
          n.month,
          n.period,
          n.midnight,
          n.drug_code,
          EXISTS (
            SELECT 1 FROM venues v
            WHERE n.latitude BETWEEN v.latitude - 0.02 AND v.latitude + 0.02
              AND n.longitude BETWEEN v.longitude - 0.02 AND v.longitude + 0.02
              AND heat_meters(v.latitude, v.longitude, n.latitude, n.longitude) <= {radius:.8f}
          ) AS near
        FROM nibrs_raw n
        """
    )
    connection.execute("DROP TABLE nibrs_raw")


def _frame(connection: duckdb.DuckDBPyConnection, sql: str) -> pd.DataFrame:
    return connection.execute(sql).fetchdf()


def _as_series(frame: pd.DataFrame, keys: list[str]) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype="int64")
    return frame.groupby(keys, sort=True)["weight"].sum().astype("int64")


def _type_map(frame: pd.DataFrame, keys: list[str]) -> dict[str, pd.Series]:
    if frame.empty or "crime_type" not in frame.columns:
        return {}
    found: dict[str, pd.Series] = {}
    for type_id, part in frame.groupby("crime_type", sort=True):
        found[str(type_id)] = _as_series(part, keys)
    return found


def _dated(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or "month" not in frame.columns:
        return frame.iloc[0:0]
    mask = frame["month"].astype("string").str.match(_MONTH_RE, na=False)
    return frame.loc[mask]


def _report_from(connection: duckdb.DuckDBPyConnection) -> dict:
    cells = _frame(
        connection,
        """
        SELECT latitude, longitude, count(*)::BIGINT AS weight
        FROM lapd GROUP BY 1, 2
        """,
    )
    venues = _frame(
        connection,
        """
        SELECT latitude, longitude, count(*)::BIGINT AS weight
        FROM lapd WHERE near GROUP BY 1, 2
        """,
    )
    types = _frame(
        connection,
        """
        SELECT crime_type, latitude, longitude, count(*)::BIGINT AS weight
        FROM lapd WHERE crime_type IS NOT NULL GROUP BY 1, 2, 3
        """,
    )
    monthly = _dated(
        _frame(
            connection,
            """
            SELECT month, latitude, longitude, count(*)::BIGINT AS weight
            FROM lapd WHERE month IS NOT NULL GROUP BY 1, 2, 3
            """,
        )
    )
    monthly_venues = _dated(
        _frame(
            connection,
            """
            SELECT month, latitude, longitude, count(*)::BIGINT AS weight
            FROM lapd WHERE near AND month IS NOT NULL GROUP BY 1, 2, 3
            """,
        )
    )
    monthly_types = _dated(
        _frame(
            connection,
            """
            SELECT crime_type, month, latitude, longitude, count(*)::BIGINT AS weight
            FROM lapd WHERE crime_type IS NOT NULL AND month IS NOT NULL
            GROUP BY 1, 2, 3, 4
            """,
        )
    )
    months = sorted(monthly["month"].astype(str).unique()) if not monthly.empty else []
    return {
        "all": _as_series(cells, ["latitude", "longitude"]),
        "venues": _as_series(venues, ["latitude", "longitude"]),
        "types": _type_map(types, ["latitude", "longitude"]),
        "months": months,
        "monthly": {
            "all": _as_series(monthly, ["month", "latitude", "longitude"]),
            "venues": _as_series(monthly_venues, ["month", "latitude", "longitude"]),
            "types": _type_map(monthly_types, ["month", "latitude", "longitude"]),
        },
    }


def _empty_nibrs() -> dict:
    return {
        "months": [],
        "counts": pd.Series(dtype="int64"),
        "venues": pd.Series(dtype="int64"),
        "types": {},
    }


def _nibrs_from(connection: duckdb.DuckDBPyConnection) -> dict:
    tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
    if "nibrs" not in tables:
        return _empty_nibrs()
    counts = _frame(
        connection,
        """
        SELECT month, latitude, longitude, count(*)::BIGINT AS weight
        FROM nibrs GROUP BY 1, 2, 3
        """,
    )
    venues = _frame(
        connection,
        """
        SELECT month, latitude, longitude, count(*)::BIGINT AS weight
        FROM nibrs WHERE near GROUP BY 1, 2, 3
        """,
    )
    types = _frame(
        connection,
        """
        SELECT crime_type, month, latitude, longitude, count(*)::BIGINT AS weight
        FROM nibrs WHERE crime_type IS NOT NULL GROUP BY 1, 2, 3, 4
        """,
    )
    if counts.empty:
        return _empty_nibrs()
    return {
        "months": sorted(counts["month"].astype(str).unique()),
        "counts": _as_series(counts, ["month", "latitude", "longitude"]),
        "venues": _as_series(venues, ["month", "latitude", "longitude"]),
        "types": _type_map(types, ["month", "latitude", "longitude"]),
    }


def _cell_arrays(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if frame.empty:
        return (np.empty(0), np.empty(0), np.empty(0, dtype=np.int64))
    grouped = frame.groupby(["latitude", "longitude"], sort=False)["weight"].sum()
    return (
        grouped.index.get_level_values(0).to_numpy(dtype=np.float64),
        grouped.index.get_level_values(1).to_numpy(dtype=np.float64),
        grouped.to_numpy(dtype=np.int64),
    )


def _grids_by(frame: pd.DataFrame, key: str) -> dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]]:
    if frame.empty or key not in frame.columns:
        return {}
    found = {}
    for value, part in frame.groupby(key, sort=False):
        found[str(value)] = _cell_arrays(part)
    return found


def _named_counts(frame: pd.DataFrame, key: str, limit: int) -> list[dict]:
    if frame.empty or key not in frame.columns:
        return []
    totals = frame.groupby(key, sort=False)["weight"].sum().sort_values(ascending=False).head(limit)
    return [{"name": str(name), "records": int(weight)} for name, weight in totals.items() if str(name)]


def _details_from(connection: duckdb.DuckDBPyConnection) -> dict:
    """Hour, premise, weapon, area, lag, and drug aggregates. Victim fields are not read."""
    hours = _frame(
        connection,
        """
        SELECT period, latitude, longitude, count(*)::BIGINT AS weight
        FROM lapd WHERE period IS NOT NULL
        GROUP BY 1, 2, 3
        """,
    )
    noon = _frame(
        connection,
        """
        SELECT latitude, longitude, count(*)::BIGINT AS weight
        FROM lapd WHERE noon GROUP BY 1, 2
        """,
    )
    premises = _frame(
        connection,
        """
        SELECT premise_bucket, latitude, longitude, count(*)::BIGINT AS weight
        FROM lapd WHERE premise_bucket IS NOT NULL
        GROUP BY 1, 2, 3
        """,
    )
    premise_names = _frame(
        connection,
        """
        SELECT premis AS name, count(*)::BIGINT AS weight
        FROM lapd WHERE premis <> ''
        GROUP BY 1
        """,
    )
    weapon = _frame(
        connection,
        """
        SELECT latitude, longitude, count(*)::BIGINT AS weight
        FROM lapd WHERE weapon GROUP BY 1, 2
        """,
    )
    lapd_cells = _frame(
        connection,
        "SELECT latitude, longitude, count(*)::BIGINT AS weight FROM lapd GROUP BY 1, 2",
    )
    flagged = _frame(
        connection,
        """
        SELECT detail_kind, latitude, longitude, count(*)::BIGINT AS weight
        FROM lapd WHERE detail_kind IS NOT NULL
        GROUP BY 1, 2, 3
        """,
    )
    areas = _frame(
        connection,
        """
        SELECT area_name AS name, count(*)::BIGINT AS weight
        FROM lapd WHERE area_name IS NOT NULL GROUP BY 1
        """,
    )
    districts = _frame(
        connection,
        """
        SELECT district AS name, count(*)::BIGINT AS weight
        FROM lapd WHERE district IS NOT NULL GROUP BY 1
        """,
    )
    lag = _frame(
        connection,
        "SELECT lag_bucket AS name, count(*)::BIGINT AS weight FROM lapd GROUP BY 1",
    )
    median = connection.execute(
        "SELECT median(lag_days) FROM lapd WHERE lag_days >= 0"
    ).fetchone()
    tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
    if "nibrs" in tables:
        nibrs_hours = _frame(
            connection,
            """
            SELECT period, latitude, longitude, count(*)::BIGINT AS weight
            FROM nibrs WHERE period IS NOT NULL
            GROUP BY 1, 2, 3
            """,
        )
        midnight = _frame(
            connection,
            """
            SELECT latitude, longitude, count(*)::BIGINT AS weight
            FROM nibrs WHERE midnight GROUP BY 1, 2
            """,
        )
        drugs = _frame(
            connection,
            """
            SELECT drug_code, period, latitude, longitude, count(*)::BIGINT AS weight
            FROM nibrs WHERE drug_code IS NOT NULL
            GROUP BY 1, 2, 3, 4
            """,
        )
        nibrs_cells = _frame(
            connection,
            "SELECT latitude, longitude, count(*)::BIGINT AS weight FROM nibrs GROUP BY 1, 2",
        )
    else:
        nibrs_hours = pd.DataFrame()
        midnight = pd.DataFrame()
        drugs = pd.DataFrame()
        nibrs_cells = pd.DataFrame()
    drug_totals = []
    if not drugs.empty:
        grouped = drugs.groupby("drug_code")["weight"].sum().sort_values(ascending=False)
        drug_totals = [{"name": str(code), "records": int(weight)} for code, weight in grouped.items()]
    return {
        "lapd_hours": _grids_by(hours, "period"),
        "lapd_noon": _cell_arrays(noon),
        "premise": _grids_by(premises, "premise_bucket"),
        "premise_names": _named_counts(premise_names, "name", 12),
        "weapon": _cell_arrays(weapon),
        "lapd_cells": _cell_arrays(lapd_cells),
        "flags": _grids_by(flagged, "detail_kind"),
        "areas": _named_counts(areas, "name", 8),
        "districts": _named_counts(districts, "name", 8),
        "lag": _named_counts(lag, "name", 10),
        "lag_median_days": None if median is None or median[0] is None else round(float(median[0]), 1),
        "nibrs_hours": _grids_by(nibrs_hours, "period"),
        "nibrs_midnight": _cell_arrays(midnight),
        "drugs": _grids_by(drugs, "drug_code"),
        "drug_hours": _grids_by(drugs, "period"),
        "drug_totals": drug_totals,
        "nibrs_cells": _cell_arrays(nibrs_cells),
    }


def build_grids(
    lapd_csv: Path,
    nibrs_csv: Path | None,
    venues: list[dict],
    radius_m: float,
) -> tuple[dict, dict, dict]:
    """Scan both extracts and return the LAPD grids, NIBRS month grids, and detail slices."""
    connection = duckdb.connect()
    try:
        _install(connection)
        _venues_table(connection, venues)
        _load_lapd(connection, lapd_csv, radius_m)
        if nibrs_csv is not None and nibrs_csv.exists():
            _load_nibrs(connection, nibrs_csv, radius_m)
        connection.execute("SET enable_external_access = false")
        connection.execute("SET lock_configuration = true")
        return _report_from(connection), _nibrs_from(connection), _details_from(connection)
    finally:
        connection.close()


def _origins() -> tuple[list[dict], float]:
    try:
        summary = load_summary()
    except DatasetNotFound:
        return [], 800.0
    radius = float(summary.get("buffer_radius_m") or 800.0)
    venues = []
    for venue in summary.get("venues") or []:
        if venue.get("latitude") is None or venue.get("longitude") is None:
            continue
        venues.append(
            {"latitude": float(venue["latitude"]), "longitude": float(venue["longitude"])}
        )
    return venues, radius


def ensure() -> None:
    """Build the grids when the source files have changed."""
    global _stamp, _report, _nibrs, _details
    stamp = grid_stamp()
    with _LOCK:
        if _stamp == stamp and _report is not None and _nibrs is not None and _details is not None:
            return
        started = time.perf_counter()
        venues, radius = _origins()
        nibrs = NIBRS_PATH if NIBRS_PATH.exists() else None
        report, nibrs_grids, details = build_grids(_crime_csv(), nibrs, venues, radius)
        _report = report
        _nibrs = nibrs_grids
        _details = details
        _stamp = stamp
        elapsed = time.perf_counter() - started
        print(f"heatmap warehouse: built in {elapsed:.1f}s", flush=True)


def report_bundle() -> dict:
    ensure()
    return _report


def nibrs_bundle() -> dict:
    ensure()
    return _nibrs


def details_bundle() -> dict:
    ensure()
    return _details


def warm() -> None:
    """Start the scan without blocking the server from accepting requests."""

    def _run() -> None:
        try:
            ensure()
        except Exception as exc:
            print(f"heatmap warehouse skip: {exc}", flush=True)

    threading.Thread(target=_run, name="heatmap-warehouse", daemon=True).start()


def reset() -> None:
    """Drop the cached grids. Tests use build_grids directly."""
    global _stamp, _report, _nibrs, _details
    with _LOCK:
        _stamp = None
        _report = None
        _nibrs = None
        _details = None
