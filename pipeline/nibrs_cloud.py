"""Publish a new NIBRS extract to Cloud Storage and reload the hosted site.

Cloud Scheduler starts a Cloud Run job on Tuesdays at 18:00 America/Los_Angeles.
The city portal does not name a weekday. The two publication stamps still
visible are both Tuesdays: the consolidated load on 2026-09-01, and
``rowsUpdatedAt`` on 2026-09-29 at 19:18 UTC. Six in the evening is after
that afternoon publish. The job still downloads only when the timestamp changes.

An update replaces the extract, the merged counts, the NIBRS charts, the city
baseline, and the daily crime counts joined to weather. The serving revision
then restarts so the in-memory tables are built from those files.
"""

from __future__ import annotations

import json
import os
from urllib.parse import quote

from pipeline.nibrs import DEFAULT_DIR, MANIFEST_NAME, REPO_ROOT

BUCKET_ENV = "NIBRS_BUCKET"
ROLL_ENV = "NIBRS_ROLL_SERVICE"
GENERATION_ENV = "NIBRS_GENERATION"
SCHEDULE_CRON = "0 18 * * 2"
SCHEDULE_ZONE = "America/Los_Angeles"
PROJECT_ENV = "GOOGLE_CLOUD_PROJECT"
REGION_ENV = "GOOGLE_CLOUD_LOCATION"

MANIFEST_OBJECT = "data/raw/nibrs/manifest.json"
REQUIRED = (
    "data/raw/nibrs/nibrs_current.csv",
    "data/processed/crime_merged.json",
    "data/processed/nibrs_charts.json",
    "data/processed/city_baseline.json",
)
OPTIONAL = (
    "data/processed/venue_days_with_weather.csv",
    "data/processed/weather_meta.json",
)
ARTIFACTS = REQUIRED + OPTIONAL + (MANIFEST_OBJECT,)


def bucket_name() -> str:
    return os.environ.get(BUCKET_ENV, "").strip()


def stamp(manifest: dict | None) -> int | None:
    current = ((manifest or {}).get("datasets") or {}).get("k7nn-b2ep") or {}
    value = current.get("rows_updated_at")
    if value is None:
        return None
    return int(value)


def _read_manifest() -> dict:
    path = DEFAULT_DIR / MANIFEST_NAME
    if not path.is_file():
        return {"datasets": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_bytes(relative: str, payload: bytes) -> None:
    path = REPO_ROOT / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_bytes(payload)
    temporary.replace(path)


class CloudStorage:
    """JSON API client. Tests pass their own object with get and put."""

    def __init__(self, session=None) -> None:
        self._session = session

    def session(self):
        if self._session is None:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession

            credentials, _project = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            self._session = AuthorizedSession(credentials)
        return self._session

    def get(self, bucket: str, name: str) -> bytes | None:
        url = (
            "https://storage.googleapis.com/storage/v1/b/"
            f"{quote(bucket, safe='')}/o/{quote(name, safe='')}?alt=media"
        )
        response = self.session().get(url, timeout=600)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.content

    def put(self, bucket: str, name: str, payload: bytes) -> None:
        url = (
            "https://storage.googleapis.com/upload/storage/v1/b/"
            f"{quote(bucket, safe='')}/o?uploadType=media&name={quote(name, safe='')}"
        )
        response = self.session().post(
            url,
            data=payload,
            headers={"Content-Type": "application/octet-stream"},
            timeout=600,
        )
        response.raise_for_status()


def prepare_job(storage: CloudStorage | None = None) -> None:
    """Use the published manifest so a fresh container does not download twice."""
    bucket = bucket_name()
    if not bucket:
        return
    store = storage or CloudStorage()
    payload = store.get(bucket, MANIFEST_OBJECT)
    if not payload:
        print("NIBRS manifest: Cloud Storage has no extract yet", flush=True)
        return
    remote = json.loads(payload.decode("utf-8"))
    remote_stamp = stamp(remote)
    local_stamp = stamp(_read_manifest())
    if remote_stamp is None:
        return
    if local_stamp is not None and local_stamp > remote_stamp:
        return
    _write_bytes(MANIFEST_OBJECT, payload)
    print("NIBRS manifest: using the copy in Cloud Storage", flush=True)


def pull_if_newer(storage: CloudStorage | None = None) -> bool:
    """Replace local artifacts when Cloud Storage has a newer extract."""
    bucket = bucket_name()
    if not bucket:
        return False
    store = storage or CloudStorage()
    payload = store.get(bucket, MANIFEST_OBJECT)
    if not payload:
        print("NIBRS pull: no extract in Cloud Storage yet", flush=True)
        return False
    remote = json.loads(payload.decode("utf-8"))
    remote_stamp = stamp(remote)
    local_stamp = stamp(_read_manifest())
    if remote_stamp is None:
        return False
    if local_stamp is not None and remote_stamp <= local_stamp:
        print("NIBRS pull: local extract is current", flush=True)
        return False
    print("NIBRS pull: downloading the newer extract", flush=True)
    fetched: dict[str, bytes] = {}
    for name in REQUIRED:
        body = store.get(bucket, name)
        if body is None:
            raise FileNotFoundError(f"Cloud Storage is missing {name}")
        fetched[name] = body
    for name in OPTIONAL:
        body = store.get(bucket, name)
        if body is not None:
            fetched[name] = body
    for name, body in fetched.items():
        _write_bytes(name, body)
    _write_bytes(MANIFEST_OBJECT, payload)
    return True


def upload_artifacts(storage: CloudStorage | None = None) -> None:
    bucket = bucket_name()
    if not bucket:
        return
    store = storage or CloudStorage()
    for name in ARTIFACTS:
        path = REPO_ROOT / name
        if not path.is_file():
            print(f"NIBRS publish: skipped missing {name}", flush=True)
            continue
        store.put(bucket, name, path.read_bytes())
        print(f"NIBRS publish: {name}", flush=True)


def _generation(containers: list[dict]) -> str:
    for item in containers[0].get("env") or []:
        if item.get("name") == GENERATION_ENV:
            return str(item.get("value") or "")
    return ""


def _service_url(project: str, region: str, service: str) -> str:
    return (
        "https://run.googleapis.com/v2/projects/"
        f"{quote(project, safe='')}/locations/{quote(region, safe='')}/services/{quote(service, safe='')}"
    )


def roll_service(generation: str, session=None) -> None:
    """Start a new revision so every instance rebuilds its in-memory tables."""
    service = os.environ.get(ROLL_ENV, "").strip()
    project = os.environ.get(PROJECT_ENV, "").strip()
    region = os.environ.get(REGION_ENV, "").strip() or "us-west1"
    if not service or not project:
        return
    if session is None:
        session = CloudStorage().session()
    url = _service_url(project, region, service)
    response = session.get(url, timeout=60)
    response.raise_for_status()
    body = response.json()
    containers = body.get("template", {}).get("containers") or []
    if not containers:
        raise RuntimeError("Cloud Run service has no container")
    if _generation(containers) == str(generation):
        print(f"NIBRS service: already on extract {generation}", flush=True)
        return
    env = [item for item in (containers[0].get("env") or []) if item.get("name") != GENERATION_ENV]
    env.append({"name": GENERATION_ENV, "value": str(generation)})
    containers[0]["env"] = env
    patched = session.patch(
        url,
        params={"updateMask": "template.containers"},
        json={"template": {"containers": containers}},
        timeout=120,
    )
    patched.raise_for_status()
    print(f"NIBRS service: reloading extract {generation}", flush=True)


def publish_and_roll(manifest: dict, rebuilt: bool, storage: CloudStorage | None = None, session=None) -> None:
    """Upload after a real download, then reload the site if it is behind."""
    if not bucket_name():
        return
    published = stamp(manifest) if rebuilt else None
    if rebuilt:
        upload_artifacts(storage)
    elif os.environ.get(ROLL_ENV, "").strip():
        store = storage or CloudStorage()
        payload = store.get(bucket_name(), MANIFEST_OBJECT)
        if payload:
            published = stamp(json.loads(payload.decode("utf-8")))
    if published is None or not os.environ.get(ROLL_ENV, "").strip():
        return
    roll_service(str(published), session=session)
