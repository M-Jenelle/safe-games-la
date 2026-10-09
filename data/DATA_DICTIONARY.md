# Data dictionary

Torchy queries these tables. It does not invent columns. The warnings below are attached by the tools whenever a result crosses them.

## Series break

March 7, 2024 is a change in what a row means.

- Before that date, `incident_count` and `incidents` are LAPD police reports: one row per report.
- From that date forward, `venue_days.incident_count` is NIBRS offenses: one row per offense, and `count_source` is `nibrs`.
- The two are not added together. A total that crosses the date is two series.

`incidents` holds the 2020–2024 report points inside each 800 m venue circle. It does not repeat the NIBRS offense rows.

## Overlap

An 800 m circle around one venue can cover the same block as another venue, especially downtown. `overlap_pairs` lists circles whose centers are less than 1,600 m apart. A report in the overlap is stored once per venue. Adding venue totals does not produce a count of distinct reports.

## Reported crime

Every crime figure is a reported record. It is not a count of every crime that happened.

## Tables

### venues

One row per LA28 venue. `buffer_radius_m` is 800.

### venue_days

One row per venue per day. Weather is the Open-Meteo archive at the venue pin. `wet_day` is 1 when precipitation was recorded. `hot_day` is 1 when `temp_f_mean` is at least 85°F. `is_permit_event_day` marks a day with at least one LADBS temporary-event permit matched to that venue. `incident_count` follows the series break above.

### incidents

One LAPD report inside a venue circle, with `date` before the series break. `category` is the report label. The same `incident_id` can appear under two `venue_id` values when circles overlap.

### permits

Days with a matched permit: `venue_id`, `date`, `permit_count`, `incident_count`, `count_source`.

### events

Ticketmaster listings and Dodger home games. Listings are not joined to crime counts. A home game has `kind` = `home_game` and `venue_id` = `V01`.

### facilities

Fire stations, police or sheriff stations, and hospitals. `kind` is `fire`, `police`, or `hospital`. `emergency_room` is the recorded flag for hospitals and is blank otherwise. This is not a response time and not a jurisdiction boundary.

### transit_stops

Metro rail stations and bus stops from the static GTFS extracts. `kind` is `rail` or `bus`. This is not a live alert, a schedule, or a fare.

### offense_counts

One row per venue, offense group, and series. `series` is `lapd_report` through March 6, 2024, or `nibrs` from March 7, 2024. The group labels are not the same series. Grand theft auto is Vehicle in the older reports and Theft under NIBRS codes. Ask for `series = 'nibrs'` when the question is about the latest labels only.

### overlap_pairs

Venue pairs whose 800 m circles can share reports. `distance_m` is the straight-line distance between pins.
