const FLAG_LABELS = {
  no_crime_within_radius: "No crime reports in the buffer",
  no_rail_stations_within_radius: "No rail stations in the buffer",
  no_bus_stops_within_radius: "No bus stops in the buffer",
  no_fire_stations_loaded: "No fire stations in the dataset",
  no_police_stations_loaded: "No police stations in the dataset",
  no_hospitals_loaded: "No hospitals in the dataset",
  geocode_mismatch: "Listed coordinates differ from the geocoder",
  geocode_not_found: "Geocoder could not confirm this address",
  city_of_la_but_nearest_station_is_not_lapd: "Nearest station is not LAPD",
};

const state = {
  meta: null,
  map: null,
  googleMap: null,
  venueMarkers: [],
  heatOverlay: null,
  bufferCircle: null,
  facilities: null,
  crimePoints: null,
  crimeView: "all",
  crimeHot: false,
  crimeCache: {},
  layerViews: {
    fire: "all",
    hospitals: "all",
    police: "all",
    rail: "all",
    bus: "all",
  },
  layers: {
    venues: true,
    crime: true,
    fire: false,
    hospitals: false,
    police: false,
    rail: false,
    bus: false,
  },
  layerInfo: null,
  tipKey: "",
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
const overview = document.querySelector("#detail");
const detail = document.querySelector("#venue-page");
const mapFrame = document.querySelector("#map-frame");
const mapPanel = document.querySelector(".map-panel");
const mapHint = document.querySelector("#map-hint");
const ZOOM = 7;
const chatContext = document.querySelector("#chat-context");
const analysisLead = document.querySelector("#analysis-lead");
const chatPanel = document.querySelector("#chat-panel");
const chatLauncher = document.querySelector("#chat-launcher");
const chatClose = document.querySelector("#chat-close");

function setChatOpen(open) {
  chatPanel.hidden = !open;
  chatLauncher.hidden = open;
  chatLauncher.setAttribute("aria-expanded", open ? "true" : "false");
  if (open) chatClose.focus();
  else chatLauncher.focus();
}

chatLauncher.addEventListener("click", () => setChatOpen(true));
chatClose.addEventListener("click", () => setChatOpen(false));
const appShell = document.querySelector(".app");
const roster = document.querySelector("#roster");
const rosterToggle = document.querySelector("#roster-toggle");
const rosterToggleLabel = rosterToggle.querySelector(".visually-hidden");
const rosterBackdrop = document.querySelector("#roster-backdrop");
const NARROW_ROSTER = 980;
let rosterNarrow = window.innerWidth <= NARROW_ROSTER;

function rosterIsNarrow() {
  return window.innerWidth <= NARROW_ROSTER;
}

function syncHeaderHeight() {
  const header = document.querySelector(".topbar");
  if (header) document.documentElement.style.setProperty("--header-h", `${header.offsetHeight}px`);
}

function resizeMap() {
  const map = state.googleMap;
  if (!map || !window.google?.maps?.event) return;
  google.maps.event.trigger(map, "resize");
}

function setRosterCollapsed(collapsed) {
  appShell.classList.toggle("roster-collapsed", collapsed);
  roster.setAttribute("aria-hidden", collapsed ? "true" : "false");
  if (collapsed) roster.setAttribute("inert", "");
  else roster.removeAttribute("inert");
  rosterToggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
  rosterToggleLabel.textContent = collapsed ? "Show venues" : "Hide venues";
  window.setTimeout(resizeMap, 220);
}

rosterToggle.addEventListener("click", () => {
  setRosterCollapsed(!appShell.classList.contains("roster-collapsed"));
});
rosterBackdrop.addEventListener("click", () => {
  setRosterCollapsed(true);
  rosterToggle.focus();
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (!chatPanel.hidden) {
    setChatOpen(false);
    return;
  }
  if (rosterIsNarrow() && !appShell.classList.contains("roster-collapsed")) {
    setRosterCollapsed(true);
    rosterToggle.focus();
  }
});

syncHeaderHeight();
setRosterCollapsed(rosterIsNarrow());

rosterList.addEventListener("click", (event) => {
  const card = event.target.closest("[data-venue-id]");
  if (!card) return;
  selectVenue(card.dataset.venueId);
});

overview.addEventListener("click", (event) => {
  if (event.target.closest("[data-close-briefing]")) clearSelection();
  if (event.target.closest("[data-expand-briefing]")) openVenuePage();
});

detail.addEventListener("click", (event) => {
  if (event.target.closest("[data-close-briefing]")) closeVenuePage();
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

function formatSports(value) {
  return String(value || "")
    .split(";")
    .map((part) => part.trim())
    .filter(Boolean)
    .join(" · ");
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
  if (!metaStrip || !state.meta) return;
  const metaData = state.meta;
  metaStrip.replaceChildren(
    el("p", { className: "meta-line" }, [
      text(`${Math.round(metaData.buffer_radius_m)} m buffer · ${metaData.venue_count} venues`),
    ]),
    el("p", { className: "meta-line" }, [
      text(`Updated ${formatWhen(metaData.generated_at)}`),
    ]),
    el("p", { className: "meta-line meta-quiet" }, [
      text(`Densest ${metaData.densest_venue.venue_name} (${formatNumber(metaData.densest_venue.crime_per_km2)}/km²) · quietest ${metaData.quietest_venue.venue_name}`),
    ]),
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
    }, [
      el("div", { className: "card-top" }, [
        el("span", { className: "zone" }, [text(venue.olympic_zone)]),
        el("span", { className: "rate" }, [text(`${formatNumber(venue.crime_per_km2)}/km²`)]),
      ]),
      el("h3", {}, [text(venue.venue_name)]),
      el("div", { className: "sports" }, [text(formatSports(venue.sports))]),
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

const FACILITY_STYLE = {
  fire: { color: [223, 0, 36], scale: 0.9 },
  hospitals: { color: [0, 133, 199], scale: 0.9 },
  police: { color: [26, 26, 26], scale: 0.9 },
  rail: { color: [0, 159, 61], scale: 0.85 },
  bus: { color: [200, 150, 0], scale: 0.5 },
};

function mapZoom() {
  const zoom = state.googleMap?.getZoom?.();
  return Number.isFinite(zoom) ? zoom : 10;
}

function pointPixelRadius(zoom) {
  const px = 22 - (zoom - 10) * 2.6;
  return Math.max(6, Math.min(32, px));
}

const VENUE_PIN_PATH = "M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7z";

function venuePinIcon(selected) {
  return {
    path: VENUE_PIN_PATH,
    fillColor: "#0085C7",
    fillOpacity: 1,
    strokeColor: "#ffffff",
    strokeWeight: selected ? 2 : 1.25,
    scale: selected ? 1.55 : 1.25,
    anchor: new google.maps.Point(12, 22),
  };
}

function crimeRadiusPixels(zoom) {
  const px = 30 - (zoom - 10) * 1.5;
  return Math.max(16, Math.min(34, px));
}

function renderMap() {
  if (!state.map || !state.googleMap) return;
  state.venueMarkers.forEach((marker) => marker.setMap(null));
  state.venueMarkers = [];
  const bounds = [];
  for (const marker of state.map.markers) {
    const selected = marker.venue_id === state.selectedId;
    const visible = state.zone === "all" || marker.olympic_zone === state.zone;
    const pin = new google.maps.Marker({
      map: state.layers.venues ? state.googleMap : null,
      position: { lat: marker.latitude, lng: marker.longitude },
      title: marker.venue_name,
      icon: venuePinIcon(selected),
      opacity: visible ? 1 : 0.35,
      zIndex: selected ? 10 : 1,
    });
    pin.addListener("click", () => selectVenue(marker.venue_id));
    state.venueMarkers.push(pin);
    bounds.push([marker.latitude, marker.longitude]);
  }
  mapPanel.classList.toggle("has-briefing", Boolean(state.selectedId));
  if (!state.selectedId && bounds.length) {
    const mapBounds = new google.maps.LatLngBounds();
    bounds.forEach(([lat, lng]) => mapBounds.extend({ lat, lng }));
    state.googleMap.fitBounds(mapBounds, 28);
  }
  if (state.selectedId) {
    const selected = state.map.markers.find((item) => item.venue_id === state.selectedId);
    if (selected) {
      state.googleMap.panTo({ lat: selected.latitude, lng: selected.longitude });
      state.googleMap.setZoom(13);
    }
  }
  syncBuffer();
  refreshCrimeLayer();
  setTimeout(resizeMap, 50);
}

function setVenueLayerVisible(visible) {
  state.venueMarkers.forEach((marker) => marker.setMap(visible ? state.googleMap : null));
}

function facilityLayerEnabled() {
  return Object.keys(FACILITY_STYLE).some((name) => state.layers[name]);
}

let overlayZoom = null;

function onMapIdle() {
  if (!state.layers.crime && !facilityLayerEnabled()) return;
  const zoomKey = Math.round(mapZoom() * 2) / 2;
  if (zoomKey === overlayZoom) return;
  syncOverlay();
}

function syncBuffer() {
  if (state.bufferCircle) {
    state.bufferCircle.setMap(null);
    state.bufferCircle = null;
  }
  if (!state.selectedId || !state.googleMap || !state.meta || !state.map) return;
  const marker = state.map.markers.find((item) => item.venue_id === state.selectedId);
  if (!marker) return;
  state.bufferCircle = new google.maps.Circle({
    map: state.googleMap,
    center: { lat: marker.latitude, lng: marker.longitude },
    radius: state.meta.buffer_radius_m,
    strokeColor: "#0085C7",
    strokeWeight: 2,
    strokeOpacity: 0.8,
    fillOpacity: 0,
    clickable: false,
  });
}

function facilityLayers() {
  if (!state.facilities || typeof deck === "undefined") return [];
  const zoom = mapZoom();
  const radius = pointPixelRadius(zoom);
  const layers = [];
  for (const [name, style] of Object.entries(FACILITY_STYLE)) {
    if (!state.layers[name]) continue;
    const block = state.facilities[name];
    if (!block?.points?.length) continue;
    const view = state.layerViews[name] || "all";
    const data = block.points.filter((point) => !point.views || point.views.includes(view));
    if (!data.length) continue;
    layers.push(new deck.ScatterplotLayer({
      id: `facility-${name}-${view}`,
      data,
      pickable: true,
      stroked: true,
      radiusUnits: "pixels",
      lineWidthUnits: "pixels",
      getPosition: (point) => [point.longitude, point.latitude],
      getFillColor: style.color,
      getRadius: radius * style.scale,
      getLineColor: [255, 255, 255],
      getLineWidth: zoom < 13 ? 2 : 1,
      updateTriggers: { getRadius: zoom, getLineWidth: zoom < 13 },
    }));
  }
  return layers;
}

function crimeHeatmapLayer() {
  if (!state.layers.crime || !state.crimePoints?.length || typeof deck === "undefined") return null;
  const zoom = mapZoom();
  const radiusPixels = crimeRadiusPixels(zoom);
  state.heatmapLayer = new deck.HeatmapLayer({
    id: "crime-heatmap",
    data: state.crimePoints,
    getPosition: (point) => [point.longitude, point.latitude],
    getWeight: (point) => Math.log(point.weight + 1),
    radiusPixels,
    intensity: 1,
    threshold: zoom < 12 ? 0.02 : 0,
    colorRange: state.crimeHot
      ? [[244, 195, 0], [232, 96, 28], [223, 0, 36], [140, 0, 20]]
      : [[0, 159, 61], [120, 186, 48], [244, 195, 0], [232, 96, 28], [223, 0, 36]],
  });
  return state.heatmapLayer;
}

function syncCrimeLegend() {
  const legend = document.querySelector("#crime-legend");
  if (!legend) return;
  legend.classList.toggle("is-hidden", !state.layers.crime);
  const labels = legend.querySelectorAll("span");
  if (labels.length >= 2) {
    labels[0].textContent = state.crimeHot ? "High" : "Less";
    labels[1].textContent = state.crimeHot ? "Highest" : "More";
  }
}

function fillSelect(select, options, current) {
  if (!select || !options?.length) return;
  select.replaceChildren();
  const groups = new Map();
  for (const option of options) {
    const node = el("option", { value: option.id }, [text(option.label)]);
    if (!option.group) {
      select.append(node);
      continue;
    }
    let group = groups.get(option.group);
    if (!group) {
      group = el("optgroup", { label: option.group });
      groups.set(option.group, group);
      select.append(group);
    }
    group.append(node);
  }
  if ([...select.options].some((option) => option.value === current)) select.value = current;
}

function fillCrimeView(options) {
  const select = document.querySelector("#crime-view");
  fillSelect(select, options, state.crimeView);
  if (select) select.disabled = !state.layers.crime;
}

function fillLayerView(name, options) {
  const select = document.querySelector(`#${name}-view`);
  fillSelect(select, options, state.layerViews[name] || "all");
  if (select) select.disabled = !state.layers[name];
}

function facilityPopup(point) {
  const lines = [`<strong>${escapeHtml(point.name || "Location")}</strong>`];
  if (point.location) lines.push(`<small>${escapeHtml(point.location)}</small>`);
  if (point.detail) lines.push(`<small>${escapeHtml(point.detail)}</small>`);
  return `<div class="venue-popup">${lines.join("")}</div>`;
}

function facilityPoint(info) {
  const point = info?.object;
  if (!point || !String(info.layer?.id || "").startsWith("facility-")) return null;
  return point;
}

function hideFacilityTip() {
  const tip = document.querySelector("#facility-tip");
  if (!tip) return;
  tip.hidden = true;
  state.tipKey = "";
}

function showFacilityTip(info) {
  const tip = document.querySelector("#facility-tip");
  const point = facilityPoint(info);
  if (!tip || !point || info.x == null || info.y == null) {
    hideFacilityTip();
    return;
  }
  const key = `${point.name}|${point.location || ""}|${point.detail || ""}`;
  if (state.tipKey !== key) {
    tip.innerHTML = facilityPopup(point);
    state.tipKey = key;
  }
  tip.hidden = false;
  const frame = tip.parentElement;
  const margin = 8;
  const offset = 14;
  let left = info.x + offset;
  let top = info.y + offset;
  if (left + tip.offsetWidth > frame.clientWidth - margin) left = info.x - tip.offsetWidth - offset;
  if (top + tip.offsetHeight > frame.clientHeight - margin) top = info.y - tip.offsetHeight - offset;
  tip.style.left = `${Math.max(margin, left)}px`;
  tip.style.top = `${Math.max(margin, top)}px`;
}

function syncOverlay() {
  if (!state.heatOverlay) return;
  overlayZoom = Math.round(mapZoom() * 2) / 2;
  syncCrimeLegend();
  const layers = facilityLayers();
  const heatmap = crimeHeatmapLayer();
  if (heatmap) layers.push(heatmap);
  state.heatOverlay.setProps({
    layers,
    getCursor: ({ object }) => (object ? "pointer" : "grab"),
    onHover: (info) => showFacilityTip(info),
    onClick: (info) => {
      const point = facilityPoint(info);
      if (!point) return;
      if (!state.layerInfo) state.layerInfo = new google.maps.InfoWindow();
      state.layerInfo.setContent(facilityPopup(point));
      state.layerInfo.open({
        map: state.googleMap,
        position: { lat: point.latitude, lng: point.longitude },
      });
    },
  });
}

function refreshCrimeLayer() {
  if (!state.googleMap || !state.heatOverlay) return;
  syncOverlay();
}

function bindLayerToggles() {
  document.querySelector("#map-viewport")?.addEventListener("mouseleave", hideFacilityTip);
  document.querySelectorAll("#layer-toggles input[data-layer]").forEach((input) => {
    input.addEventListener("change", () => {
      const name = input.dataset.layer;
      state.layers[name] = input.checked;
      if (name === "venues") setVenueLayerVisible(input.checked);
      else if (name === "crime") {
        const select = document.querySelector("#crime-view");
        if (select) select.disabled = !input.checked;
        refreshCrimeLayer();
      } else {
        const select = document.querySelector(`#${name}-view`);
        if (select) select.disabled = !input.checked;
        hideFacilityTip();
        syncOverlay();
      }
    });
  });
}

async function ensureGoogleMap() {
  if (state.googleMap) return;
  const config = await fetchJson("/api/config");
  if (!config.google_maps_api_key) {
    throw new Error("Google Maps is not configured. Set GOOGLE_MAPS_API_KEY before starting the server.");
  }
  await loadGoogleMaps(config.google_maps_api_key);
  state.googleMap = new google.maps.Map(document.querySelector("#google-map"), {
    center: { lat: 34.05, lng: -118.25 },
    zoom: 10,
    mapTypeControl: false,
    streetViewControl: false,
    fullscreenControl: true,
    clickableIcons: false,
  });
  state.heatOverlay = new deck.GoogleMapsOverlay({ layers: [] });
  state.heatOverlay.setMap(state.googleMap);
  state.googleMap.addListener("idle", onMapIdle);
}

function loadGoogleMaps(apiKey) {
  if (window.google?.maps?.Map) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const callbackName = "safeGamesGoogleMapsReady";
    window[callbackName] = resolve;
    const script = document.createElement("script");
    script.async = true;
    script.defer = true;
    script.src = `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(apiKey)}&loading=async&callback=${callbackName}&v=weekly`;
    script.onerror = () => reject(new Error("Google Maps JavaScript API could not load."));
    document.head.append(script);
  });
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  }[character]));
}

window.addEventListener("resize", () => {
  syncHeaderHeight();
  const narrow = rosterIsNarrow();
  if (narrow !== rosterNarrow) {
    rosterNarrow = narrow;
    setRosterCollapsed(narrow);
  }
  resizeMap();
});

function renderChatContext() {
  const venue = state.venues.find((item) => item.venue_id === state.selectedId);
  if (!venue) {
    chatContext.textContent = "No briefing open. General questions are in scope.";
    analysisLead.hidden = false;
    return;
  }
  analysisLead.hidden = true;
  chatContext.textContent = `Briefing open: ${venue.venue_name}. Questions can be about that venue or about the city as a whole.`;
}

function venueIdFromLocation() {
  const match = location.hash.match(/^#\/venue\/([A-Za-z0-9_-]+)$/);
  return match ? match[1] : null;
}

function rememberVenue(venueId) {
  const next = `#/venue/${venueId}`;
  if (location.hash === next) return;
  history.pushState({ venue: venueId }, "", next);
}

function forgetVenue() {
  if (!location.hash.startsWith("#/venue/")) return;
  history.pushState({}, "", `${location.pathname}${location.search}`);
}

function closeVenuePage(options = {}) {
  if (options.history !== false) forgetVenue();
  detail.hidden = true;
  resizeMap();
}

function openVenuePage(options = {}) {
  const venueId = state.detail?.venue_id || state.selectedId;
  if (!venueId) return;
  if (options.history !== false) rememberVenue(venueId);
  if (!state.detail) {
    detail.hidden = false;
    detail.replaceChildren(el("div", { className: "venue-sheet" }, [
      el("p", { className: "empty-detail" }, [text("Loading briefing…")]),
    ]));
    return;
  }
  renderDetail();
}

function clearSelection(options = {}) {
  requestToken += 1;
  closeVenuePage(options);
  state.selectedId = null;
  state.detail = null;
  overview.hidden = true;
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

const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function monthLabel(key) {
  const [year, month] = key.split("-");
  return `${MONTH_NAMES[Number(month) - 1]} ${year}`;
}

function monthChart(entries, peakKey, onFocus) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "chart");
  svg.setAttribute("viewBox", "0 0 960 200");
  svg.setAttribute("role", "img");
  if (!entries.length) {
    svg.setAttribute("aria-label", "No dated incidents in this buffer");
    return svg;
  }
  const max = Math.max(...entries.map((entry) => entry.count), 1);
  const peak = entries.find((entry) => entry.key === peakKey) || entries[0];
  svg.setAttribute(
    "aria-label",
    `Monthly incidents from ${monthLabel(entries[0].key)} to ${monthLabel(entries[entries.length - 1].key)}. Peak ${monthLabel(peak.key)} with ${formatNumber(peak.count)}.`,
  );
  const width = 960 / entries.length;
  entries.forEach((entry, index) => {
    const height = Math.max((entry.count / max) * 168, entry.count ? 2 : 0);
    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    rect.setAttribute("x", String(index * width + 0.6));
    rect.setAttribute("y", String(176 - height));
    rect.setAttribute("width", String(Math.max(width - 1.2, 0.6)));
    rect.setAttribute("height", String(height));
    rect.setAttribute("fill", entry.key === peakKey ? "#DF0024" : "#0085C7");
    rect.dataset.month = entry.key;
    rect.addEventListener("pointerenter", () => onFocus(entry.key));
    svg.append(rect);
    if (entry.key.endsWith("-01")) {
      const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
      label.setAttribute("x", String(index * width));
      label.setAttribute("y", "194");
      label.setAttribute("fill", "#4e5963");
      label.setAttribute("font-size", "11");
      label.textContent = entry.key.slice(0, 4);
      svg.append(label);
    }
  });
  return svg;
}

function categoryRows(byCategory, total, options = {}) {
  let entries = Object.entries(byCategory).sort((a, b) => b[1] - a[1]);
  if (options.limit) entries = entries.slice(0, options.limit);
  const max = entries.length ? entries[0][1] : 1;
  return entries.map(([category, count]) => {
    const share = total ? Math.round((count / total) * 100) : 0;
    const countLabel = options.share === false ? formatNumber(count) : `${formatNumber(count)} · ${share}%`;
    return el("div", { className: "bar-row" }, [
      el("div", {}, [
        el("div", { className: "bar-label", title: category }, [text(category)]),
        el("div", { className: "bar-track" }, [
          el("div", { className: "bar-fill", style: `width: ${(count / max) * 100}%` }),
        ]),
      ]),
      el("div", { className: "bar-count" }, [text(countLabel)]),
    ]);
  });
}

function overviewChart(byMonth) {
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
    rect.setAttribute("fill", entry.key === peak.key ? "#DF0024" : "#0085C7");
    svg.append(rect);
  });
  return svg;
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

function yearTotals(entries) {
  const totals = new Map();
  for (const entry of entries) {
    const year = entry.key.slice(0, 4);
    totals.set(year, (totals.get(year) || 0) + entry.count);
  }
  return [...totals.entries()];
}

function monthTable(entries, peakKey) {
  const byYear = new Map();
  for (const entry of entries) {
    const [year, month] = entry.key.split("-");
    if (!byYear.has(year)) byYear.set(year, Array(12).fill(null));
    byYear.get(year)[Number(month) - 1] = entry;
  }
  const head = el("tr", {}, [
    el("th", {}, [text("Year")]),
    ...MONTH_NAMES.map((name) => el("th", {}, [text(name)])),
  ]);
  const rows = [...byYear.entries()].map(([year, months]) => el("tr", {}, [
    el("th", {}, [text(year)]),
    ...months.map((entry) => el("td", {
      className: entry && entry.key === peakKey ? "is-peak" : "",
      "data-month": entry ? entry.key : "",
    }, [text(entry ? formatNumber(entry.count) : "")])),
  ]));
  return el("table", { className: "month-table" }, [
    el("thead", {}, [head]),
    el("tbody", {}, rows),
  ]);
}

function renderOverview() {
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
    el("div", { className: "station-grid" }, [
      stationCard("Fire", venue.nearest_fire_station),
      stationCard("Police", venue.nearest_police_station),
      stationCard("Hospital", venue.nearest_hospital),
    ]),
    el("p", { className: "footnote" }, [text(state.meta?.jurisdiction_method || "")]),
  );
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
  const years = yearTotals(months);
  const peakYear = years.reduce((best, entry) => (entry[1] > best[1] ? entry : best), years[0] || ["", 0]);
  const total = venue.crime_count_nearby;
  const categories = categoryRows(venue.crime_by_category, total);
  const flags = venue.data_quality_flags.map((flag) => (
    el("span", { className: "flag" }, [text(FLAG_LABELS[flag] || flag)])
  ));
  const rail = venue.rail_stations_nearby.stations.map((station) => (
    el("li", {}, [
      el("strong", {}, [text(station.station_name)]),
      text(` · ${station.lines} · ${formatDistance(station.distance_m)}`),
    ])
  ));
  const buses = venue.bus_stops_nearby.lines.map((line) => (
    el("span", { className: "pill" }, [text(line)])
  ));
  const readout = el("p", { className: "month-readout" }, [
    text(peak ? `${monthLabel(peak.key)} · ${formatNumber(peak.count)} incidents` : "No dated incidents."),
  ]);

  function focusMonth(key) {
    const entry = months.find((item) => item.key === key);
    if (!entry) return;
    readout.textContent = `${monthLabel(key)} · ${formatNumber(entry.count)} incidents`;
    detail.querySelectorAll("[data-month]").forEach((node) => {
      const active = node.dataset.month === key;
      node.classList.toggle("is-active", active);
      if (node.tagName === "rect") {
        node.setAttribute("fill", node.dataset.month === peak?.key ? "#DF0024" : (active ? "#1a1a1a" : "#0085C7"));
      }
    });
  }

  const chart = monthChart(months, peak?.key, focusMonth);
  const table = monthTable(months, peak?.key);
  table.addEventListener("pointerover", (event) => {
    const cell = event.target.closest("[data-month]");
    if (cell?.dataset.month) focusMonth(cell.dataset.month);
  });

  detail.hidden = false;
  detail.scrollTop = 0;
  detail.replaceChildren(el("div", { className: "venue-sheet" }, [
    el("button", { className: "venue-back", type: "button", "data-close-briefing": "true" }, [text("Back to map")]),
    el("p", { className: "zone" }, [text(`${venue.olympic_zone} · ${formatSports(venue.sports)}`)]),
    el("h2", { className: "detail-title", id: "detail-heading" }, [text(venue.venue_name)]),
    el("p", { className: "address" }, [text(`${venue.address}, ${venue.city}`)]),
    el("div", { className: "stats" }, [
      stat("Incidents", formatNumber(total), "inside the buffer, 2020–2024"),
      stat("Density", formatNumber(venue.crime_per_km2), "per km²"),
      stat("Busiest month", peak ? monthLabel(peak.key) : "None", peak ? `${formatNumber(peak.count)} incidents` : "No dated incidents"),
      stat("Buffer", `${Math.round(venue.buffer_radius_m)} m`, jurisdictionLabel(venue.lapd_jurisdiction)),
    ]),
    flags.length ? el("div", { className: "flags" }, flags) : el("span"),
    el("h3", { className: "section-title" }, [text("Incidents by month")]),
    el("p", { className: "muted" }, [text("LAPD reports inside the buffer. Red is the busiest month. Move across a bar or a cell to read that month.")]),
    readout,
    years.length ? el("div", { className: "year-row" }, years.map(([year, count]) => (
      el("article", { className: year === peakYear[0] ? "year-card is-peak" : "year-card" }, [
        el("span", {}, [text(year)]),
        el("strong", {}, [text(formatNumber(count))]),
      ])
    ))) : el("span"),
    chart,
    el("div", { className: "month-table-wrap" }, [table]),
    el("h3", { className: "section-title" }, [text(`Incident types · ${formatNumber(categories.length)}`)]),
    el("div", { className: "category-list" }, categories.length
      ? categories
      : [el("p", { className: "muted" }, [text("No incidents in this buffer.")])]),
    el("h3", { className: "section-title" }, [text(`Rail · ${formatNumber(venue.rail_stations_nearby.count)} ${venue.rail_stations_nearby.count === 1 ? "station" : "stations"}`)]),
    rail.length ? el("ul", { className: "place-list" }, rail) : el("p", { className: "muted" }, [text("None in the buffer.")]),
    el("h3", { className: "section-title" }, [
      text(`Bus · ${formatNumber(venue.bus_stops_nearby.count)} stops · ${venue.bus_stops_nearby.lines.length} lines`),
    ]),
    el("div", { className: "pills" }, buses.length ? buses : [el("span", { className: "muted" }, [text("None in the buffer.")])]),
    el("h3", { className: "section-title" }, [text("Nearest response and care")]),
    el("div", { className: "station-grid" }, [
      stationCard("Fire", venue.nearest_fire_station),
      stationCard("Police", venue.nearest_police_station),
      stationCard("Hospital", venue.nearest_hospital),
    ]),
    el("p", { className: "footnote" }, [text(state.meta.jurisdiction_method)]),
  ]));
  if (peak) focusMonth(peak.key);
}

function stat(label, value, note) {
  return el("article", { className: "stat" }, [
    el("span", {}, [text(label)]),
    el("strong", {}, [text(value)]),
    el("em", {}, [text(note)]),
  ]);
}

async function selectVenue(venueId, options = {}) {
  const expand = Boolean(options.expand);
  if (state.detail?.venue_id === venueId) {
    state.selectedId = venueId;
    renderOverview();
    renderRoster();
    renderChatContext();
    if (expand) openVenuePage({ history: options.history !== false });
    return;
  }
  if (!expand) closeVenuePage({ history: false });
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
  renderOverview();
  try {
    const venue = await fetchJson(`/api/venues/${encodeURIComponent(venueId)}`);
    if (token !== requestToken) return;
    state.detail = venue;
    renderOverview();
    if (expand || !detail.hidden) openVenuePage({ history: options.history !== false });
  } catch (error) {
    if (token !== requestToken) return;
    overview.replaceChildren(
      el("div", { className: "detail-head" }, [
        el("h2", { className: "detail-title", id: "overview-heading" }, [text("Briefing")]),
        el("button", { className: "close-briefing", type: "button", "data-close-briefing": "true" }, [text("Close")]),
      ]),
      el("p", { className: "empty-detail" }, [text(error.message)]),
    );
  }
}

let crimeRequest = 0;

function bindFacilityViews() {
  for (const name of Object.keys(state.layerViews)) {
    const select = document.querySelector(`#${name}-view`);
    if (!select) continue;
    select.addEventListener("change", () => {
      state.layerViews[name] = select.value;
      if (state.layers[name]) syncOverlay();
    });
  }
}

function bindCrimeView() {
  const select = document.querySelector("#crime-view");
  if (!select) return;
  select.addEventListener("change", () => {
    state.crimeView = select.value;
    loadCrimeHeat(select.value);
  });
}

async function loadCrimeHeat(view = state.crimeView || "all") {
  const crimeToggle = document.querySelector('#layer-toggles input[data-layer="crime"]');
  const token = ++crimeRequest;
  state.crimeView = view;
  const cached = state.crimeCache[view];
  if (cached) {
    state.crimePoints = cached.points;
    state.crimeHot = Boolean(cached.hot);
    fillCrimeView(cached.options);
    if (state.layers.crime) refreshCrimeLayer();
    return;
  }
  try {
    const crimeHeat = await fetchJson(`/api/map/crime?view=${encodeURIComponent(view)}`);
    if (token !== crimeRequest) return;
    state.crimeCache[view] = crimeHeat;
    state.crimePoints = crimeHeat.points;
    state.crimeHot = Boolean(crimeHeat.hot);
    fillCrimeView(crimeHeat.options);
    if (state.layers.crime) refreshCrimeLayer();
  } catch (error) {
    if (token !== crimeRequest) return;
    if (view !== "all") {
      if (!mapHint.hidden) mapHint.textContent = `Could not load that crime view: ${error.message}`;
      return;
    }
    state.layers.crime = false;
    state.crimePoints = null;
    if (crimeToggle) {
      crimeToggle.checked = false;
      crimeToggle.disabled = true;
    }
    const select = document.querySelector("#crime-view");
    if (select) select.disabled = true;
    if (!mapHint.hidden) mapHint.textContent = `Could not load city crime: ${error.message}`;
    refreshCrimeLayer();
  }
}

async function init() {
  bindLayerToggles();
  bindCrimeView();
  bindFacilityViews();
  try {
    const [metaData, list, mapData, facilities] = await Promise.all([
      fetchJson("/api/meta"),
      fetchJson("/api/venues"),
      fetchJson("/api/map"),
      fetchJson("/api/map/layers").catch(() => null),
    ]);
    state.meta = metaData;
    state.venues = list.venues;
    state.map = mapData;
    state.facilities = facilities;
    if (facilities) {
      for (const name of Object.keys(state.layerViews)) {
        fillLayerView(name, facilities[name]?.options);
      }
    }
    if (!facilities) {
      document.querySelectorAll("#layer-toggles input[data-layer]").forEach((input) => {
        if (input.dataset.layer !== "venues" && input.dataset.layer !== "crime") input.disabled = true;
      });
    }
    renderMeta();
    renderZoneOptions();
    renderRoster();
    renderChatContext();
    try {
      await ensureGoogleMap();
      renderMap();
    } catch (error) {
      mapHint.textContent = error.message;
      mapHint.style.pointerEvents = "auto";
    }
    loadCrimeHeat();
  } catch (error) {
    if (metaStrip) metaStrip.textContent = error.message;
    if (mapHint) {
      mapHint.hidden = false;
      mapHint.textContent = error.message;
    }
  }
  const routed = venueIdFromLocation();
  if (routed) selectVenue(routed, { expand: true, history: false });
}

window.addEventListener("popstate", () => {
  const routed = venueIdFromLocation();
  if (routed) selectVenue(routed, { expand: true, history: false });
  else closeVenuePage({ history: false });
});

init();
