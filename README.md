# Safe Games LA

When millions of people come to Los Angeles for the 2028 Olympics, many of them will be visiting these neighborhoods for the first time. Local agencies will be managing much larger crowds around venues, transit stops, and the surrounding communities.

The information needed to plan for that already exists. It lives in different places. Safe Games LA brings those pieces together around each Olympic venue: crime patterns in the surrounding area, nearby hospitals, police stations, fire stations, Metro stations, and bus stops. A planner can see where transit may concentrate after an event, which emergency resources are already nearby, and where extra staffing may make sense.

This is a coordination tool for the Olympic committee and emergency services. It is a record of what has happened around these venues, with the pattern labeled as a pattern. A later, simpler version could help visitors and locals understand the area around a venue and plan how they want to get there.

**Fewer blind spots. Earlier coordination. A safer experience for the people who live here and the people coming for the Games.**

## What you can do

Open the map. Fourteen LA28 venues are already on it. The crime layer is a heatmap of LAPD reports, so concentrations show up as color rather than as a list of pins.

Select a venue. The briefing under the map shows the 2020–present record count, density, how that circle compares with the city, nearby transit, and the nearest fire station, police station, and hospital. Expand opens the full page: offense groups, weekday pattern, month, time of day, distance from the venue, permit-day context, and weather. Time of day and distance are waffle charts. Click a square to see the offense groups in that slice.

Compare two venues side by side, including a weather view. Ask Torchy a plain-language question. The figures are calculated from the venue tables.

## For reviewers

The hosted briefing is on Google Cloud Run:

[https://safe-games-la-dbev6ehrka-uw.a.run.app](https://safe-games-la-dbev6ehrka-uw.a.run.app)

Open that address and sign in with a Google account on **kaygen.com**. The sign-in is required before the map loads. Accounts outside that domain are not granted access. The same briefing can be run locally, below, without that account. The map and the venue pages do not need a Google Cloud login. Torchy writes with Gemini when Vertex credentials are present, and without them it still answers the demo questions from the tables.

## Demo path

1. Open the hosted briefing above, or start the app and open [http://127.0.0.1:8000](http://127.0.0.1:8000).
2. Leave the crime view on **All crime**. The heatmap is the hotspot view.
3. Select **Peacock Theater** or **Crypto.com Arena**, then **Dodger Stadium**. Downtown circles overlap, so the same report can belong to more than one venue. The compare page says so.
4. Open **Ask Torchy** and try:
   - How many incidents were reported near Dodger Stadium?
   - Which day of the week is busiest near the Coliseum?
   - Which crimes rose the most near Peacock Theater?
   - Where is the nearest Metro station to Peacock Theater?
   - What is the nearest fire station to Dodger Stadium?
   - Compare crime near Peacock Theater and Dodger Stadium.
5. On a venue page, open **Weather**. Wet days and hot days are compared with other days in the same months. That is the rain question: what the record shows on venue days with that kind of weather, not a forecast for 2028.

## How this meets the use case

The assignment is to turn the Los Angeles crime file from 2020 to 2024 into something a public-safety user can act on: where and when, what is shifting, and how to act. The product is scoped to the Olympic venues those agencies will actually staff.

| Use case | In this tool |
|---|---|
| Hotspots | Citywide crime heatmap, then a half-mile circle around the selected venue. Filter by year and month. Switch the crime view among all crime, high-count cells, venue circles, and NIBRS offenses. |
| Trends | Venue page and Torchy: busiest weekday, top offense groups, and which groups rose from 2020 through 2023. Later years are shown in their own columns because the reporting system changed. |
| Compare areas | Compare page for any two venues. Counts are kept separate. Overlapping downtown circles are labeled. |
| Pattern on event days | Permit days, and Dodger home games, are compared with other days of the same weekday. A Saturday event is compared with other Saturdays. |
| Weather as a planning input | Wet-day and hot-day comparisons on the venue page and the compare page. |
| Natural-language search | Ask Torchy. Gemini calls the calculation tools. Python produces the count, and the answer stays on the recorded tables. |
| Decision support | Nearest fire, police, and hospital, plus rail stations and bus stops inside the venue circle, so staffing and access can be discussed from the same screen. |

## How a number is made

Each venue has a half-mile circle. The calculation uses 800 meters, which is half a mile. Crime, rail, and bus counts use that circle. The nearest fire station, police station, and hospital come from the full loaded list, so a station can sit outside the circle. Those distances, and each Metro station distance, are shown in miles. Density is records per square mile. The city comparison uses that same square-mile rate.

Two crime files cover the period, and they are not added together. Through March 6, 2024 the count is one LAPD police report. From March 7, 2024 forward the count is one NIBRS offense. A trend that crosses that date is shown as two series. Offense groups on the NIBRS side use NIBRS codes.

Monthly counts from March through December 2024 sit well below the months on either side. At Crypto.com Arena, February 2024 is 290 records and January 2025 is 241, while April 2024 is 96 and December 2024 is 115. That gap is in the extract. It is not labeled as a drop in crime. Gray months after the latest record average that calendar month in earlier years that use the same counting unit. April through December 2024 are left out of that average, because that stretch of the extract is thin.

The city rate uses Los Angeles city land area from the 2020 Census. Venue circles are not summed into it.

Weather comes from the Open-Meteo archive at the venue pin. On the venue page, a wet day or a hot day is compared with other days in the same months. Months past the latest crime record, shown in gray on the venue page, are the average of that month in earlier years with the same counting unit. April through December 2024 are left out. They are estimates, not a forecast for the Games. When Torchy compares wet days, it holds the weekday fixed and can include a range. That adjusted reply is not the percentage in the weather table. The permit-day and home-game tables already hold the weekday fixed.

Ask Torchy uses Gemini 2.5 Flash. Gemini decides which calculation to run. Python returns the count, the comparison, or the live reading, and Gemini writes the reply from that result. If Gemini is unavailable, those demo questions are still answered from the same tables.

## What this tool will not say

It will not call a neighborhood safe or unsafe unless that sentence also includes the citywide rate. It will not predict a daily crime count for the 2028 Games, name a cause, or give a travel time, a response time, or a fare. A live Metro notice, when the transit feed is connected, is a service alert only. It is not a crime count on a train. Victim age and sex are not in the loaded files. A count near a station, stop, or facility is records within half a mile of that pin. It is not a count of crime on a train or inside the building.

The permit-day and home-game tables hold the weekday fixed. A Saturday event is compared with other Saturdays, weighted by how the event days fall across the week. Weekend days still carry more events, and the page says so, but that mix is not the comparison in the table. A count model can also hold the month fixed and give a range. That range does not replace the percentage in the table. It is a past association, not a forecast.

The heatmap is LAPD. Places outside the LAPD reporting area have no crime cells. Some venues have no rail station inside half a mile. Dodger Stadium is one of them. That is the recorded distance, not a missing pin.

## Data

| Data | Source | Role |
|---|---|---|
| Crime, 2020–2024 | [LA Open Data, Crime Data from 2020 to 2024](https://data.lacity.org/Public-Safety/Crime-Data-from-2020-to-2024/2nrs-mtv8) · [CSV](https://data.lacity.org/api/v3/views/2nrs-mtv8/export.csv?accessType=DOWNLOAD) | One row per police report. Primary dataset named in the use case. |
| NIBRS offenses | [LA Open Data, LAPD NIBRS Offenses Dataset](https://data.lacity.org/Public-Safety/LAPD-NIBRS-Offenses-Dataset/k7nn-b2ep) · [CSV](https://data.lacity.org/api/v3/views/k7nn-b2ep/export.csv?accessType=DOWNLOAD) | One row per offense from March 7, 2024. A Tuesday evening check downloads a new extract only when the portal timestamp changes. |
| LA28 venues | Official LA28 venue list | Fourteen venues, geocoded. The map starts here. |
| Metro rail and bus | LA Metro GTFS | Stations and stops inside the venue circle. Static, not live. |
| Fire stations | data.lacity.org | Nearest station. |
| Police and sheriff stations | LA County GIS | Nearest station. Not an official jurisdiction boundary. |
| Hospitals | LA County hospital facilities | Nearest facility and the recorded emergency-room flag. |
| Weather | Open-Meteo historical archive | Daily weather at the venue pin, joined to the daily crime count. |
| Event permits | LADBS temporary special-event permits | Days matched to the nearest venue within half a mile. |
| Dodger home games | MLB home-game list, 2020–2024 | Dodger Stadium only. |
| City land area | U.S. Census QuickFacts, Los Angeles city, 2020 | 469.49 square miles. City rate only. |

Addresses in the crime file are rounded to the hundred block. The heatmap is block-level.

## Run it

Python 3.10 or newer, from the repo root:

```bash
python -m pip install -r requirements.txt
python -m uvicorn backend.main:app --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

That command is the whole setup. Before the site accepts a request, the server builds any processed file that is not already on disk:

1. Venue buffers at half a mile, calculated as 800 m (`pipeline.run`)
2. Time of day and distance from the venue
3. Permit-day comparison
4. The NIBRS extract, if it is not downloaded yet
5. Merged 2020–present counts, NIBRS charts, and the city baseline
6. Weather at each venue pin
7. Ticketmaster listings, when the local export is already in the repo

Files that are already present are left alone. A restart, including a reload after a code change, does not reread the crime extracts or rewrite the published numbers.

The hosted site checks the NIBRS extract on Cloud Scheduler, Tuesdays at 6:00 PM Pacific. The city portal does not publish a fixed biweekly calendar. The two updates still visible both landed on a Tuesday, and 6:00 PM is after that afternoon publish. The job downloads only when the portal timestamp is new. It then rebuilds the merged counts, the NIBRS charts, the city baseline, and the daily crime counts joined to weather, and starts a new revision so the map and Torchy read those files. An unchanged Tuesday does nothing else.

Both crime extracts are too large for git. Download them from LA Open Data:

[Crime Data from 2020 to 2024](https://data.lacity.org/Public-Safety/Crime-Data-from-2020-to-2024/2nrs-mtv8) · [CSV](https://data.lacity.org/api/v3/views/2nrs-mtv8/export.csv?accessType=DOWNLOAD). Save this as `data/Crime_Data_from_2020_to_2024.csv`.

[LAPD NIBRS Offenses Dataset](https://data.lacity.org/Public-Safety/LAPD-NIBRS-Offenses-Dataset/k7nn-b2ep) · [CSV](https://data.lacity.org/api/v3/views/k7nn-b2ep/export.csv?accessType=DOWNLOAD). The app saves its extract as `data/raw/nibrs/nibrs_current.csv`.

These are the city’s public files, datasets `2nrs-mtv8` and `k7nn-b2ep`. The hosted briefing already includes both. A fresh local machine needs the 2020–2024 file before the first full build. Without it, the server still starts from any processed files that are already present, and the log names each build it skipped. If the NIBRS file is not already there, the app downloads it from the same portal.

The same prepare step can be run without opening the site:

```bash
python -m pipeline.prepare
```

The map needs a referrer-restricted Google Maps JavaScript key in `.env`:

```dotenv
GOOGLE_MAPS_API_KEY=your_browser_key
```

Allow `http://127.0.0.1:8000/*`. The hosted map also needs `https://safe-games-la-dbev6ehrka-uw.a.run.app/*` on that same key. Restart the local server after changing the key. Torchy answers without the key. The basemap does not draw without it.

Publishing the hosted copy, from a machine signed in to the Google Cloud project:

```bash
powershell -ExecutionPolicy Bypass -File scripts/deploy_cloud_run.ps1
```

That publish keeps the Google sign-in and limits it to kaygen.com accounts. The Tuesday NIBRS check is separate:

```bash
powershell -ExecutionPolicy Bypass -File scripts/deploy_nibrs_job.ps1
```

Run it after the site publish. The service has to be the copy that knows how to load a new extract.

Torchy uses Gemini when the machine can reach Vertex AI. Sign in with Application Default Credentials and set the project:

```bash
gcloud auth application-default login
```

```dotenv
GOOGLE_CLOUD_PROJECT=your_gcp_project
GOOGLE_CLOUD_LOCATION=us-west1
GEMINI_MODEL=gemini-2.5-flash
```

The account needs `roles/aiplatform.user` on that project. The model name and location can be omitted. Those two defaults are `gemini-2.5-flash` and `us-west1`.

Useful endpoints: `/api/health`, `/api/meta`, `/api/map`, `/api/map/crime`, `/api/venues`, `/api/venues/{venue_id}`, `/api/chat/v2`, `/api/chat/v2/stream`.

Checks:

```bash
python -m unittest
```

The citywide heatmap tests, and the tests that rank stations or Metro lines, skip when `data/Crime_Data_from_2020_to_2024.csv` is not on disk. The other tests use the processed JSON.
