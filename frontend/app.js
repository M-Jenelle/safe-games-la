const FLAG_LABELS = {
  no_crime_within_radius: "No crime reports in the buffer",
  no_rail_stations_within_radius: "No rail stations in the buffer",
  no_bus_stops_within_radius: "No bus stops in the buffer",
  no_fire_stations_loaded: "No fire stations in the dataset",
  no_police_stations_loaded: "No police stations in the dataset",
  geocode_mismatch: "Listed coordinates differ from the geocoder",
  geocode_not_found: "Geocoder could not confirm this address",
  city_of_la_but_nearest_station_is_not_lapd: "Nearest station is not LAPD",
};

const state = {
  meta: null,
  map: null,
  venues: [],
  selectedId: null,
  detail: null,
  zone: "all",
  sort: "density",
};

let requestToken = 0;

const rosterList = document.querySelector("#roster-list");
const zoneFilter = document.querySelector("#zone-filter");
const sortMode = document.querySelector("#sort-mode");
const metaStrip = document.querySelector("#meta-strip");
const detail = document.querySelector("#detail");
const mapCanvas = document.querySelector("#map-canvas");
const mapViewport = document.querySelector("#map-viewport");
const mapFrame = document.querySelector("#map-frame");
const mapPanel = document.querySelector(".map-panel");
const mapHint = document.querySelector("#map-hint");
const ZOOM = 7;
const chatContext = document.querySelector("#chat-context");

rosterList.addEventListener("click", (event) => {
  const card = event.target.closest("[data-venue-id]");
  if (!card) return;
  selectVenue(card.dataset.venueId);
});

mapCanvas.addEventListener("click", (event) => {
  const marker = event.target.closest("[data-venue-id]");
  if (!marker) return;
  selectVenue(marker.dataset.venueId);
});

detail.addEventListener("click", (event) => {
  if (event.target.closest("[data-close-briefing]")) clearSelection();
});

zoneFilter.addEventListener("change", () => {
  state.zone = zoneFilter.value;
  renderRoster();
  renderMap();
});

sortMode.addEventListener("change", () => {
  state.sort = sortMode.value;
  renderRoster();
});

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "className") node.className = value;
    else node.setAttribute(key, value);
  }
  for (const child of children) node.append(child);
  return node;
}

function text(value) {
  return document.createTextNode(value == null ? "" : String(value));
}

async function fetchJson(path) {
  const response = await fetch(path);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detailText = typeof body.detail === "string" ? body.detail : response.statusText;
    throw new Error(detailText);
  }
  return body;
}

function formatNumber(value) {
  return Number(value).toLocaleString("en-US");
}

function formatDistance(meters) {
  if (meters == null) return "Unknown distance";
  if (meters < 1000) return `${Math.round(meters)} m`;
  return `${(meters / 1000).toFixed(1)} km`;
}

function formatWhen(iso) {
  if (!iso) return "unknown date";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

function densityT(value) {
  const rates = state.venues.map((venue) => venue.crime_per_km2);
  const min = Math.log(Math.min(...rates) + 1);
  const max = Math.log(Math.max(...rates) + 1);
  if (max === min) return 0.5;
  return (Math.log(value + 1) - min) / (max - min);
}

function densityColor(value) {
  const t = densityT(value);
  const low = [42, 107, 79];
  const mid = [184, 122, 46];
  const high = [141, 47, 31];
  const mix = t < 0.5
    ? lerp(low, mid, t / 0.5)
    : lerp(mid, high, (t - 0.5) / 0.5);
  return `rgb(${mix.map((channel) => Math.round(channel)).join(", ")})`;
}

function lerp(a, b, t) {
  return a.map((channel, index) => channel + (b[index] - channel) * t);
}

function visibleVenues() {
  const filtered = state.venues.filter((venue) => (
    state.zone === "all" || venue.olympic_zone === state.zone
  ));
  const sorted = [...filtered];
  if (state.sort === "name") {
    sorted.sort((a, b) => a.venue_name.localeCompare(b.venue_name));
  } else {
    sorted.sort((a, b) => b.crime_per_km2 - a.crime_per_km2);
  }
  return sorted;
}

function jurisdictionLabel(value) {
  if (value === true) return "LAPD";
  if (value === false) return "Not LAPD";
  return "Unknown";
}

function renderMeta() {
  const metaData = state.meta;
  metaStrip.replaceChildren(
    text("Buffer "),
    el("strong", {}, [text(`${Math.round(metaData.buffer_radius_m)} m`)]),
    text(` · ${metaData.venue_count} venues · built ${formatWhen(metaData.generated_at)}`),
    el("br"),
    text("Densest "),
    el("strong", {}, [text(metaData.densest_venue.venue_name)]),
    text(` (${formatNumber(metaData.densest_venue.crime_per_km2)} / km²) · quietest `),
    el("strong", {}, [text(metaData.quietest_venue.venue_name)]),
  );
}

function renderZoneOptions() {
  const zones = [...new Set(state.venues.map((venue) => venue.olympic_zone))].sort();
  for (const zone of zones) {
    zoneFilter.append(el("option", { value: zone }, [text(zone)]));
  }
}

function renderRoster() {
  const venues = visibleVenues();
  if (!venues.length) {
    rosterList.replaceChildren(el("p", { className: "muted" }, [text("No venues in this zone.")]));
    return;
  }
  rosterList.replaceChildren(...venues.map((venue) => {
    const selected = venue.venue_id === state.selectedId;
    return el("button", {
      className: selected ? "venue-card selected" : "venue-card",
      type: "button",
      "data-venue-id": venue.venue_id,
      "aria-pressed": selected ? "true" : "false",
      style: `border-left-color: ${densityColor(venue.crime_per_km2)}`,
    }, [
      el("div", { className: "card-top" }, [
        el("span", { className: "zone" }, [text(venue.olympic_zone)]),
        el("span", { className: "rate" }, [text(`${formatNumber(venue.crime_per_km2)} / km²`)]),
      ]),
      el("h3", {}, [text(venue.venue_name)]),
      el("div", { className: "sports" }, [text(venue.sports)]),
      el("div", { className: "card-foot" }, [
        text(`${formatNumber(venue.crime_count_nearby)} incidents · ${jurisdictionLabel(venue.lapd_jurisdiction)}`),
      ]),
    ]);
  }));
  rosterList.querySelector(".venue-card.selected")?.scrollIntoView({ block: "nearest" });
}

function project(latitude, longitude) {
  const bounds = state.map.bounds;
  const x = ((longitude - bounds.west) / (bounds.east - bounds.west)) * 100;
  const y = ((bounds.north - latitude) / (bounds.north - bounds.south)) * 100;
  return { x, y };
}

function renderMap() {
  if (!state.map) return;
  const zoomed = Boolean(state.selectedId);
  const markerScale = zoomed ? 1 / ZOOM : 1;
  const markers = state.map.markers.map((marker) => {
    const spot = project(marker.latitude, marker.longitude);
    const selected = marker.venue_id === state.selectedId;
    const dimmed = state.zone !== "all" && marker.olympic_zone !== state.zone;
    const size = 14 + densityT(marker.crime_per_km2) * 12;
    const classes = ["marker"];
    if (selected) classes.push("selected");
    if (dimmed) classes.push("dimmed");
    const children = [];
    if (selected) {
      children.push(el("span", { className: "marker-label" }, [text(marker.venue_name)]));
    }
    return el("button", {
      className: classes.join(" "),
      type: "button",
      "data-venue-id": marker.venue_id,
      "aria-pressed": selected ? "true" : "false",
      "aria-label": `${marker.venue_name}, ${formatNumber(marker.crime_per_km2)} per square kilometer`,
      style: `left: ${spot.x}%; top: ${spot.y}%; width: ${size}px; height: ${size}px; background: ${densityColor(marker.crime_per_km2)}; transform: translate(-50%, -50%) scale(${markerScale})`,
    }, children);
  });
  const labels = state.map.labels.map((label) => {
    const spot = project(label.latitude, label.longitude);
    return el("span", {
      className: "map-label",
      style: `left: ${spot.x}%; top: ${spot.y}%`,
    }, [text(label.name)]);
  });
  mapCanvas.replaceChildren(...labels, ...markers);
  syncMapFrame();
}

function syncMapFrame() {
  const zoomed = Boolean(state.selectedId);
  mapPanel.classList.toggle("has-briefing", zoomed);
  mapFrame.classList.toggle("is-zoomed", zoomed);
  requestAnimationFrame(applyZoom);
}

function applyZoom() {
  if (!state.selectedId || !state.map) {
    mapCanvas.style.transform = "translate(0px, 0px) scale(1)";
    return;
  }
  const marker = state.map.markers.find((item) => item.venue_id === state.selectedId);
  if (!marker) return;
  const spot = project(marker.latitude, marker.longitude);
  const width = mapViewport.clientWidth;
  const height = mapViewport.clientHeight;
  const markerX = (spot.x / 100) * width;
  const markerY = (spot.y / 100) * height;
  const tx = width / 2 - markerX * ZOOM;
  const ty = height / 2 - markerY * ZOOM;
  mapCanvas.style.transform = `translate(${tx}px, ${ty}px) scale(${ZOOM})`;
}

window.addEventListener("resize", () => {
  if (state.map) applyZoom();
});

function renderChatContext() {
  const venue = state.venues.find((item) => item.venue_id === state.selectedId);
  if (!venue) {
    chatContext.textContent = "No briefing open. General questions are in scope.";
    return;
  }
  chatContext.textContent = `Briefing open: ${venue.venue_name}. Questions can be about that venue or about the city as a whole.`;
}

function clearSelection() {
  requestToken += 1;
  state.selectedId = null;
  state.detail = null;
  detail.hidden = true;
  mapHint.hidden = false;
  renderRoster();
  renderMap();
  renderChatContext();
}

function filledMonths(byMonth) {
  const keys = Object.keys(byMonth).sort();
  if (!keys.length) return [];
  const [startYear, startMonth] = keys[0].split("-").map(Number);
  const [endYear, endMonth] = keys[keys.length - 1].split("-").map(Number);
  const entries = [];
  let year = startYear;
  let month = startMonth;
  while (year < endYear || (year === endYear && month <= endMonth)) {
    const key = `${year}-${String(month).padStart(2, "0")}`;
    entries.push({ key, count: byMonth[key] || 0 });
    month += 1;
    if (month === 13) {
      month = 1;
      year += 1;
    }
  }
  return entries;
}

function monthChart(byMonth) {
  const entries = filledMonths(byMonth);
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "chart");
  svg.setAttribute("viewBox", "0 0 640 140");
  svg.setAttribute("role", "img");
  if (!entries.length) {
    svg.setAttribute("aria-label", "No dated incidents in this buffer");
    return svg;
  }
  const max = Math.max(...entries.map((entry) => entry.count), 1);
  const peak = entries.reduce((best, entry) => (entry.count > best.count ? entry : best), entries[0]);
  svg.setAttribute(
    "aria-label",
    `Monthly incidents from ${entries[0].key} to ${entries[entries.length - 1].key}. Peak ${peak.key} with ${peak.count}.`,
  );
  const width = 640 / entries.length;
  entries.forEach((entry, index) => {
    const height = (entry.count / max) * 110;
    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    rect.setAttribute("x", String(index * width + 0.4));
    rect.setAttribute("y", String(120 - height));
    rect.setAttribute("width", String(Math.max(width - 0.8, 0.4)));
    rect.setAttribute("height", String(height));
    rect.setAttribute("fill", entry.key === peak.key ? "#9a4e24" : "#123f4d");
    svg.append(rect);
  });
  return svg;
}

function categoryRows(byCategory) {
  const entries = Object.entries(byCategory)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6);
  const max = entries.length ? entries[0][1] : 1;
  return entries.map(([category, count]) => el("div", { className: "bar-row" }, [
    el("div", {}, [
      el("div", { className: "bar-label", title: category }, [text(category)]),
      el("div", { className: "bar-track" }, [
        el("div", { className: "bar-fill", style: `width: ${(count / max) * 100}%` }),
      ]),
    ]),
    el("div", { className: "bar-count" }, [text(formatNumber(count))]),
  ]));
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
  return el("article", { className: "station" }, lines);
}

function renderDetail() {
  const venue = state.detail;
  if (!venue) {
    detail.replaceChildren(el("p", { className: "empty-detail" }, [text("Select a venue to open its briefing.")]));
    return;
  }
  const months = filledMonths(venue.crime_by_month);
  const peak = months.reduce(
    (best, entry) => (entry.count > best.count ? entry : best),
    months[0],
  );
  const categories = categoryRows(venue.crime_by_category);
  const flags = venue.data_quality_flags.map((flag) => (
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

  detail.hidden = false;
  mapHint.hidden = true;
  detail.replaceChildren(
    el("div", { className: "detail-head" }, [
      el("div", {}, [
        el("p", { className: "zone" }, [text(`${venue.olympic_zone} · ${venue.sports}`)]),
        el("h2", { className: "detail-title", id: "detail-heading" }, [text(venue.venue_name)]),
      ]),
      el("button", { className: "close-briefing", type: "button", "data-close-briefing": "true" }, [text("Close")]),
    ]),
    el("p", { className: "address" }, [text(`${venue.address}, ${venue.city}`)]),
    el("div", { className: "stats" }, [
      stat("Incidents", formatNumber(venue.crime_count_nearby), "inside the buffer"),
      stat("Density", formatNumber(venue.crime_per_km2), "per km²"),
      stat("Jurisdiction", jurisdictionLabel(venue.lapd_jurisdiction), "nearest local station"),
      stat("Buffer", `${Math.round(venue.buffer_radius_m)} m`, "radius"),
    ]),
    flags.length ? el("div", { className: "flags" }, flags) : el("p", { className: "muted" }, [text("No data-quality flags.")]),
    el("h3", { className: "section-title" }, [text("Top categories")]),
    ...(categories.length
      ? categories
      : [el("p", { className: "muted" }, [text("No incidents in this buffer.")])]),
    el("h3", { className: "section-title" }, [text("Incidents by month")]),
    monthChart(venue.crime_by_month),
    el("p", { className: "chart-caption muted" }, [
      text(peak ? `Peak ${peak.key}: ${formatNumber(peak.count)} incidents.` : "No dated incidents."),
    ]),
    el("h3", { className: "section-title" }, [text(`Rail · ${venue.rail_stations_nearby.count}`)]),
    el("div", { className: "pills" }, rail.length ? rail : [el("span", { className: "muted" }, [text("None in the buffer.")])]),
    el("h3", { className: "section-title" }, [
      text(`Bus · ${formatNumber(venue.bus_stops_nearby.count)} stops · ${venue.bus_stops_nearby.lines.length} lines`),
    ]),
    el("div", { className: "pills" }, buses.length ? buses : [el("span", { className: "muted" }, [text("None in the buffer.")])]),
    el("h3", { className: "section-title" }, [text("Nearest response")]),
    el("div", { className: "station-grid" }, [
      stationCard("Fire", venue.nearest_fire_station),
      stationCard("Police", venue.nearest_police_station),
    ]),
    el("p", { className: "footnote" }, [text(state.meta.jurisdiction_method)]),
  );
}

function stat(label, value, note) {
  return el("article", { className: "stat" }, [
    el("span", {}, [text(label)]),
    el("strong", {}, [text(value)]),
    el("em", {}, [text(note)]),
  ]);
}

async function selectVenue(venueId) {
  const token = ++requestToken;
  const card = state.venues.find((venue) => venue.venue_id === venueId);
  if (card && state.zone !== "all" && card.olympic_zone !== state.zone) {
    state.zone = "all";
    zoneFilter.value = "all";
  }
  state.selectedId = venueId;
  state.detail = null;
  renderRoster();
  renderMap();
  renderChatContext();
  detail.hidden = false;
  mapHint.hidden = true;
  detail.replaceChildren(el("p", { className: "empty-detail" }, [text("Loading briefing…")]));
  if (window.innerWidth <= 980) detail.scrollIntoView({ block: "start" });
  try {
    const venue = await fetchJson(`/api/venues/${encodeURIComponent(venueId)}`);
    if (token !== requestToken) return;
    state.detail = venue;
    renderDetail();
  } catch (error) {
    if (token !== requestToken) return;
    detail.replaceChildren(el("p", { className: "empty-detail" }, [text(error.message)]));
  }
}

async function init() {
  try {
    const [metaData, list, mapData] = await Promise.all([
      fetchJson("/api/meta"),
      fetchJson("/api/venues"),
      fetchJson("/api/map"),
    ]);
    state.meta = metaData;
    state.venues = list.venues;
    state.map = mapData;
    renderMeta();
    renderZoneOptions();
    renderRoster();
    renderMap();
    renderChatContext();
  } catch (error) {
    metaStrip.textContent = error.message;
    detail.hidden = false;
    detail.replaceChildren(el("p", { className: "empty-detail" }, [text(error.message)]));
  }
}

init();
