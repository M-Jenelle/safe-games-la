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
- **FastAPI backend and crime chatbot** — the existing app serves the map, venue briefings, and `POST /api/chat`. The chatbot calculates full-period incident totals, most common categories, and two-venue count comparisons directly from the processed summary. Click **Ask crime data** or open `/?chat=1` for the demo.

### ⚠️ Known issues to be aware of
- **`lapd_jurisdiction` flag is a nearest-station estimate, not an official boundary check.** It currently flags LA Zoo, Griffith Observatory, Riviera Country Club, and both Venice Beach venues as "non-LAPD," but this is very likely wrong for the Zoo, Griffith Observatory, and Venice (all are LAPD territory in reality — nearest station distance isn't the same as jurisdiction). Needs a boundary-map cross-check before we present this flag anywhere. Don't hide venues from the map based on this flag yet.
- **Downtown venue buffers overlap.** Several downtown venues (DTLA Arena, LA Convention Center, Coliseum, etc.) have overlapping 800m zones, so the same crime incident can appear under multiple venues. When building the heatmap, filter to one venue at a time rather than plotting all venues' points simultaneously, or incidents will visually double-count.
- **Some venues have zero nearby transit within 800m** — Dodger Stadium (nearest rail is 1.2km), the Zoo, Griffith Observatory, Riviera, the Port, Sepulveda Basin, and both Venice sites have no rail station in range; Griffith Observatory also has no bus stop in range. This is realistic, not a bug — just don't be surprised by empty transit fields for these venues.
- **Two venue rows (Port of LA, Venice Beach) had a malformed CSV row** (unquoted comma) — the loader repairs this automatically, but flagging it in case anyone edits `la28_venues.csv` by hand in the future.
- **4 venues (Sepulveda Basin, Venice Beach, Venice Beach Boardwalk, Port of LA) still have soft or approximate coordinates** — good enough for buffer-zone joins, but not fully geocoder-verified. Not currently blocking anything, but worth knowing if precision ever matters more.
- **Crime data caveats from the source itself**: transcribed from paper reports (may contain inaccuracies), addresses only given to the nearest hundred block for privacy — so our data is realistically block-level, not exact-address level.

### To DO
- **Frontend map** — first heatmap version is wired in `frontend/index.html` and `frontend/app.js`. Google Maps provides the basemap; venue markers are colored/sized by crime density; selecting a venue loads its per-venue crime points and displays a deck.gl heatmap inside the analysis radius. Emergency-service and transit overlays remain the next map-layer additions.
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
1. Cross-check the jurisdiction flag against an actual LAPD boundary map.
2. Revisit traffic data joins and stretch goals (NIBRS layer, anomaly detection) if time remains.

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

NIBRS offenses are a separate LAPD extract, starting March 7, 2024. The 2024–2025 view (`y8y3-fqfu`) stopped updating on August 18, 2026 and was merged into the current dataset (`k7nn-b2ep`), which still changes and includes 2025 through the present. Download or refresh both views with:

```bash
python -m pipeline.nibrs
```

The command reads each view's Socrata `rowsUpdatedAt` and downloads only when that time changes. `python -m pipeline.nibrs --install-schedule` registers a daily 6:15am check that does the same thing. NIBRS keeps one row per offense, so those files stay in `data/raw/nibrs/` and are not added to the 2020–2024 incident totals. `python -m pipeline.merge_crime` writes a separate `data/processed/crime_merged.json`: reports dated before March 7, 2024, then NIBRS offenses. It does not change the other data files.

Click a marker or a venue in the list to zoom in and open its briefing under the map. The selected venue's crime heatmap and analysis radius appear on the map. Close returns to the full venue view. Configure a referrer-restricted Google Maps JavaScript API key before starting the server:

Put the key in a repo-root `.env` file (`GOOGLE_MAPS_API_KEY=...`). The server reads that file on startup. Restart the server after adding or changing the key.

```bash
python -m uvicorn backend.main:app --reload
```

Useful endpoints: `/api/health`, `/api/meta`, `/api/map`, `/api/venues`, `/api/venues/{venue_id}`, `/api/venues/{venue_id}/crime-points`.

### Prediction data collectors

Put the Ticketmaster key in the repository-root `.env.local` file:

```bash
TICKETMASTER_API_KEY=your_ticketmaster_key_here
```

That file is ignored by Git. Generate venue-scoped prediction data with:

```bash
python scripts/collect_prediction_data.py
```

The collector uses only the venues in `data/la28_venues.csv`. It requests Ticketmaster events from January 2020 through 2028, writes full Discovery v2 event objects and analysis-ready CSV rows to `prediction_data/ticketmaster_events.json` and `prediction_data/ticketmaster_events.csv`, and downloads LADBS permits from January 2020 through the current date. LADBS permits are kept only when they fall within 800 meters of an allow-listed venue in `prediction_data/ladbs_tse_permits.json` and `prediction_data/ladbs_tse_permits.csv`.

Ticketmaster calls are throttled to a conservative 0.6 seconds between requests and capped at 100 calls per run by default. Override the cap only when needed with `--max-ticketmaster-calls`; the script stops before exceeding its own cap and does not retry a `429` response automatically.

Ticketmaster Discovery is not a guaranteed historical archive, so completed events from earlier years may not be returned even when the 2020 start date is requested.

## Crime chatbot demo

Start the same app from the repo root. It works in data mode without an API key; Claude interpretation is optional:

```bash
.venv/bin/python -m uvicorn backend.main:app --reload
```

On a fresh checkout, use Python 3.10+ to create an environment (`python3 -m venv .venv`), then install the requirements with `.venv/bin/python -m pip install -r requirements.txt`.

Open [http://127.0.0.1:8000/?chat=1](http://127.0.0.1:8000/?chat=1). The chatbot works without a Google Maps key. It has three clickable suggested questions drawn from the actual venue roster. You can also try:

- “What is the most common crime category near Dodger Stadium?” → **BATTERY - SIMPLE ASSAULT: 154 reports**.
- “How many incidents were reported near Dodger Stadium?” → **All categories: 914 reported incidents**.
- “Compare the crime counts near Dodger Stadium and Crypto.com Arena.” → separate venue totals and their absolute difference, with an overlap note.
- “How many incidents near Venice?” → asks you to choose **Venice Beach** or **Venice Beach Boardwalk**. Clicking a choice continues the question.

Select a venue in the existing roster to ask about “this venue.” Without a map selection, the last single venue answered in chat provides context. Explicit names override context; ambiguous names always require clarification. After a two-venue comparison, name a venue or select one before using “this venue.”

`POST /api/chat` accepts `{"message": "How many incidents near this venue?", "venue_id": "V01"}`. `venue_id` is optional. Responses include `status`, `answer`, `question_type`, `results` (actual venue/category/count rows), `choices` (clarification questions), `source`, and `engine` (`data`, `claude`, or `fallback`). `GET /api/chat/suggestions` provides the demo questions; `/api/chat/config` reports whether Claude is configured and the selected model, without exposing its key. A missing or mismatched dataset returns HTTP 503 with a sourced explanation; invalid request sizes return HTTP 422.

Every answer labels **LAPD crime reports via the LA Open Data Portal**, the processed source file, **2020–2024**, and the **800 m radius**. The chatbot reads `venue_summary.json` through the existing reload-on-change store. Tests independently check totals and top categories against `crime_points_by_venue.json` for all 14 venues. Venue buffers overlap, so comparisons never sum their counts into a unique citywide total.

This small demo supports full-period incident totals, the most common category (including ties), and comparisons of exactly two venue totals. It explicitly declines category-specific counts, date/time filters, other radii, transit/service questions, causes, live conditions, safety judgments, and 2028 predictions. Venue matching and every number come from code and processed data. Keep the pipeline at `--radius-m 800` for this demo.

### Enable Claude

Add the following settings to the repo-root `.env` file (see `.env.example`):

```dotenv
ANTHROPIC_API_KEY=your_anthropic_api_key
ANTHROPIC_MODEL=claude-haiku-4-5
```

Refresh the chatbot after adding your key. It will show **Claude enabled**. Claude settings are read from `.env` on each request, so adding or changing them needs no server restart. Nonempty process environment variables take precedence over the file. The key stays in the backend and is excluded from Git; there is no key input in the browser. `ANTHROPIC_MODEL` is optional and defaults to [Claude Haiku 4.5](https://platform.claude.com/docs/en/models/overview). Existing `httpx` handles [Anthropic's Messages API](https://platform.claude.com/docs/en/api/overview), so no new package is needed.

Claude receives the question, selected venue ID, and venue names. Its [structured response](https://platform.claude.com/docs/en/build-with-claude/structured-outputs) contains only an intent and a supported-scope flag. Code resolves ambiguous names, rejects explicit unsupported qualifiers, reads the JSON, calculates the answer, and adds provenance. Model-generated counts or extra output fields are rejected. Claude cannot override a recognized local question type. Each request makes at most one API call with an 8-second read timeout and a 256-token output limit; missing keys, API errors, refusals, or invalid output fall back to the local parser, with an interface notice. Natural wording such as “Which offence shows up most around Dodger Stadium?” is supported when Claude is enabled. Answers and numbers are always formatted by code.

The chatbot uses the supplied robot image in the launcher, header, messages, and browser tab. `frontend/chat-icon.jpg` is the original image, framed with CSS in the interface.

Run the checks from the repo root:

```bash
.venv/bin/python -m unittest tests.test_pipeline tests.test_api tests.test_chat tests.test_claude
node --check frontend/app.js
node --check frontend/chat.js
```

The two citywide heatmap tests in `tests.test_api` also need the raw, gitignored `data/Crime_Data_from_2020_to_2024.csv`. If it is absent, those routes return HTTP 503 and those tests fail. The chatbot and per-venue endpoints use the processed JSON. Run chatbot checks alone with `.venv/bin/python -m unittest tests.test_chat tests.test_claude`. Claude tests use a mock HTTP transport and make no external or paid API calls.
