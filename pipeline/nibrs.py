"""Download LAPD NIBRS offense extracts and refresh them when Socrata updates.

The portal publishes two views:

* ``y8y3-fqfu`` — "LAPD NIBRS Offenses Dataset 2024 to 2025". Updates stopped
  on August 18, 2026. The rows were merged into the current dataset.
* ``k7nn-b2ep`` — "LAPD NIBRS Offenses Dataset", formerly "2026 to Present".
  This is the file that still changes, and it includes 2025 through the present
  plus the earlier NIBRS rows.

Each run reads ``rowsUpdatedAt`` from the Socrata view metadata. A dataset is
downloaded only when that timestamp differs from the last published manifest.
Cloud Scheduler runs that check on Tuesdays at 18:00 America/Los_Angeles. A
new download rebuilds the merged file, the NIBRS charts, the city baseline,
and the daily crime counts, then reloads the hosted site. An unchanged
Tuesday does not.

NIBRS stores one row per offense. A single case can appear more than once.
These extracts are not added to the 2020–2024 incident file.

    python -m pipeline.nibrs
    python -m pipeline.nibrs --force
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = REPO_ROOT / "data" / "raw" / "nibrs"
MANIFEST_NAME = "manifest.json"
PORTAL = "https://data.lacity.org"
PAGE_SIZE = 50_000
# Socrata sometimes ignores a large $limit and returns one of these instead.
KNOWN_CAPS = (1000, 5000, 10000)
USER_AGENT = "safe-games-la-nibrs/1.0"

# Location, offense, and date only. Victim, suspect, and premise fields stay out.
COLUMNS = (
    "caseno",
    "uniquenibrno",
    "date_occ",
    "nibr_code",
    "nibr_description",
    "hndrdth_lat",
    "hndrdth_lon",
)


@dataclass(frozen=True)
class NibrsDataset:
    view_id: str
    label: str
    filename: str


DATASETS = (
    NibrsDataset(
        "y8y3-fqfu",
        "LAPD NIBRS Offenses Dataset 2024 to 2025",
        "nibrs_2024_2025.csv",
    ),
    NibrsDataset(
        "k7nn-b2ep",
        "LAPD NIBRS Offenses Dataset",
        "nibrs_current.csv",
    ),
)


def needs_download(local_updated_at: int | None, remote_updated_at: int, force: bool) -> bool:
    if force or local_updated_at is None:
        return True
    return int(local_updated_at) != int(remote_updated_at)


def _headers() -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    token = os.environ.get("SOCRATA_APP_TOKEN", "").strip()
    if token:
        headers["X-App-Token"] = token
    return headers


def _get(url: str, timeout: int) -> bytes:
    request = urllib.request.Request(url, headers=_headers())
    last_error: Exception | None = None
    for _ in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code < 500 and exc.code != 429:
                raise
        except urllib.error.URLError as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def fetch_metadata(view_id: str) -> dict:
    payload = json.loads(_get(f"{PORTAL}/api/views/{view_id}.json", timeout=60))
    updated = payload.get("rowsUpdatedAt")
    if updated is None:
        raise ValueError(f"{view_id} metadata has no rowsUpdatedAt")
    return {
        "view_id": view_id,
        "name": payload.get("name") or view_id,
        "rows_updated_at": int(updated),
    }


def fetch_csv_page(view_id: str, offset: int, limit: int) -> str:
    query = urllib.parse.urlencode(
        {
            "$select": ",".join(COLUMNS),
            "$order": ":id",
            "$limit": limit,
            "$offset": offset,
        }
    )
    raw = _get(f"{PORTAL}/resource/{view_id}.csv?{query}", timeout=180)
    return raw.decode("utf-8-sig")


def download_dataset(dataset: NibrsDataset, dest: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_suffix(".csv.partial")
    offset = 0
    written = 0
    cap: int | None = None
    try:
        with temporary.open("w", newline="", encoding="utf-8") as handle:
            writer: csv.writer | None = None
            indexes: list[int] | None = None
            while True:
                page = fetch_csv_page(dataset.view_id, offset, cap or PAGE_SIZE)
                reader = csv.reader(io.StringIO(page))
                page_header = next(reader, None)
                if page_header is None:
                    break
                if writer is None:
                    missing = [column for column in COLUMNS if column not in page_header]
                    if missing:
                        raise ValueError(f"{dataset.view_id} is missing columns: {', '.join(missing)}")
                    writer = csv.writer(handle, lineterminator="\n")
                    writer.writerow(list(COLUMNS))
                    indexes = [page_header.index(column) for column in COLUMNS]
                assert indexes is not None and writer is not None
                count = 0
                for row in reader:
                    if not any(cell.strip() for cell in row):
                        continue
                    writer.writerow([row[index] if index < len(row) else "" for index in indexes])
                    count += 1
                if count == 0:
                    break
                written += count
                print(f"  {dataset.view_id}: {written:,} rows", flush=True)
                requested = cap or PAGE_SIZE
                if count < requested:
                    if cap is None and count in KNOWN_CAPS:
                        cap = count
                    else:
                        break
                offset += count
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    if written == 0:
        temporary.unlink(missing_ok=True)
        raise ValueError(f"{dataset.view_id} returned no rows")
    temporary.replace(dest)
    return written


def _iso(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).replace(microsecond=0).isoformat()


def _load_manifest(path: Path) -> dict:
    if not path.exists():
        return {"datasets": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_manifest(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def sync_dataset(
    dataset: NibrsDataset,
    directory: Path,
    manifest: dict,
    *,
    force: bool,
    fetch_meta=fetch_metadata,
    download=download_dataset,
) -> dict:
    previous = manifest.setdefault("datasets", {}).get(dataset.view_id, {})
    try:
        remote = fetch_meta(dataset.view_id)
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        record = {
            "label": dataset.label,
            "filename": dataset.filename,
            "status": "unavailable",
            "error": str(exc),
            "rows_updated_at": previous.get("rows_updated_at"),
        }
        manifest["datasets"][dataset.view_id] = record
        print(f"{dataset.label}: unavailable ({exc})", flush=True)
        return record

    remote_updated = int(remote["rows_updated_at"])
    local_updated = previous.get("rows_updated_at")
    if not needs_download(local_updated, remote_updated, force):
        record = dict(previous)
        record.update({
            "label": remote["name"],
            "filename": dataset.filename,
            "status": "unchanged",
            "rows_updated_at": remote_updated,
            "rows_updated_at_iso": _iso(remote_updated),
        })
        manifest["datasets"][dataset.view_id] = record
        print(f"{remote['name']}: unchanged since {_iso(remote_updated)}", flush=True)
        return record

    print(f"{remote['name']}: downloading ({_iso(remote_updated)})", flush=True)
    path = directory / dataset.filename
    rows = download(dataset, path)
    record = {
        "label": remote["name"],
        "filename": dataset.filename,
        "status": "downloaded",
        "rows_updated_at": remote_updated,
        "rows_updated_at_iso": _iso(remote_updated),
        "downloaded_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "rows": rows,
    }
    manifest["datasets"][dataset.view_id] = record
    print(f"{remote['name']}: wrote {rows:,} rows to {path}", flush=True)
    return record


def sync_all(directory: Path = DEFAULT_DIR, *, force: bool = False) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    manifest_path = directory / MANIFEST_NAME
    manifest = _load_manifest(manifest_path)
    manifest["checked_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    for dataset in DATASETS:
        sync_dataset(dataset, directory, manifest, force=force)
        _save_manifest(manifest_path, manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--force", action="store_true", help="Download even when rowsUpdatedAt is unchanged")
    args = parser.parse_args(argv)
    from pipeline.nibrs_cloud import prepare_job, publish_and_roll

    prepare_job()
    manifest = sync_all(args.output_dir, force=args.force)
    rebuilt = False
    if args.output_dir.resolve() == DEFAULT_DIR.resolve():
        from pipeline.refresh import after_sync

        rebuilt = after_sync(manifest)
    publish_and_roll(manifest, rebuilt)
    current = manifest.get("datasets", {}).get("k7nn-b2ep", {})
    if current.get("status") == "unavailable":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
