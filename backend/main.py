"""Safe Games LA API.

Run from the repo root:

    python -m uvicorn backend.main:app --reload
"""

from __future__ import annotations

from pathlib import Path
import os

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.store import (
    DatasetNotFound,
    get_crime_points,
    get_venue,
    list_venues,
    map_layers,
    map_payload,
    meta,
)

ROOT_DIR = Path(__file__).resolve().parents[1]
FRONTEND_DIR = ROOT_DIR / "frontend"


def _load_env_file() -> None:
    """Read repo-root .env. A variable already set in the process wins."""
    path = ROOT_DIR / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file()

app = FastAPI(
    title="Safe Games LA",
    version="0.1.0",
    summary="Venue safety briefings for LA28 anchor sites.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.middleware("http")
async def disable_static_cache(request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-cache"
    return response


def _missing(exc: DatasetNotFound) -> HTTPException:
    return HTTPException(status_code=503, detail=str(exc))


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/api/config")
def config() -> dict:
    """Public browser configuration; the Maps key must be referrer-restricted."""
    return {"google_maps_api_key": os.environ.get("GOOGLE_MAPS_API_KEY", "")}


@app.get("/api/meta")
def read_meta() -> dict:
    try:
        return meta()
    except DatasetNotFound as exc:
        raise _missing(exc) from exc


@app.get("/api/map")
def read_map() -> dict:
    """Citywide venue markers. Crime heat is loaded per selected venue."""
    try:
        return map_payload()
    except DatasetNotFound as exc:
        raise _missing(exc) from exc


@app.get("/api/map/layers")
def read_map_layers() -> dict:
    """Fire, hospital, police, rail, and bus points for map toggles."""
    try:
        layers = map_layers()
    except DatasetNotFound as exc:
        raise _missing(exc) from exc
    return {name: {"count": len(points), "points": points} for name, points in layers.items()}


@app.get("/api/venues")
def read_venues() -> dict:
    try:
        venues = list_venues()
    except DatasetNotFound as exc:
        raise _missing(exc) from exc
    return {"venues": venues}


@app.get("/api/venues/{venue_id}")
def read_venue(venue_id: str) -> dict:
    try:
        venue = get_venue(venue_id)
    except DatasetNotFound as exc:
        raise _missing(exc) from exc
    if venue is None:
        raise HTTPException(status_code=404, detail=f"Unknown venue_id '{venue_id}'")
    return venue


@app.get("/api/venues/{venue_id}/crime-points")
def read_crime_points(
    venue_id: str,
    limit: int | None = Query(default=None, ge=0, le=50000),
) -> dict:
    """Incident points for a future heatmap layer.

    The full set is returned unless ``limit`` is set. Overlapping venue
    buffers repeat an incident under each venue, so callers should request
    one venue at a time.
    """
    try:
        block = get_crime_points(venue_id)
    except DatasetNotFound as exc:
        raise _missing(exc) from exc
    if block is None:
        raise HTTPException(status_code=404, detail=f"Unknown venue_id '{venue_id}'")
    points = block["points"]
    if limit is not None:
        points = points[:limit]
    return {
        "venue_id": venue_id,
        "venue_name": block["venue_name"],
        "latitude": block["latitude"],
        "longitude": block["longitude"],
        "point_count": block["point_count"],
        "returned": len(points),
        "points": points,
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount(
    "/static",
    StaticFiles(directory=FRONTEND_DIR),
    name="frontend",
)
