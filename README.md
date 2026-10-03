# Safe Games LA

## What we're building

A public-safety readiness tool for LA agencies ahead of the 2028 Olympics. It has two core features:

1. **An interactive heatmap** showing historical crime density around each Olympic venue, with context layers (transit access, fire/police coverage).
2. **An AI chatbot analyst** that answers plain-language questions about crime patterns near venues, grounded in real data with citations — not hallucinated answers.

**The pitch, in one line:** instead of a generic crime dashboard, this is a venue-scoped planning tool that helps LAPD and city planners figure out where to focus resources as millions of tourists and Olympic venues activate across the city.


## Where things stand

### ✅ Done
- **Venue dataset finalized** — 14 confirmed Olympic venues inside the City of LA (`la28_venues.csv`), addresses and coordinates geocoded/verified against the official LA28 venues page and the US Census geocoder.
- **Supporting datasets collected**: LA Metro rail stations, LA Metro bus stops, LAFD fire stations, LA County/municipal police stations, and LA County hospitals — all point-based, all cleaned.
- **Crime dataset pulled**: `Crime_Data_from_2020_to_2024.csv` from the LA Open Data Portal (official LAPD source, the dataset our hackathon doc names). 1,002,654 usable rows after dropping ~2,240 with missing or invalid coordinates.
- **Data pipeline built and run** (`pipeline/`):
  - `pipeline/geo.py` — reusable `buffer_zone()` spatial join (bounding box filter, then haversine distance in meters)
  - `pipeline/loaders.py` — cleans and loads each raw dataset
  - `pipeline/aggregate.py` — per-venue rollup
  - Run with: `python -m pipeline.run` (default radius) or `python -m pipeline.run --radius-m 800` to set the buffer radius explicitly
- **Two processed outputs ready for the frontend**, in `data/processed/`:
  - `venue_summary.json` — one row per venue: coordinates, `crime_count_nearby`, `crime_per_km2`, `crime_by_category`, `crime_by_month`, nearby rail stations, bus stop count + unique lines, nearest fire station, nearest police station, `lapd_jurisdiction` flag
  - `crime_points_by_venue.json` (16.7 MB) — individual incidents per venue (lat/long, category, date, incident id) for the heatmap point layer

### ⚠️ Known issues to be aware of
- **`lapd_jurisdiction` flag is a nearest-station estimate, not an official boundary check.** It currently flags LA Zoo, Griffith Observatory, Riviera Country Club, and both Venice Beach venues as "non-LAPD," but this is very likely wrong for the Zoo, Griffith Observatory, and Venice (all are LAPD territory in reality — nearest station distance isn't the same as jurisdiction). Needs a boundary-map cross-check before we present this flag anywhere. Don't hide venues from the map based on this flag yet.
- **Downtown venue buffers overlap.** Several downtown venues (DTLA Arena, LA Convention Center, Coliseum, etc.) have overlapping 800m zones, so the same crime incident can appear under multiple venues. When building the heatmap, filter to one venue at a time rather than plotting all venues' points simultaneously, or incidents will visually double-count.
- **Some venues have zero nearby transit within 800m** — Dodger Stadium (nearest rail is 1.2km), the Zoo, Griffith Observatory, Riviera, the Port, Sepulveda Basin, and both Venice sites have no rail station in range; Griffith Observatory also has no bus stop in range. This is realistic, not a bug — just don't be surprised by empty transit fields for these venues.
- **Two venue rows (Port of LA, Venice Beach) had a malformed CSV row** (unquoted comma) — the loader repairs this automatically, but flagging it in case anyone edits `la28_venues.csv` by hand in the future.
- **4 venues (Sepulveda Basin, Venice Beach, Venice Beach Boardwalk, Port of LA) still have soft or approximate coordinates** — good enough for buffer-zone joins, but not fully geocoder-verified. Not currently blocking anything, but worth knowing if precision ever matters more.
- **Crime data caveats from the source itself**: transcribed from paper reports (may contain inaccuracies), addresses only given to the nearest hundred block for privacy — so our data is realistically block-level, not exact-address level.

### To DO
- **Backend API** — no FastAPI layer yet; frontend and agent will currently need to read the processed JSON files directly, or someone needs to stand up basic endpoints (`/venues`, `/venue/{id}/crime-points`, `/venue/{id}/summary`).
- **Frontend map** — first heatmap version is wired in `frontend/index.html` and `frontend/app.js`. Google Maps provides the basemap; venue markers are colored/sized by crime density; selecting a venue loads its per-venue crime points and displays a deck.gl heatmap inside the analysis radius. Emergency-service and transit overlays remain the next map-layer additions.
- **AI agent / chatbot** — no agent built yet. Should use `venue_summary.json` fields (especially `crime_by_category`, `crime_by_month`) as tool outputs so answers are grounded in real numbers.
- **Traffic data integration** — we have a few manual traffic-count PDFs (Dodger Stadium, Exposition Park, Venice Beach) but haven't joined them in yet. These are area-matched by name, not by coordinates, so they need manual mapping to venues rather than a spatial join.
- **Jurisdiction boundary fix** — cross-check the 4 flagged venues against an actual LAPD division boundary map instead of relying on nearest-station distance.
- **NIBRS "recent activity" layer (optional/stretch)** — decided to explore showing bi-weekly-refreshed NIBRS data as a secondary "most recent activity" indicator per venue, clearly labeled as NOT live/real-time. This needs its own ingestion path since NIBRS uses a different schema than our main 2020–2024 dataset — treat as a stretch goal, not core.
- **Anomaly/baseline comparison (optional/stretch)** — if time allows: compute current-period vs. historical baseline deviation per venue/category using data we already have. Would strengthen both the map and the chatbot's answers.

## Data sources reference

| Data | Source | Notes |
|---|---|---|
| Crime (2020–2024) | LA Open Data Portal, "Crime Data from 2020 to Present" | Legacy/frozen dataset — official source per hackathon doc |
| Olympic venues | LA28 official venues page (la28.org) | Cross-checked and geocoded; verification status per row in `la28_venues.csv` |
| Metro rail/bus | LA Metro GTFS (via LACMTA GitHub) | Static schedule data, not live |
| Fire stations | data.lacity.org | Static |
| Police/Sheriff stations | LA County GIS | Static |
| Hospitals | `hospitals_in_LA.csv` | LA County hospital facility data; normalized by the pipeline |

## Immediate next steps 
1. Stand up minimal backend endpoints serving the two processed JSON files (unblocks frontend + agent to work in parallel)
2. Frontend: extend the Google Maps/deck.gl map with emergency-service and transit overlay toggles
3. Agent: define tool schema against `venue_summary.json` fields, get basic Q&A working end-to-end with real data
4. Once both core features work end-to-end: revisit jurisdiction flag fix, traffic data join, and stretch goals (NIBRS layer, anomaly detection) if time remains

## How to run

From the repo root, with Python 3.10+:

```bash
python -m pip install -r requirements.txt
python -m uvicorn backend.main:app --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

The app serves the processed JSON already in `data/processed/`. If those files are missing, build them first:

```bash
python -m pipeline.run
```

Default buffer is 800 m. Pass `--radius-m` to change it. The API reloads the JSON when those files change on disk.

Click a marker or a venue in the list to zoom in and open its briefing under the map. The selected venue's crime heatmap and analysis radius appear on the map. Close returns to the full venue view. Configure a referrer-restricted Google Maps JavaScript API key before starting the server:

```bash
GOOGLE_MAPS_API_KEY=your_key_here python -m uvicorn backend.main:app --reload
```

Useful endpoints: `/api/health`, `/api/meta`, `/api/map`, `/api/venues`, `/api/venues/{venue_id}`, `/api/venues/{venue_id}/crime-points`.

Run the checks from the repo root:

```bash
python -m unittest tests.test_pipeline tests.test_api
```
