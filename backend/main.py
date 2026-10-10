"""Safe Games LA API.

Run from the repo root:

    python -m uvicorn backend.main:app --reload
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import json
import os
import sys

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.claude import settings as claude_settings

from backend.store import (
    DatasetNotFound,
    crime_heat_points,
    get_crime_points,
    get_venue,
    list_venues,
    map_layers,
    map_payload,
    meta,
    home_game_comparison,
    permit_comparison,
    weather_comparison,
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
        if key.strip() in {"ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "SWIFTLY_API_KEY", "GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION"}:
            # Claude reads these settings on demand, allowing local key changes.
            continue
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env_file()


def _skip_prepare() -> bool:
    """Tests import the app. They must not build data or register a Windows task."""
    if os.environ.get("SAFE_GAMES_PREPARE") == "0":
        return True
    return any("unittest" in arg for arg in sys.argv)


@asynccontextmanager
async def _lifespan(_app):
    if not _skip_prepare():
        from pipeline.prepare import prepare

        prepare()
        try:
            from backend.warehouse import connect

            connect()
        except Exception as exc:
            print(f"warehouse skip: {exc}")
        try:
            from backend.heat_warehouse import warm

            warm()
        except Exception as exc:
            print(f"heatmap warehouse skip: {exc}")
    yield


app = FastAPI(
    title="Safe Games LA",
    version="0.1.0",
    summary="Venue safety briefings for LA28 anchor sites.",
    lifespan=_lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
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


class ChatV2Turn(BaseModel):
    """One earlier turn. The agent uses the question and the answer it gave."""

    model_config = {"extra": "ignore"}
    user_text: str = Field(default="", max_length=2000)
    answer: str = Field(default="", max_length=2000)
    tool: str | None = Field(default=None, max_length=80)
    arguments: dict | None = None


class ChatV2Request(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    venue_id: str | None = Field(default=None, min_length=1, max_length=80)
    history: list[ChatV2Turn] = Field(default_factory=list, max_length=6)


@app.get("/api/chat/config")
def chat_config() -> dict:
    """Public status only. The API key never leaves the server."""
    return claude_settings()


def _v2_history(request: ChatV2Request) -> list[dict]:
    return [turn.model_dump() for turn in request.history]


def _v2_answer(request: ChatV2Request, narrate: bool = True) -> dict:
    """Tools for the page figures. The model answers anything those tools do not cover."""
    from backend.agent import answer_events

    for event in answer_events(request.message, request.venue_id, _v2_history(request)):
        if event.get("event") == "template":
            payload = dict(event)
            payload.pop("event", None)
            return payload
    return {
        "version": "v2",
        "status": "answered",
        "answer": "I didn't get that one out. Ask it again in a short sentence, and name a venue if it is about just one place.",
        "caveat": "",
        "confidence": {"kind": "none", "text": ""},
        "narration": None,
        "table": None,
        "links": [],
        "choices": [],
        "results": [],
        "tool": "conversation",
        "arguments": {},
        "engine": "v2",
    }


@app.post("/api/chat/v2")
def chat_v2(request: ChatV2Request):
    """The trial bot. The agent answers when Gemini is configured; the pattern path is the fallback."""
    response = _v2_answer(request)
    return JSONResponse(response, status_code=503 if response["status"] == "unavailable" else 200)


@app.post("/api/chat/v2/stream")
def chat_v2_stream(request: ChatV2Request):
    """Status, then answer text as it is written, then the finished payload."""
    def generate():
        from backend.agent import answer_events

        try:
            for event in answer_events(request.message, request.venue_id, _v2_history(request)):
                if str(event.get("event") or "").startswith("_"):
                    continue
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception:
            payload = {
                "event": "template",
                "version": "v2",
                "status": "answered",
                "answer": "I didn't get that one out. Ask it again in a short sentence, and name a venue if it is about just one place.",
                "caveat": "",
                "confidence": {"kind": "none", "text": ""},
                "narration": None,
                "table": None,
                "links": [],
                "choices": [],
                "results": [],
                "tool": "conversation",
                "arguments": {},
                "engine": "v2",
            }
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'event': 'narration', 'narration': None})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


@app.get("/api/map")
def read_map() -> dict:
    """Citywide venue markers. Crime heat is loaded per selected venue."""
    try:
        return map_payload()
    except DatasetNotFound as exc:
        raise _missing(exc) from exc


@app.get("/api/map/crime")
def read_crime_heat(
    view: str = Query(default="all", max_length=40),
    start: str | None = Query(default=None, max_length=7),
    end: str | None = Query(default=None, max_length=7),
) -> dict:
    """One crime heatmap: all, high-amount, around venues, or one type.

    ``start`` and ``end`` are YYYY-MM. The points are limited to that span.
    """
    try:
        return crime_heat_points(view, start=start, end=end)
    except DatasetNotFound as exc:
        raise _missing(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/map/layers")
def read_map_layers() -> dict:
    """Fire, hospital, police, rail, and bus points for map toggles."""
    try:
        layers = map_layers()
    except DatasetNotFound as exc:
        raise _missing(exc) from exc
    return layers


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


@app.get("/api/venues/{venue_id}/permit-comparison")
def read_permit_comparison(
    venue_id: str,
    year: str = Query(default="all", pattern="^(all|20[0-9]{2})$"),
    month: str = Query(default="all", pattern="^(all|[1-9]|1[0-2])$"),
    source: str = Query(default="reports", pattern="^(reports|nibrs)$"),
) -> dict:
    """Permit-day means versus other days in the same months."""
    try:
        body = permit_comparison(venue_id, year=year, month=month, source=source)
    except DatasetNotFound as exc:
        raise _missing(exc) from exc
    if body is None:
        raise HTTPException(status_code=404, detail=f"Unknown venue_id '{venue_id}'")
    return body


@app.get("/api/venues/{venue_id}/weather")
def read_weather(venue_id: str) -> dict:
    """Wet days and hot days versus the other days in the same months."""
    try:
        body = weather_comparison(venue_id)
    except DatasetNotFound as exc:
        raise _missing(exc) from exc
    if body is None:
        raise HTTPException(status_code=404, detail=f"Unknown venue_id '{venue_id}'")
    return body


@app.get("/api/venues/{venue_id}/home-games")
def read_home_games(venue_id: str) -> dict:
    """Dodger Stadium regular-season home games versus other days in those months."""
    try:
        body = home_game_comparison(venue_id)
    except DatasetNotFound as exc:
        raise _missing(exc) from exc
    if body is None:
        raise HTTPException(status_code=404, detail=f"Unknown venue_id '{venue_id}'")
    return body


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
