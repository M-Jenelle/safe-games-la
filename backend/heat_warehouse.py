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


def _load_lapd(connection: duckdb.DuckDBPyConnection, path: Path, radius_m: float) -> None:
    quoted = _quote_path(path)
    group = _group_sql("descr")
    near = _near_sql("lat", "lon", radius_m)
    connection.execute(
        f"""
        CREATE TABLE lapd AS
        SELECT
          heat_round4(lat) AS latitude,
          heat_round4(lon) AS longitude,
          crime_type,
          CASE WHEN occurred IS NULL THEN NULL ELSE strftime(occurred, '%Y-%m') END AS month,
          {near} AS near
        FROM (
          SELECT
            lat,
            lon,
            occurred,
            {group} AS crime_type
          FROM (
            SELECT
              try_cast(LAT AS DOUBLE) AS lat,
              try_cast(LON AS DOUBLE) AS lon,
              upper(coalesce("Crm Cd Desc", '')) AS descr,
              try_strptime("DATE OCC", '%m/%d/%Y %I:%M:%S %p') AS occurred
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
          strftime(occurred, '%Y-%m') AS month
        FROM (
          SELECT
            try_cast(hndrdth_lat AS DOUBLE) AS lat,
            try_cast(hndrdth_lon AS DOUBLE) AS lon,
            try_cast(date_occ AS TIMESTAMP) AS occurred,
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


def build_grids(
    lapd_csv: Path,
    nibrs_csv: Path | None,
    venues: list[dict],
    radius_m: float,
) -> tuple[dict, dict]:
    """Scan both extracts and return the LAPD grids and the NIBRS month grids."""
    connection = duckdb.connect()
    try:
        _install(connection)
        _venues_table(connection, venues)
        _load_lapd(connection, lapd_csv, radius_m)
        if nibrs_csv is not None and nibrs_csv.exists():
            _load_nibrs(connection, nibrs_csv, radius_m)
        connection.execute("SET enable_external_access = false")
        connection.execute("SET lock_configuration = true")
        return _report_from(connection), _nibrs_from(connection)
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
    global _stamp, _report, _nibrs
    stamp = grid_stamp()
    with _LOCK:
        if _stamp == stamp and _report is not None and _nibrs is not None:
            return
        started = time.perf_counter()
        venues, radius = _origins()
        nibrs = NIBRS_PATH if NIBRS_PATH.exists() else None
        report, nibrs_grids = build_grids(_crime_csv(), nibrs, venues, radius)
        _report = report
        _nibrs = nibrs_grids
        _stamp = stamp
        elapsed = time.perf_counter() - started
        print(f"heatmap warehouse: built in {elapsed:.1f}s", flush=True)


def report_bundle() -> dict:
    ensure()
    return _report


def nibrs_bundle() -> dict:
    ensure()
    return _nibrs


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
    global _stamp, _report, _nibrs
    with _LOCK:
        _stamp = None
        _report = None
        _nibrs = None
