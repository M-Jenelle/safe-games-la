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
- **Processed outputs for the 2020–2024 series**, in `data/processed/`:
  - `venue_summary.json` — one row per venue: coordinates, `crime_count_nearby`, `crime_per_km2`, `crime_by_category`, `crime_by_month`, nearby rail stations, bus stop count + unique lines, nearest fire station, nearest police station, `lapd_jurisdiction` flag
  - `crime_points_by_venue.json` — individual incidents per venue (lat/long, category, date, incident id) for the heatmap
  - `crime_time_of_day.json` and `crime_by_distance.json` — time-of-day and distance-band counts inside each 800 m circle
- **Map and venue pages** — Google Maps basemap with a deck.gl heatmap. Venue pins, plus fire, hospital, police, rail, and bus layers. Click a venue for a briefing under the map; Expand opens the venue page. The page script is `frontend/js/main.js` (ES modules under `frontend/js/`). `frontend/app.js` is gone.
- **Three crime series on the venue page.** Headline incidents, density, vs-city, and busiest month use the 2020–present blend: LAPD reports dated before March 7, 2024, then NIBRS offenses from that date forward. The series menu still switches charts among **2020–2024 reports**, **NIBRS offenses, Mar 2024–present**, and **Merged groups, 2020–present**. Those two files are not added together for the overlap months. Charts cover month, category, time of day, distance, and day of week (click a day for its top offense groups).
- **Compare page** — two venues side by side (`#/compare`). Downtown circles that overlap say so: an incident in the overlap is counted for each venue.
- **City baseline** — `data/processed/city_baseline.json`. The city rate is every usable LAPD record divided by the Census 2020 Los Angeles land area (469.49 square miles). Venue circles are not summed into it. The venue page shows that comparison as **Vs city**.
- **Event-day comparisons.** LADBS temporary-event permits are matched to the nearest venue and compared with other days in the same months (`permit_event_days.csv`, `permit_event_lift.json`). Dodger Stadium also has a separate MLB home-game comparison (`dodger_event_risk.json`). Ticketmaster listings appear only for venues the collector matched, with no crime lift attached.
- **FastAPI backend and venue chatbot** — the app serves the map, venue briefings, compare page, and `POST /api/chat`. The chatbot calculates 2020–2024 crime totals, categories, and comparisons, nearby transit, nearest facilities, and listed sports. It also answers the venue page's 2020–present count, density, city comparison, busiest month, top five offense groups, weekend pattern, and which groups rose most from 2020 to 2024. Those three answers include a table and a link to the matching venue-page section. It still declines NIBRS-only counts, permits, home-game lifts, and forecasts. Click **Ask venue data** or open `/?chat=1` for the demo.

### ⚠️ Known issues to be aware of
- **`lapd_jurisdiction` flag is a nearest-station estimate, not an official boundary check.** It currently flags LA Zoo, Griffith Observatory, Riviera Country Club, and both Venice Beach venues as "non-LAPD," but this is very likely wrong for the Zoo, Griffith Observatory, and Venice (all are LAPD territory in reality — nearest station distance isn't the same as jurisdiction). Needs a boundary-map cross-check before we present this flag anywhere. Don't hide venues from the map based on this flag yet.
- **Downtown venue buffers overlap.** Several downtown venues (DTLA Arena, LA Convention Center, Coliseum, etc.) have overlapping 800m zones, so the same crime incident can appear under multiple venues. When building the heatmap, filter to one venue at a time rather than plotting all venues' points simultaneously, or incidents will visually double-count.
- **Some venues have zero nearby transit within 800m** — Dodger Stadium (nearest rail is 1.2km), the Zoo, Griffith Observatory, Riviera, the Port, Sepulveda Basin, and both Venice sites have no rail station in range; Griffith Observatory also has no bus stop in range. This is realistic, not a bug — just don't be surprised by empty transit fields for these venues.
- **Two venue rows (Port of LA, Venice Beach) had a malformed CSV row** (unquoted comma) — the loader repairs this automatically, but flagging it in case anyone edits `la28_venues.csv` by hand in the future.
- **4 venues (Sepulveda Basin, Venice Beach, Venice Beach Boardwalk, Port of LA) still have soft or approximate coordinates** — good enough for buffer-zone joins, but not fully geocoder-verified. Not currently blocking anything, but worth knowing if precision ever matters more.
- **Crime data caveats from the source itself**: transcribed from paper reports (may contain inaccuracies), addresses only given to the nearest hundred block for privacy — so our data is realistically block-level, not exact-address level.
- **Permit-day comparison is not adjusted for day of week.** Permit days cluster on weekends, and crime is higher on weekends, so part of the gap is the weekday mix. 2020 is still inside the 2020–2024 window. Each offense group is tested on its own; those tests are not corrected for looking at many groups.
- **The heatmap is LAPD only.** Torrance and other cities outside LAPD have no points. San Pedro, Wilmington, and the Port still do. The default crime layer is the 2020–2024 extract. NIBRS is a separate crime-view option.

### Still open
- **Traffic** — not joined. The spreadsheet in the repo is Caltrans District 8 (Riverside and San Bernardino), so it does not cover these venues. A District 7 or LADOT source would be required.
- **Jurisdiction boundary fix** — cross-check the flagged venues against an actual LAPD division boundary map instead of relying on nearest-station distance.
- **Weekday-adjusted event comparison, and a real baseline model** — the permit and home-game gaps are Mann–Whitney tests on daily counts. They do not hold day of week fixed, and there is no Poisson or negative-binomial baseline yet. The chatbot still refuses forecast language.

## Data sources reference

| Data | Source | Notes |
|---|---|---|
| Crime (2020–2024) | LA Open Data Portal, "Crime Data from 2020 to Present" | Legacy/frozen dataset — official source per hackathon doc. One row per police report. |
| NIBRS offenses | LA Open Data, view `k7nn-b2ep` | Current LAPD NIBRS extract. One row per offense. Product coverage starts March 7, 2024. The older view `y8y3-fqfu` stopped updating. |
| City land area | U.S. Census Bureau QuickFacts, Los Angeles city, 2020 | 469.49 square miles. Used only for the citywide rate. |
| Olympic venues | LA28 official venues page (la28.org) | Cross-checked and geocoded; verification status per row in `la28_venues.csv` |
| Metro rail/bus | LA Metro GTFS (via LACMTA GitHub) | Static schedule data, not live |
| Fire stations | data.lacity.org | Static |
| Police/Sheriff stations | LA County GIS | Static |
| Hospitals | `hospitals_in_LA.csv` | LA County hospital facility data; normalized by the pipeline |
| Event permits | LADBS temporary special event permits | Matched to the nearest venue within 800 m. Weaker than a published game list. |
| Dodger home games | MLB home-game list, 2020–2024 | Dodger Stadium only. Separate from the permit comparison. |
| Listed events | Ticketmaster Discovery | Calendar only. Not joined to crime counts. |

## Immediate next steps
1. Cross-check the jurisdiction flag against an actual LAPD boundary map.
2. If the event-day gaps need to be more defensible, stratify them by day of week or add day of week (and a 2020 term) to a count model. Say on the page that the per-group tests are uncorrected.
3. Traffic still needs a Los Angeles source. Do not wire in the District 8 spreadsheet.

## How to run

From the repo root, with Python 3.10+:

```bash
python -m pip install -r requirements.txt
python -m uvicorn backend.main:app --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

The app serves the processed JSON already in `data/processed/`. If the 2020–2024 files are missing, build them first:

```bash
python -m pipeline.run
python -m pipeline.crime_time
python -m pipeline.crime_distance
```

Default buffer is 800 m. Pass `--radius-m` to change it on `pipeline.run`. The API reloads JSON when those files change on disk. Python changes need a server restart. Static files under `frontend/` do not.

When the 2024–present extract updates, refresh in this order. The scheduled task only downloads; it does not rebuild the JSON the site reads.

```bash
python -m pipeline.nibrs
python -m pipeline.merge_crime
python -m pipeline.nibrs_charts
python -m pipeline.city_baseline
```

`python -m pipeline.nibrs` reads Socrata `rowsUpdatedAt` for view `k7nn-b2ep` and downloads only when that time changes. `python -m pipeline.nibrs --install-schedule` registers a daily 6:15am download. NIBRS keeps one row per offense in `data/raw/nibrs/`. `merge_crime` writes `crime_merged.json`: reports dated before March 7, 2024, then NIBRS offenses. It does not change the 2020–2024 files. `nibrs_charts` rebuilds the NIBRS time, distance, weekday, and permit-day charts. `city_baseline` rebuilds the city rate, including the 2020–present blend. The default map heatmap stays on the 2020–2024 extract until `pipeline.run` is used again.

Permit days and Ticketmaster listings are separate builds. Run them when those source files change, not as part of a NIBRS refresh. `dodger_event_risk.json` is not rebuilt by these commands.

```bash
python -m pipeline.permit_event_days
python -m pipeline.ticketmaster_listings
```

Click a marker or a venue in the list to zoom in and open its briefing under the map. The selected venue's crime heatmap and analysis radius appear on the map. Close returns to the full venue view. Configure a referrer-restricted Google Maps JavaScript API key before starting the server:

Put the key in a repo-root `.env` file (`GOOGLE_MAPS_API_KEY=...`). The server reads that file on startup. Restart the server after adding or changing the key.

```bash
python -m uvicorn backend.main:app --reload
```

Useful endpoints: `/api/health`, `/api/meta`, `/api/map`, `/api/map/layers`, `/api/map/crime`, `/api/venues`, `/api/venues/{venue_id}`, `/api/venues/{venue_id}/crime-points`, `/api/venues/{venue_id}/permit-comparison`, `/api/venues/{venue_id}/home-games`.

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

## Venue chatbot demo

Start the same app from the repo root. It works in data mode without an API key; Claude interpretation is optional:

```bash
.venv/bin/python -m uvicorn backend.main:app --reload
```

On a fresh checkout, use Python 3.10+ to create an environment (`python3 -m venv .venv`), then install the requirements with `.venv/bin/python -m pip install -r requirements.txt`.

Open [http://127.0.0.1:8000/?chat=1](http://127.0.0.1:8000/?chat=1). The chatbot works without a Google Maps key. It has six clickable suggested questions drawn from the actual venue roster. You can also try:

- “What is the most common crime category near Dodger Stadium?” → **BATTERY - SIMPLE ASSAULT: 154 reports**.
- “How many incidents were reported near Dodger Stadium?” → **All categories: 914 reported incidents**.
- “Compare the crime counts near Dodger Stadium and Crypto.com Arena.” → separate venue totals and their absolute difference, with an overlap note.
- “How many incidents near Venice?” → asks you to choose **Venice Beach** or **Venice Beach Boardwalk**. Clicking a choice continues the question.
- “What transit is nearby Dodger Stadium?” → **0 rail stations; 2 bus stops**, with the recorded bus routes.
- “What rail stations are near LA Convention Center?” → station names, lines, counts, and straight-line distances within **800 m**.
- “What is the nearest fire station to Dodger Stadium?” → **Fire Station 1, 2,003.1 m**, which is outside the crime/transit buffer.
- “Does the nearest hospital to Dodger Stadium have an emergency room?” → **BARLOW RESPIRATORY HOSPITAL, 779.4 m; recorded ER flag: No**. This is the nearest hospital overall, not a nearest-ER search.
- “Which sports are listed at Dodger Stadium?” → **Baseball** from the project venue roster.

Supporting answers cite their own CSV source(s), mark the coverage/as-of date as unrecorded in the processed summary, and separately label the processing timestamp. The **2020–2024** label applies only to crime. Rail/bus counts use the **800 m** buffer. Nearest fire/police/hospital records search the loaded dataset without that radius restriction, and distances are straight-line rather than travel or response times. The hospital ER flag is a recorded attribute, not confirmation of current availability. The nearest police station does not establish jurisdiction; listed sports do not confirm an event schedule.

Select a venue in the existing roster to ask about “this venue.” Without a map selection, the last single venue answered in chat provides context. Explicit names override context; ambiguous names always require clarification. After a two-venue comparison, name a venue or select one before using “this venue.”

`POST /api/chat` accepts `{"message": "How many incidents near this venue?", "venue_id": "V01"}`. `venue_id` is optional. Responses include `status`, `answer`, `question_type`, `results` (venue/category/count rows for crime; venue/topic rows with recorded context fields for other questions), `choices` (clarification questions), `source` (primary source), `sources` (all sources used), and `engine` (`data`, `claude`, or `fallback`). Supporting sources include `scope`, `coverage_date: null`, and `processed_at`. `GET /api/chat/suggestions` provides the demo questions; `/api/chat/config` reports whether Claude is configured and the selected model, without exposing its key. A missing or mismatched dataset returns HTTP 503 with a sourced explanation; invalid request sizes return HTTP 422.

Every answer labels **LAPD crime reports via the LA Open Data Portal** and the **800 m radius**. 2020–2024 totals cite `Crime_Data_from_2020_to_2024.csv`. The venue-page count, density, city comparison, and busiest month cite the 2020–present blend in `crime_merged.json`. The chatbot reads `venue_summary.json` through the existing reload-on-change store. Tests independently check totals and top categories against `crime_points_by_venue.json` for all 14 venues. Venue buffers overlap, so comparisons never sum their counts into a unique citywide total.

This small demo supports full-period 2020–2024 incident totals, the most common category (including ties), comparisons of exactly two venue totals, the venue page's 2020–present count, density, city comparison, and busiest month, the top five offense groups, the weekend pattern, and the groups that rose most from 2020 to 2024, and the supporting questions above. “What emergency services are near this venue?” gives the nearest recorded fire station, police/sheriff station, and hospital. It explicitly declines category-specific counts, date/time filters, other radii, nearest-ER searches, route planning, schedules/fares, travel/response times, causes, live conditions, safety judgments, and 2028 predictions. Traffic and official jurisdiction boundaries need validated ingestion/joining before they can be added. Venue matching and every number come from code and processed data. Keep the pipeline at `--radius-m 800` for this demo.

A question that asks for NIBRS offenses, Ticketmaster listings, or LADBS permits on their own is declined. Those files are not folded into the 2020–2024 incident totals. The 2020–present page count is separate: reports before March 7, 2024, then NIBRS offenses.

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
python -m unittest
node --check frontend/chat.js
for f in frontend/js/*.js; do node --check "$f"; done
```

The two citywide heatmap tests in `tests.test_api` also need the raw, gitignored `data/Crime_Data_from_2020_to_2024.csv`. If it is absent, those routes return HTTP 503 and those tests fail. The chatbot and per-venue endpoints use the processed JSON. Run chatbot checks alone with `.venv/bin/python -m unittest tests.test_chat tests.test_context_chat tests.test_claude`. Claude tests use a mock HTTP transport and make no external or paid API calls.
