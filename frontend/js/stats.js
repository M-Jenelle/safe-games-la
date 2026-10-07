import { categoryRows, filledMonths, overviewChart } from "./charts.js";
import { el, formatDistance, formatNumber, formatSports, ordinal, text } from "./dom.js";
import { jurisdictionLabel } from "./roster.js";
import { FLAG_LABELS, HIDDEN_FLAGS, mapHint, overview, state } from "./state.js";
import { infoMark, pageCopy } from "./tips.js";

export function densityNote(venue) {
  const rank = venue.present?.density_rank || venue.density_rank;
  if (!rank) return "per km²";
  const place = `${rank.tied ? "tied for " : ""}${ordinal(rank.rank)} of ${rank.of}`;
  return `${place} · per km²`;
}

export function stat(label, value, note, hint) {
  const title = hint
    ? el("span", { className: "info-label" }, [text(label), infoMark(hint)])
    : el("span", {}, [text(label)]);
  return el("article", { className: "stat" }, [
    title,
    el("strong", {}, [text(value)]),
    el("em", {}, [text(note)]),
  ]);
}

export function densityHint(venue) {
  return venue?.display?.density_hint || pageCopy("density_hint");
}

export function incidentNote(venue) {
  return venue?.display?.incident_note || pageCopy("incident_note");
}

export function presentCount(venue) {
  return venue.present?.count ?? venue.crime_count_nearby;
}

export function presentRate(venue) {
  return venue.present?.crime_per_km2 ?? venue.crime_per_km2;
}

export function cityStats(venue) {
  const series = venue?.city_baseline?.present;
  if (!series?.value) return [];
  return [stat("Vs city", series.value, series.caption, series.hint)];
}

export function overlapNote(venue) {
  return venue?.display?.overlap_note || "";
}

function hospitalHasEmergencyRoom(station) {
  return String(station?.emergency_room || "").toLowerCase() === "yes";
}

export function careStations(venue) {
  const cards = [
    stationCard("Fire", venue.nearest_fire_station),
    stationCard("Police", venue.nearest_police_station),
    stationCard("Hospital", venue.nearest_hospital),
  ];
  if (!hospitalHasEmergencyRoom(venue.nearest_hospital) && venue.nearest_emergency_room) {
    cards.push(stationCard("Emergency room", venue.nearest_emergency_room));
  }
  return cards;
}

function stationCard(label, station) {
  if (!station) {
    return el("article", { className: "station" }, [
      el("span", { className: "zone" }, [text(label)]),
      el("h3", {}, [text("None loaded")]),
    ]);
  }
  const lines = [
    el("span", { className: "zone" }, [text(label)]),
    el("h3", {}, [text(station.station_name)]),
    el("p", { className: "muted" }, [text(formatDistance(station.distance_m))]),
  ];
  if (station.agency) {
    lines.push(el("p", { className: "muted" }, [text(station.agency)]));
  }
  if (station.emergency_room) {
    const room = String(station.emergency_room).toLowerCase() === "yes"
      ? "Emergency room"
      : `Emergency room: ${station.emergency_room}`;
    lines.push(el("p", { className: "muted" }, [text(room)]));
  }
  const beds = Number(station.bed_capacity);
  if (Number.isFinite(beds) && beds > 0) {
    lines.push(el("p", { className: "muted" }, [text(`${formatNumber(beds)} beds`)]));
  }
  return el("article", { className: "station" }, lines);
}

export function renderOverview() {
  const venue = state.detail;
  overview.hidden = false;
  mapHint.hidden = true;
  if (!venue) {
    overview.replaceChildren(el("p", { className: "empty-detail" }, [text("Loading briefing…")]));
    return;
  }
  const months = filledMonths(venue.crime_by_month);
  const peak = months.reduce(
    (best, entry) => (entry.count > best.count ? entry : best),
    months[0],
  );
  const categories = categoryRows(venue.crime_by_category, venue.crime_count_nearby, { limit: 6, share: false });
  const flags = venue.data_quality_flags.filter((flag) => !HIDDEN_FLAGS.has(flag)).map((flag) => (
    el("span", { className: "flag" }, [text(FLAG_LABELS[flag] || flag)])
  ));
  const rail = venue.rail_stations_nearby.stations.map((station) => (
    el("span", { className: "pill" }, [
      text(`${station.station_name} · ${station.lines} · ${formatDistance(station.distance_m)}`),
    ])
  ));
  const buses = venue.bus_stops_nearby.lines.map((line) => (
    el("span", { className: "pill" }, [text(line)])
  ));
  overview.replaceChildren(
    el("div", { className: "detail-head" }, [
      el("div", {}, [
        el("p", { className: "zone" }, [text(`${venue.olympic_zone} · ${formatSports(venue.sports)}`)]),
        el("h2", { className: "detail-title", id: "overview-heading" }, [text(venue.venue_name)]),
      ]),
      el("div", { className: "detail-actions" }, [
        el("button", { className: "expand-briefing", type: "button", "data-expand-briefing": "true" }, [text("Expand")]),
        el("button", { className: "close-briefing", type: "button", "data-close-briefing": "true" }, [text("Close")]),
      ]),
    ]),
    el("p", { className: "address" }, [text(`${venue.address}, ${venue.city}`)]),
    el("div", { className: "stats" }, [
      stat("Incidents", formatNumber(presentCount(venue)), incidentNote(venue)),
      stat("Density", formatNumber(presentRate(venue)), densityNote(venue), densityHint(venue)),
      ...cityStats(venue),
      stat("Jurisdiction", jurisdictionLabel(venue.lapd_jurisdiction), "nearest local station"),
    ]),
    flags.length ? el("div", { className: "flags" }, flags) : el("p", { className: "muted" }, [text("No data-quality flags.")]),
    overlapNote(venue) ? el("p", { className: "terms" }, [text(overlapNote(venue))]) : el("span"),
    el("h3", { className: "section-title" }, [text("Top categories")]),
    ...(categories.length
      ? categories
      : [el("p", { className: "muted" }, [text("No incidents in this buffer.")])]),
    el("h3", { className: "section-title" }, [text("Incidents by month")]),
    overviewChart(venue.crime_by_month),
    el("p", { className: "chart-caption muted" }, [
      text(peak ? `Peak ${peak.key}: ${formatNumber(peak.count)} incidents.` : "No dated incidents."),
    ]),
    el("h3", { className: "section-title" }, [text(`Rail · ${venue.rail_stations_nearby.count}`)]),
    el("div", { className: "pills" }, rail.length ? rail : [el("span", { className: "muted" }, [text("None in the buffer.")])]),
    el("h3", { className: "section-title" }, [
      text(`Bus · ${formatNumber(venue.bus_stops_nearby.count)} stops · ${venue.bus_stops_nearby.lines.length} lines`),
    ]),
    el("div", { className: "pills" }, buses.length ? buses : [el("span", { className: "muted" }, [text("None in the buffer.")])]),
    el("h3", { className: "section-title" }, [text("Nearest response and care")]),
    el("div", { className: "station-grid" }, careStations(venue)),
    el("p", { className: "terms" }, [text(state.meta?.jurisdiction_method || "")]),
  );
}
