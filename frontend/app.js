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
const comparePage = document.querySelector("#compare-page");
const mapFrame = document.querySelector("#map-frame");
const mapPanel = document.querySelector(".map-panel");
const mapHint = document.querySelector("#map-hint");
const DEFAULT_CRIME_HINT = "Pins mark venues. Crime is LAPD reports from 2020 to 2024 across the city, green where fewer and red where they concentrate.";
const NIBRS_CRIME_HINT = "Pins mark venues. This heatmap is NIBRS offenses from March 2024 through the latest extract. A case can contribute more than one point. Green is fewer and red is where they concentrate.";
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
  if (open) document.querySelector("#chat-input").focus();
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
  if (comparePage && !comparePage.hidden) {
    closeCompare();
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
    .map((part) => part.replace(
      /(^|[^A-Za-z0-9])([a-z])/g,
      (_, boundary, letter) => boundary + letter.toUpperCase(),
    ))
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
      venue.nibrs_count == null ? el("span") : el("div", { className: "card-foot" }, [
        text(`NIBRS · ${formatNumber(venue.nibrs_count)} offenses`),
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
  fire: { color: [128, 0, 0], scale: 1 },
  hospitals: { color: [0, 133, 199], scale: 1 },
  police: { color: [26, 26, 26], scale: 1 },
  rail: { color: [0, 159, 61], scale: 1 },
  bus: { color: [200, 150, 0], scale: 1 },
};

function mapZoom() {
  const zoom = state.googleMap?.getZoom?.();
  return Number.isFinite(zoom) ? zoom : 10;
}

const VENUE_PIN_SCALE = 1.25;
const VENUE_PIN_HEIGHT_UNITS = 20;

function pointPixelRadius() {
  const venueHeight = VENUE_PIN_HEIGHT_UNITS * VENUE_PIN_SCALE;
  return (venueHeight * 0.5) / 2;
}

const VENUE_PIN_PATH = "M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7z";

function venuePinIcon(selected) {
  return {
    path: VENUE_PIN_PATH,
    fillColor: "#7B2CBF",
    fillOpacity: 1,
    strokeColor: "#ffffff",
    strokeWeight: selected ? 2 : 1.25,
    scale: selected ? 1.55 : VENUE_PIN_SCALE,
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
  const radius = pointPixelRadius();
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
      parameters: { depthTest: false },
      radiusUnits: "pixels",
      lineWidthUnits: "pixels",
      getPosition: (point) => [point.longitude, point.latitude],
      getFillColor: style.color,
      getRadius: radius * style.scale,
      getLineColor: [255, 255, 255],
      getLineWidth: 1,
      updateTriggers: { getRadius: radius },
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
  const layers = [];
  const heatmap = crimeHeatmapLayer();
  if (heatmap) layers.push(heatmap);
  layers.push(...facilityLayers());
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
  window.dispatchEvent(new CustomEvent("venue-context", { detail: venue || null }));
  if (!venue) {
    chatContext.textContent = "No venue selected. Include a venue name in your question.";
    analysisLead.hidden = false;
    return;
  }
  analysisLead.hidden = true;
  chatContext.textContent = `“This venue” refers to ${venue.venue_name}.`;
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

function venueToolbar() {
  return el("div", { className: "venue-toolbar" }, [
    el("button", { className: "venue-back", type: "button", "data-close-briefing": "true" }, [text("Back to map")]),
    el("button", { className: "compare-link", type: "button" }, [text("Compare")]),
  ]);
}

function openVenuePage(options = {}) {
  if (comparePage) comparePage.hidden = true;
  const venueId = state.detail?.venue_id || state.selectedId;
  if (!venueId) return;
  if (options.history !== false) rememberVenue(venueId);
  if (!state.detail) {
    detail.hidden = false;
    detail.replaceChildren(el("div", { className: "venue-sheet" }, [
      venueToolbar(),
      el("p", { className: "empty-detail" }, [text("Loading briefing…")]),
    ]));
    syncCompareLink();
    return;
  }
  renderDetail();
  syncCompareLink();
}

function clearSelection(options = {}) {
  requestToken += 1;
  closeVenuePage(options);
  state.selectedId = null;
  state.detail = null;
  overview.hidden = true;
  mapHint.hidden = false;
  syncCrimeHint();
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
const MONTH_FULL = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

const PERMIT_HINTS = {
  "Permit days vs other days": "A permit day is a day covered by a temporary-event permit. Other days are the rest of the months that had a permit, from 2020 through 2024. Months with no permit are left out.",
  "Permit days": "Days covered by at least one temporary-event permit. Several permits on the same date still count as one day.",
  "Other days": "The other days in months that had a permit. These are not every day without an event.",
  "Permit-day mean": "Average reports on permit days.",
  "Other-day mean": "Average reports on other days.",
  "Median": "The middle daily count on permit days, then the middle count on other days.",
  "Difference": "Permit-day mean minus the other-day mean. A percent is shown when there are at least 8 permit days, the daily gap is at least 0.05, and the gap is unlikely to be chance.",
  "Overall": "All comparison months from 2020 through 2024, taken together.",
  "Offense groups": "Crime groups, such as theft or assault, where the daily average on permit days differs from other days by at least 0.05 reports. The bar length is the size of that gap.",
  "Permits on the same day": "Permit days split by how many permits cover the date. One permit and several permits are each compared with other days.",
  "One permit": "Days covered by a single permit.",
  "Several permits": "Days covered by two or more permits. Those dates are still one permit day.",
  "Days": "How many dates are in this row.",
  "Reports per day": "Average reports on the dates in this row.",
  "Offenses per day": "Average NIBRS offenses on the dates in this row. One case can include more than one offense.",
  "Difference vs other days": "This row's average minus the average on other days. Other days is the baseline, so that row has no difference.",
  "Home games vs other days": "Completed Dodgers regular-season home games, 2020 through 2024, compared with the other days in March through October of those years.",
  "Game days": "Days with a completed regular-season home game. A doubleheader still counts as one day.",
  "Other days in season": "The other days in March through October, 2020 through 2024. Winter days are left out.",
  "Game-day mean": "Average reports on home-game days.",
  "Other-day mean in season": "Average reports on the other days in those baseball months.",
  "2020–2024": "All completed regular-season home games from 2020 through 2024, taken together.",
  "Game-day groups": "Crime groups where the daily average on home-game days differs from other days in season by at least 0.05 reports. The bar length is the size of that gap.",
  "Listed events": "Ticketmaster listings matched to this venue. The dates are after 2024, so they are not joined to the crime reports.",
  "Month": "Calendar month of the listings. A month with no listing is still shown, so the gap stays visible.",
  "Listings": "How many Ticketmaster events fall in this month.",
  "Event": "The name Ticketmaster published for this listing.",
  "Start": "Local start time when Ticketmaster published one. Not listed means the hour was left off.",
  "Tickets": "On sale means tickets are listed for sale. Not on sale yet is not a cancellation.",
};

let infoTip = null;
let infoAnchor = null;
let infoPinned = false;
let infoHideTimer = 0;
let chartTip = null;

function monthLabel(key) {
  const [year, month] = key.split("-");
  return `${MONTH_NAMES[Number(month) - 1]} ${year}`;
}

function monthTitle(key) {
  const [year, month] = key.split("-");
  return `${MONTH_FULL[Number(month) - 1]} ${year}`;
}

function ensureInfoTip() {
  if (infoTip) return infoTip;
  infoTip = el("div", { className: "info-tip", role: "tooltip", hidden: "hidden" });
  document.body.append(infoTip);
  infoTip.addEventListener("mouseenter", () => window.clearTimeout(infoHideTimer));
  infoTip.addEventListener("mouseleave", () => {
    if (!infoPinned) scheduleInfoHide();
  });
  document.addEventListener("click", (event) => {
    if (!infoTip || infoTip.hidden) return;
    if (event.target.closest(".info-mark, .info-tip")) return;
    hideInfoTip(true);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") hideInfoTip(true);
  });
  window.addEventListener("scroll", () => {
    hideInfoTip(true);
    hideChartTip();
  }, true);
  return infoTip;
}

function placeInfoTip(anchor) {
  const tip = ensureInfoTip();
  const rect = anchor.getBoundingClientRect();
  const margin = 8;
  tip.hidden = false;
  const width = tip.offsetWidth;
  const height = tip.offsetHeight;
  let left = rect.left + (rect.width / 2) - (width / 2);
  let top = rect.bottom + margin;
  if (left + width > window.innerWidth - margin) left = window.innerWidth - width - margin;
  if (left < margin) left = margin;
  if (top + height > window.innerHeight - margin) top = Math.max(margin, rect.top - height - margin);
  tip.style.left = `${Math.round(left)}px`;
  tip.style.top = `${Math.round(top)}px`;
}

function showInfoTip(anchor, message, pinned) {
  window.clearTimeout(infoHideTimer);
  const tip = ensureInfoTip();
  if (infoAnchor && infoAnchor !== anchor) infoAnchor.setAttribute("aria-expanded", "false");
  infoAnchor = anchor;
  infoPinned = pinned;
  tip.textContent = message;
  anchor.setAttribute("aria-expanded", "true");
  placeInfoTip(anchor);
}

function hideInfoTip(force) {
  window.clearTimeout(infoHideTimer);
  if (!infoTip || infoTip.hidden) return;
  if (infoPinned && !force) return;
  infoTip.hidden = true;
  if (infoAnchor) infoAnchor.setAttribute("aria-expanded", "false");
  infoAnchor = null;
  infoPinned = false;
}

function scheduleInfoHide() {
  window.clearTimeout(infoHideTimer);
  infoHideTimer = window.setTimeout(() => hideInfoTip(false), 160);
}

function infoMark(message) {
  const button = el("button", {
    type: "button",
    className: "info-mark",
    "aria-label": "What this means",
    "aria-expanded": "false",
  }, [text("i")]);
  button.addEventListener("mouseenter", () => showInfoTip(button, message, infoAnchor === button && infoPinned));
  button.addEventListener("mouseleave", () => {
    if (infoAnchor === button && !infoPinned) scheduleInfoHide();
  });
  button.addEventListener("focus", () => showInfoTip(button, message, false));
  button.addEventListener("blur", () => {
    if (infoAnchor === button && !infoPinned) scheduleInfoHide();
  });
  button.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (infoAnchor === button && infoPinned) hideInfoTip(true);
    else showInfoTip(button, message, true);
  });
  return button;
}

function withInfo(label) {
  const hint = PERMIT_HINTS[label];
  if (!hint) return text(label);
  return el("span", { className: "info-label" }, [text(label), infoMark(hint)]);
}

function ensureChartTip() {
  if (chartTip) return chartTip;
  chartTip = el("div", { className: "chart-tip", hidden: "hidden" });
  document.body.append(chartTip);
  return chartTip;
}

function placeChartTip(event) {
  const tip = ensureChartTip();
  tip.hidden = false;
  const margin = 8;
  const width = tip.offsetWidth;
  const height = tip.offsetHeight;
  let left = event.clientX + 14;
  let top = event.clientY - height - 12;
  if (left + width > window.innerWidth - margin) left = event.clientX - width - 14;
  if (left < margin) left = margin;
  if (top < margin) top = event.clientY + 16;
  tip.style.left = `${Math.round(left)}px`;
  tip.style.top = `${Math.round(top)}px`;
}

function showChartTip(event, key, count, word) {
  const tip = ensureChartTip();
  tip.replaceChildren(
    el("strong", {}, [text(monthTitle(key))]),
    el("span", {}, [text(`${formatNumber(count)} ${word}`)]),
  );
  placeChartTip(event);
}

function hideChartTip() {
  if (chartTip) chartTip.hidden = true;
}

function bindChartHover(svg, entry, word) {
  const show = (event) => showChartTip(event, entry.key, entry.count, word);
  svg.addEventListener("mouseenter", show);
  svg.addEventListener("mousemove", (event) => {
    if (!chartTip || chartTip.hidden) show(event);
    else placeChartTip(event);
  });
}

function monthChart(entries, peakKey, onFocus, word = "incidents") {
  hideChartTip();
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
    const x = index * width + 0.6;
    const barWidth = Math.max(width - 1.2, 0.6);
    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    rect.setAttribute("x", String(x));
    rect.setAttribute("y", String(176 - height));
    rect.setAttribute("width", String(barWidth));
    rect.setAttribute("height", String(height));
    rect.setAttribute("fill", entry.key === peakKey ? "#DF0024" : "#0085C7");
    rect.dataset.month = entry.key;
    svg.append(rect);
    const hit = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    hit.setAttribute("x", String(x));
    hit.setAttribute("y", "0");
    hit.setAttribute("width", String(barWidth));
    hit.setAttribute("height", "176");
    hit.setAttribute("fill", "transparent");
    hit.addEventListener("click", () => onFocus(entry.key));
    bindChartHover(hit, entry, word);
    svg.append(hit);
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
  svg.addEventListener("mouseleave", hideChartTip);
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
  hideChartTip();
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
    const x = index * width + 0.4;
    const barWidth = Math.max(width - 0.8, 0.4);
    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    rect.setAttribute("x", String(x));
    rect.setAttribute("y", String(120 - height));
    rect.setAttribute("width", String(barWidth));
    rect.setAttribute("height", String(height));
    rect.setAttribute("fill", entry.key === peak.key ? "#DF0024" : "#0085C7");
    svg.append(rect);
    const hit = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    hit.setAttribute("x", String(x));
    hit.setAttribute("y", "0");
    hit.setAttribute("width", String(barWidth));
    hit.setAttribute("height", "120");
    hit.setAttribute("fill", "transparent");
    bindChartHover(hit, entry, "incidents");
    svg.append(hit);
  });
  svg.addEventListener("mouseleave", hideChartTip);
  return svg;
}

function ordinal(value) {
  const number = Number(value);
  const mod100 = number % 100;
  if (mod100 >= 11 && mod100 <= 13) return `${number}th`;
  return `${number}${["th", "st", "nd", "rd"][number % 10] || "th"}`;
}

function densityNote(venue) {
  const rank = venue.present?.density_rank || venue.density_rank;
  if (!rank) return "per km²";
  const place = `${rank.tied ? "tied for " : ""}${ordinal(rank.rank)} of ${rank.of}`;
  return `${place} · per km²`;
}

function stat(label, value, note, hint) {
  const title = hint
    ? el("span", { className: "info-label" }, [text(label), infoMark(hint)])
    : el("span", {}, [text(label)]);
  return el("article", { className: "stat" }, [
    title,
    el("strong", {}, [text(value)]),
    el("em", {}, [text(note)]),
  ]);
}

const DENSITY_HINT = "Records per square kilometer inside the 800 m circle, from 2020 through the latest data. Reports run through March 6, 2024, then NIBRS offenses, and one NIBRS case can count more than once. The circle covers about 2.01 km², so density is the count divided by that area. The place compares this venue with the other 13.";

function presentCount(venue) {
  return venue.present?.count ?? venue.crime_count_nearby;
}

function presentRate(venue) {
  return venue.present?.crime_per_km2 ?? venue.crime_per_km2;
}

function cityStats(venue) {
  const series = venue?.city_baseline?.present;
  if (!series?.value) return [];
  return [stat("Vs city", series.value, series.caption, series.hint)];
}

function overlapNote(venue) {
  const others = venue.overlapping_venues || [];
  if (!others.length) return "";
  const names = others.map((item) => item.venue_name);
  const list = names.length === 1
    ? names[0]
    : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
  return `The 800 m circle overlaps ${list}. An incident in the overlap is counted for each venue.`;
}

function hospitalHasEmergencyRoom(station) {
  return String(station?.emergency_room || "").toLowerCase() === "yes";
}

function careStations(venue) {
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

function yearTotals(entries) {
  const totals = new Map();
  for (const entry of entries) {
    const year = entry.key.slice(0, 4);
    totals.set(year, (totals.get(year) || 0) + entry.count);
  }
  return [...totals.entries()];
}

function monthTable(entries, peakKey, activeKey, fromMonth, toMonth) {
  const indexes = [];
  for (let month = fromMonth; month <= toMonth; month += 1) indexes.push(month);
  const byYear = new Map();
  for (const entry of entries) {
    const [year, month] = entry.key.split("-");
    const monthNumber = Number(month);
    if (monthNumber < fromMonth || monthNumber > toMonth) continue;
    if (!byYear.has(year)) byYear.set(year, Array(12).fill(null));
    byYear.get(year)[monthNumber - 1] = entry;
  }
  const head = el("tr", {}, [
    el("th", {}, [text("Year")]),
    ...indexes.map((month) => el("th", {}, [text(MONTH_NAMES[month - 1])])),
  ]);
  const rows = [...byYear.entries()].map(([year, months]) => el("tr", {}, [
    el("th", {}, [text(year)]),
    ...indexes.map((month) => {
      const entry = months[month - 1];
      const classes = [];
      if (entry && entry.key === peakKey) classes.push("is-peak");
      if (entry && entry.key === activeKey) classes.push("is-active");
      return el("td", {
        className: classes.join(" "),
        "data-month": entry ? entry.key : "",
      }, [text(entry ? formatNumber(entry.count) : "")]);
    }),
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
      stat("Incidents", formatNumber(presentCount(venue)), "inside the buffer, 2020–present"),
      stat("Density", formatNumber(presentRate(venue)), densityNote(venue), DENSITY_HINT),
      ...cityStats(venue),
      stat("Jurisdiction", jurisdictionLabel(venue.lapd_jurisdiction), "nearest local station"),
    ]),
    venue.nibrs ? el("p", { className: "muted" }, [
      text(`NIBRS offenses since Mar 2024: ${formatNumber(venue.nibrs.count)}. A case can include more than one offense.`),
    ]) : el("span"),
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

function formatMean(value) {
  return Number(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatMedian(value) {
  const number = Number(value);
  return Number.isInteger(number) ? String(number) : number.toFixed(1);
}

function formatGap(summary) {
  if (summary.absolute_difference == null) return "—";
  const difference = Number(summary.absolute_difference);
  const signed = `${difference > 0 ? "+" : ""}${difference.toFixed(2)} / day`;
  if (!summary.percent_shown || summary.lift_pct == null) return signed;
  const lift = Number(summary.lift_pct);
  return `${signed} · ${lift > 0 ? "+" : ""}${lift.toFixed(1)}%`;
}

function formatMaybeMean(value) {
  if (value == null) return "—";
  return formatMean(value);
}

function formatPermitSpan(start, end) {
  const startDate = new Date(`${start}T00:00:00`);
  const endDate = new Date(`${end}T00:00:00`);
  const startLabel = `${MONTH_NAMES[startDate.getMonth()]} ${startDate.getDate()}, ${startDate.getFullYear()}`;
  if (start === end) return startLabel;
  if (startDate.getFullYear() === endDate.getFullYear() && startDate.getMonth() === endDate.getMonth()) {
    return `${MONTH_NAMES[startDate.getMonth()]} ${startDate.getDate()}–${endDate.getDate()}, ${startDate.getFullYear()}`;
  }
  const endLabel = `${MONTH_NAMES[endDate.getMonth()]} ${endDate.getDate()}, ${endDate.getFullYear()}`;
  return `${startLabel} – ${endLabel}`;
}

function permitTable(rows) {
  const labels = ["", "Permit days", "Other days", "Permit-day mean", "Other-day mean", "Median", "Difference"];
  const bodyRows = rows.map((row, index) => el("tr", { className: index === 0 ? "is-overall" : "" }, [
    el("th", { scope: "row" }, [withInfo(row.label)]),
    el("td", {}, [text(formatNumber(row.event_day_count))]),
    el("td", {}, [text(formatNumber(row.other_day_count))]),
    el("td", {}, [text(formatMean(row.event_day_mean))]),
    el("td", {}, [text(formatMean(row.other_day_mean))]),
    el("td", {}, [text(`${formatMedian(row.event_day_median)} vs ${formatMedian(row.other_day_median)}`)]),
    el("td", {}, [text(formatGap(row))]),
  ]));
  return el("div", { className: "month-table-wrap" }, [
    el("table", { className: "month-table permit-table" }, [
      el("thead", {}, [el("tr", {}, labels.map((label) => el("th", {}, [withInfo(label)])))]),
      el("tbody", {}, bodyRows),
    ]),
  ]);
}

function groupRows(groups, eventLabel = "Permit-day mean") {
  const max = groups.reduce(
    (largest, group) => Math.max(largest, Math.abs(Number(group.absolute_difference) || 0)),
    0,
  ) || 1;
  return groups.map((group) => {
    const magnitude = Math.abs(Number(group.absolute_difference) || 0);
    const means = `${eventLabel} ${formatMean(group.event_day_mean)}, other-day mean ${formatMean(group.other_day_mean)}`;
    return el("div", { className: "bar-row" }, [
      el("div", {}, [
        el("div", { className: "bar-label", title: means }, [text(group.label)]),
        el("div", { className: "bar-track" }, [
          el("div", { className: "bar-fill", style: `width: ${(magnitude / max) * 100}%` }),
        ]),
      ]),
      el("div", { className: "bar-count" }, [text(formatGap(group))]),
    ]);
  });
}

function permitLoadTable(rows, unit) {
  const labels = ["", "Days", unit === "offenses" ? "Offenses per day" : "Reports per day", "Median", "Difference vs other days"];
  return el("div", { className: "month-table-wrap" }, [
    el("table", { className: "month-table permit-table" }, [
      el("thead", {}, [el("tr", {}, labels.map((label) => el("th", {}, [withInfo(label)])))]),
      el("tbody", {}, rows.map((row) => el("tr", {}, [
        el("th", { scope: "row" }, [withInfo(row.label)]),
        el("td", {}, [text(formatNumber(row.day_count))]),
        el("td", {}, [text(formatMaybeMean(row.mean))]),
        el("td", {}, [text(row.median == null ? "—" : formatMedian(row.median))]),
        el("td", {}, [text(row.label === "Other days" ? "—" : formatGap(row))]),
      ]))),
    ]),
  ]);
}

function formatClock(value) {
  if (!value) return "Not listed";
  const [hourText, minuteText] = value.split(":");
  let hour = Number(hourText);
  const minute = minuteText || "00";
  const suffix = hour >= 12 ? "pm" : "am";
  hour = hour % 12 || 12;
  const minutes = minute === "00" ? "00" : minute;
  return `${hour}:${minutes} ${suffix}`;
}

function todayIso() {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

function listingMonthTable(months, selected, onPick) {
  const peak = months.reduce((best, month) => Math.max(best, month.count), 0);
  return el("div", { className: "month-table-wrap" }, [
    el("table", { className: "month-table listing-months" }, [
      el("thead", {}, [el("tr", {}, ["Month", "Listings"].map((label) => el("th", {}, [withInfo(label)])))]),
      el("tbody", {}, months.map((month) => {
        const pressed = month.month === selected;
        const row = el("tr", { className: [
          month.count && month.count === peak ? "is-overall" : "",
          pressed ? "is-selected" : "",
        ].filter(Boolean).join(" ") }, [
          el("th", { scope: "row" }, [
            el("button", {
              type: "button",
              className: "listing-month",
              "aria-pressed": pressed ? "true" : "false",
            }, [text(month.label)]),
          ]),
          el("td", {}, [text(formatNumber(month.count))]),
        ]);
        row.addEventListener("click", () => onPick(month.month));
        return row;
      })),
    ]),
  ]);
}

function listingEventTable(events) {
  const columns = [
    { label: "Date", className: "" },
    { label: "Event", className: "event-names" },
    { label: "Start", className: "" },
    { label: "Tickets", className: "" },
  ];
  return el("div", { className: "month-table-wrap" }, [
    el("table", { className: "month-table listing-events" }, [
      el("thead", {}, [el("tr", {}, columns.map((column) => (
        el("th", column.className ? { className: column.className } : {}, [withInfo(column.label)])
      )))]),
      el("tbody", {}, events.map((event) => el("tr", {}, [
        el("th", { scope: "row" }, [text(formatPermitSpan(event.date, event.date))]),
        el("td", { className: "event-names" }, [text(event.name)]),
        el("td", {}, [text(formatClock(event.start_time))]),
        el("td", {}, [text(event.on_sale ? "On sale" : "Not on sale yet")]),
      ]))),
    ]),
  ]);
}

function mountListingSection(block, host) {
  if (!block || !(block.events || []).length) {
    host.replaceChildren();
    return;
  }
  let monthsExpanded = false;
  let selectedMonth = "";
  const months = block.months || [];
  const events = block.events || [];
  const monthHost = el("div", { className: "listing-pane listing-pane-months" });
  const eventHost = el("div", { className: "listing-pane listing-pane-events" });

  function upcomingMonths() {
    const start = todayIso().slice(0, 7);
    const later = months.filter((month) => month.month >= start);
    return later.length ? later : months;
  }

  function paint() {
    const laterMonths = upcomingMonths();
    const shownMonths = monthsExpanded ? months : laterMonths.slice(0, 3);
    const monthToggle = el("button", { className: "show-more", type: "button" }, [
      text(monthsExpanded ? "Show less" : "Show more"),
    ]);
    monthToggle.hidden = months.length <= shownMonths.length && !monthsExpanded;
    monthToggle.addEventListener("click", () => {
      monthsExpanded = !monthsExpanded;
      paint();
    });

    const selected = months.find((month) => month.month === selectedMonth);
    const monthEvents = selected
      ? events.filter((event) => event.date.slice(0, 7) === selectedMonth)
      : [];
    monthHost.replaceChildren(
      listingMonthTable(shownMonths, selectedMonth, (key) => {
        selectedMonth = selectedMonth === key ? "" : key;
        paint();
      }),
      monthToggle,
    );
    eventHost.hidden = !selected;
    eventHost.replaceChildren(
      ...(selected ? [
        el("p", { className: "listing-caption" }, [text(selected.label)]),
        monthEvents.length
          ? listingEventTable(monthEvents)
          : el("p", { className: "muted" }, [text("No listings in this month.")]),
      ] : []),
    );
  }

  paint();
  host.replaceChildren(el("section", { className: "permit-section listing-section", "aria-label": "Listed events" }, [
    el("h3", { className: "section-title" }, [withInfo("Listed events")]),
    el("p", { className: "muted" }, [text(block.note || "")]),
    el("p", { className: "muted" }, [text("Click a month to see its events. Click it again to clear.")]),
    el("div", { className: "listing-split" }, [monthHost, eventHost]),
    el("p", { className: "terms" }, [text(block.disclaimer || "")]),
  ]));
}

function mountPermitSection(venueId, host, source) {
  let request = 0;
  let groupsExpanded = false;
  const series = source === "nibrs" ? "nibrs" : "reports";
  const unit = series === "nibrs" ? "offenses" : "reports";

  async function refresh() {
    const token = ++request;
    let body;
    try {
      body = await fetchJson(`/api/venues/${encodeURIComponent(venueId)}/permit-comparison?source=${series}`);
    } catch (error) {
      if (token === request) host.replaceChildren();
      return;
    }
    if (token !== request || !body.available) {
      if (token === request) host.replaceChildren();
      return;
    }
    const rows = [{ label: "Overall", ...body.summary }, ...(body.series || []).map((item) => ({
      label: item.year,
      ...item,
    }))];
    const venueNote = el("p", { className: "muted" });
    venueNote.hidden = !body.note;
    venueNote.textContent = body.note || "";
    const percentNote = el("p", { className: "muted" });
    percentNote.hidden = !body.percent_note;
    percentNote.textContent = body.percent_note || "";
    const groups = body.groups || [];
    const groupList = el("div", { className: "category-list" });
    const groupToggle = el("button", { className: "show-more", type: "button" }, [text("Show more")]);

    function paintGroups() {
      const visible = groupsExpanded ? groups : groups.slice(0, 5);
      groupList.replaceChildren(...groupRows(visible));
      groupToggle.hidden = groups.length <= 5;
      groupToggle.textContent = groupsExpanded ? "Show less" : "Show more";
    }

    groupToggle.addEventListener("click", () => {
      groupsExpanded = !groupsExpanded;
      paintGroups();
    });
    const groupBlock = groups.length
      ? [
        el("h3", { className: "section-title" }, [withInfo("Offense groups")]),
        el("p", { className: "muted" }, [text(`Groups that differ by at least 0.05 ${unit} per day.`)]),
        groupList,
        groupToggle,
      ]
      : (body.summary.event_day_count >= 8
        ? [el("p", { className: "muted" }, [text(`No offense group differs by at least 0.05 ${unit} per day.`)])]
        : []);
    if (groups.length) paintGroups();
    const loadRows = body.permit_load || [];
    const loadBlock = loadRows.length ? [
      el("h3", { className: "section-title" }, [withInfo("Permits on the same day")]),
      el("p", { className: "muted" }, [text("One permit on the day, versus several. Both are compared with other days.")]),
      permitLoadTable(loadRows, unit),
    ] : [];
    host.replaceChildren(el("section", { className: "permit-section", "aria-label": "Permit days" }, [
      el("h3", { className: "section-title" }, [withInfo("Permit days vs other days")]),
      el("p", { className: "muted" }, [text(
        "A permit day is a day covered by a temporary-event permit. Other days are the rest of the months that had a permit.",
      )]),
      venueNote,
      body.source_note ? el("p", { className: "terms" }, [text(body.source_note)]) : el("span"),
      percentNote,
      body.empty ? el("span") : permitTable(rows),
      ...groupBlock,
      ...loadBlock,
      el("p", { className: "terms" }, [text(body.disclaimer || "")]),
    ]));
  }

  refresh();
}

function gameTable(summary) {
  const labels = ["", "Game days", "Other days in season", "Game-day mean", "Other-day mean in season", "Median", "Difference"];
  return el("div", { className: "month-table-wrap" }, [
    el("table", { className: "month-table permit-table" }, [
      el("thead", {}, [el("tr", {}, labels.map((label) => el("th", {}, [withInfo(label)])))]),
      el("tbody", {}, [
        el("tr", { className: "is-overall" }, [
          el("th", { scope: "row" }, [withInfo("2020–2024")]),
          el("td", {}, [text(formatNumber(summary.event_day_count))]),
          el("td", {}, [text(formatNumber(summary.other_day_count))]),
          el("td", {}, [text(formatMean(summary.event_day_mean))]),
          el("td", {}, [text(formatMean(summary.other_day_mean))]),
          el("td", {}, [text(`${formatMedian(summary.event_day_median)} vs ${formatMedian(summary.other_day_median)}`)]),
          el("td", {}, [text(formatGap(summary))]),
        ]),
      ]),
    ]),
  ]);
}

function mountHomeGames(host) {
  host.replaceChildren();
  fetchJson("/api/venues/V01/home-games").then((body) => {
    if (!body.available) {
      host.replaceChildren();
      return;
    }
    const groups = body.groups || [];
    host.replaceChildren(el("section", { className: "permit-section", "aria-label": "Home games" }, [
      el("h3", { className: "section-title" }, [withInfo("Home games vs other days")]),
      el("p", { className: "muted" }, [text(body.note || "")]),
      gameTable(body.summary),
      groups.length
        ? el("h3", { className: "section-title" }, [withInfo("Game-day groups")])
        : el("span"),
      groups.length
        ? el("p", { className: "muted" }, [text("Groups that differ by at least 0.05 reports per day.")])
        : el("span"),
      groups.length ? el("div", { className: "category-list" }, groupRows(groups, "Game-day mean")) : el("span"),
      el("p", { className: "terms" }, [text(body.disclaimer || "")]),
    ]));
  }).catch(() => {
    host.replaceChildren();
  });
}

const TIME_COLORS = {
  night: "#000000",
  morning: "#F4C300",
  afternoon: "#0085C7",
  evening: "#009F3D",
};

const DISTANCE_COLORS = {
  near: "#0085C7",
  mid: "#F4C300",
  far: "#009F3D",
};

const GROUP_COLORS = {
  vehicle: "#0085C7",
  theft: "#009F3D",
  assault: "#DF0024",
  vandalism: "#F4C300",
  burglary: "#000000",
  robbery: "#005A8C",
  weapons: "#046A2C",
  sexual: "#9E1B2E",
  homicide: "#C4A035",
  other: "#6B6B6B",
};

function shareLabel(count, total) {
  if (!total) return "0%";
  const share = (count / total) * 100;
  if (share > 0 && share < 1) return "<1%";
  return `${Math.round(share)}%`;
}

function pieSlicePath(cx, cy, radius, start, end) {
  const sweep = end - start;
  if (sweep >= Math.PI * 2 - 0.0001) {
    return `M ${cx - radius} ${cy} A ${radius} ${radius} 0 1 1 ${cx + radius} ${cy} A ${radius} ${radius} 0 1 1 ${cx - radius} ${cy} Z`;
  }
  const x1 = cx + radius * Math.cos(start);
  const y1 = cy + radius * Math.sin(start);
  const x2 = cx + radius * Math.cos(end);
  const y2 = cy + radius * Math.sin(end);
  const large = sweep > Math.PI ? 1 : 0;
  return `M ${cx} ${cy} L ${x1} ${y1} A ${radius} ${radius} 0 ${large} 1 ${x2} ${y2} Z`;
}

function showSliceTip(event, title, detailText) {
  const tip = ensureChartTip();
  tip.replaceChildren(
    el("strong", {}, [text(title)]),
    el("span", {}, [text(detailText)]),
  );
  placeChartTip(event);
}

function pieBlock(entries, total, options = {}) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "pie-chart");
  svg.setAttribute("viewBox", "0 0 200 200");
  svg.setAttribute("role", "img");
  const slices = entries.filter((entry) => entry.count > 0);
  svg.setAttribute(
    "aria-label",
    slices.map((entry) => `${entry.label}, ${formatNumber(entry.count)}`).join(". "),
  );
  const radius = 86;
  let angle = -Math.PI / 2;
  const sliceTotal = slices.reduce((sum, entry) => sum + entry.count, 0) || 1;
  for (const entry of slices) {
    const sweep = (entry.count / sliceTotal) * Math.PI * 2;
    const start = angle;
    angle += sweep;
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    const selected = options.selectedId === entry.id;
    path.setAttribute("d", pieSlicePath(100, 100, selected ? radius + 6 : radius, start, angle));
    path.setAttribute("fill", entry.color);
    const selectedStroke = entry.color.toLowerCase() === "#000000" ? "#ffffff" : "#1a1a1a";
    path.setAttribute("stroke", selected ? selectedStroke : "#f7f6f4");
    path.setAttribute("stroke-width", selected ? "2" : "1");
    const detailText = `${formatNumber(entry.count)} ${options.countWord || "incidents"} · ${shareLabel(entry.count, total)}`;
    const show = (event) => showSliceTip(event, entry.label, detailText);
    path.addEventListener("mouseenter", show);
    path.addEventListener("mousemove", (event) => {
      if (!chartTip || chartTip.hidden) show(event);
      else placeChartTip(event);
    });
    if (options.onPick) {
      path.style.cursor = "pointer";
      path.addEventListener("click", () => options.onPick(entry.id));
    }
    svg.append(path);
  }
  svg.addEventListener("mouseleave", hideChartTip);

  const legend = el("div", { className: "pie-legend" });
  if (options.caption) {
    legend.append(el("p", { className: "pie-caption" }, [text(options.caption)]));
  }
  for (const entry of slices) {
    const selected = options.selectedId === entry.id;
    const attrs = { className: selected ? "pie-key is-selected" : "pie-key" };
    if (options.onPick) {
      attrs.type = "button";
      attrs["aria-pressed"] = selected ? "true" : "false";
    }
    const row = el(options.onPick ? "button" : "div", attrs, [
      el("i", { className: "pie-swatch", style: `background:${entry.color}` }),
      el("span", {}, [text(entry.label)]),
      el("strong", {}, [text(`${formatNumber(entry.count)} · ${shareLabel(entry.count, total)}`)]),
    ]);
    const detailText = `${formatNumber(entry.count)} ${options.countWord || "incidents"} · ${shareLabel(entry.count, total)}`;
    const show = (event) => showSliceTip(event, entry.label, detailText);
    row.addEventListener("mouseenter", show);
    row.addEventListener("mousemove", (event) => {
      if (!chartTip || chartTip.hidden) show(event);
      else placeChartTip(event);
    });
    row.addEventListener("mouseleave", hideChartTip);
    if (options.onPick) row.addEventListener("click", () => options.onPick(entry.id));
    legend.append(row);
  }
  return el("div", { className: "pie-block" }, [svg, legend]);
}

function groupEntries(groups) {
  return (groups || []).map((group) => ({
    id: group.id,
    label: group.label,
    count: group.count,
    color: GROUP_COLORS[group.id] || "#4e5963",
  }));
}

function pieSlices(block, colorFor) {
  return (block?.periods || block?.bands || []).map((item) => ({
    ...item,
    color: colorFor(item.id),
  }));
}

function mountDrillPie(host, options) {
  const slices = (options.slices || []).filter((slice) => slice.count);
  if (!slices.length) {
    if (!options.monthLabel) {
      host.replaceChildren();
      return;
    }
    host.replaceChildren(el("section", { className: "time-section", "aria-label": options.title }, [
      el("h3", { className: "section-title" }, [text(options.title)]),
      el("p", { className: "muted" }, [text(`${options.monthLabel}. None in this month.`)]),
    ]));
    return;
  }
  let selected = "";
  const row = el("div", { className: "pie-row" });

  function paint() {
    hideChartTip();
    const chosen = slices.find((slice) => slice.id === selected);
    const primary = pieBlock(slices, options.total, {
      selectedId: selected,
      countWord: options.countWord,
      onPick: (id) => {
        selected = selected === id ? "" : id;
        paint();
      },
    });
    if (!chosen) {
      row.replaceChildren(primary);
      return;
    }
    row.replaceChildren(
      primary,
      pieBlock(groupEntries(chosen.groups), chosen.count, {
        caption: `Crimes, ${chosen.label}`,
        countWord: options.countWord,
      }),
    );
  }

  host.replaceChildren(el("section", { className: "time-section", "aria-label": options.title }, [
    el("h3", { className: "section-title" }, [text(options.title)]),
    el("p", { className: "muted" }, [text(options.hint)]),
    row,
    el("p", { className: "terms" }, [text(options.disclaimer || "")]),
    options.guide ? el("p", { className: "muted pie-guide" }, [text(options.guide)]) : el("span"),
  ]));
  paint();
}

function crimePie(chosen, countWord) {
  return pieBlock(groupEntries(chosen.groups), chosen.count, {
    caption: `Crimes, ${chosen.label}`,
    countWord,
  });
}

function mountPairedPies(host, options) {
  const panels = options.panels.map((panel) => ({
    ...panel,
    slices: (panel.slices || []).filter((slice) => slice.count),
  }));
  const blank = panels.map((panel) => !panel.slices.length);
  const hint = blank[0] !== blank[1]
    ? "Click a slice to see the crimes in the open space. Click it again to clear."
    : options.hint;

  if (blank[0] && blank[1]) {
    host.replaceChildren(el("section", { className: "time-section", "aria-label": options.title }, [
      el("h3", { className: "section-title" }, [text(options.title)]),
      el("p", { className: "muted" }, [text(options.month ? `${options.month}. None in this month.` : "None in this range.")]),
    ]));
    return;
  }

  if (blank[0] !== blank[1]) {
    const liveIndex = blank[0] ? 1 : 0;
    const live = panels[liveIndex];
    let selected = "";
    const liveHost = el("div");
    const crimeHost = el("div");

    function paint() {
      hideChartTip();
      const chosen = live.slices.find((slice) => slice.id === selected);
      liveHost.replaceChildren(pieBlock(live.slices, live.total, {
        selectedId: selected,
        countWord: live.countWord,
        onPick: (id) => {
          selected = selected === id ? "" : id;
          paint();
        },
      }));
      crimeHost.replaceChildren(chosen ? crimePie(chosen, live.countWord) : el("span"));
    }

    paint();
    const liveColumn = el("div", { className: "pie-column" }, [
      el("p", { className: "listing-caption" }, [text(live.label)]),
      liveHost,
      el("p", { className: "terms" }, [text(live.disclaimer || "")]),
    ]);
    const crimeColumn = el("div", { className: "pie-column pie-slot" }, [crimeHost]);
    host.replaceChildren(el("section", { className: "time-section", "aria-label": options.title }, [
      el("h3", { className: "section-title" }, [text(options.title)]),
      el("p", { className: "muted" }, [text(options.month ? `${options.month}. ${hint}` : hint)]),
      el("div", { className: "pie-pair pie-pair-slots" }, blank[0] ? [crimeColumn, liveColumn] : [liveColumn, crimeColumn]),
    ]));
    return;
  }

  const columns = panels.map((panel) => {
    const primaryHost = el("div");
    const drillHost = el("div", { className: "pie-drill" });
    let selected = "";

    function paint() {
      hideChartTip();
      const chosen = panel.slices.find((slice) => slice.id === selected);
      primaryHost.replaceChildren(pieBlock(panel.slices, panel.total, {
        selectedId: selected,
        countWord: panel.countWord,
        onPick: (id) => {
          selected = selected === id ? "" : id;
          paint();
        },
      }));
      drillHost.replaceChildren(chosen ? crimePie(chosen, panel.countWord) : el("span"));
    }

    paint();
    return el("div", { className: "pie-column" }, [
      el("p", { className: "listing-caption" }, [text(panel.label)]),
      primaryHost,
      drillHost,
      el("p", { className: "terms" }, [text(panel.disclaimer || "")]),
    ]);
  });

  host.replaceChildren(el("section", { className: "time-section", "aria-label": options.title }, [
    el("h3", { className: "section-title" }, [text(options.title)]),
    el("p", { className: "muted" }, [text(options.month ? `${options.month}. ${hint}` : hint)]),
    el("div", { className: "pie-pair" }, columns),
  ]));
}

const PIE_GUIDE = "Choose “NIBRS offenses, Mar 2024–present” in Series, above, to see the 2024–present chart.";

function mountTimeSection(crimeTime, host, countWord, guide, monthLabel) {
  const periods = crimeTime?.periods || [];
  mountDrillPie(host, {
    title: "Crimes by time of day",
    hint: "Click a slice to see the crimes in that part of the day. Click it again to clear.",
    disclaimer: crimeTime?.disclaimer || "",
    guide,
    monthLabel,
    total: crimeTime?.total || 0,
    countWord,
    slices: periods.map((period) => ({
      ...period,
      color: TIME_COLORS[period.id] || "#4e5963",
    })),
  });
}

function scopedWeekday(block, month) {
  if (!block) return null;
  if (!month) return block;
  const scoped = block.by_month?.[month];
  if (!scoped || !scoped.total) return { total: 0, days: [], disclaimer: block.disclaimer || "" };
  return { ...scoped, disclaimer: block.disclaimer || "" };
}

function weekdayBars(block, selectedId, onPick) {
  const days = block.days || [];
  const max = Math.max(...days.map((day) => day.count), 1);
  return el("div", { className: "weekday-chart" }, days.map((day) => {
    const pressed = day.id === selectedId;
    const attrs = {
      className: day.id === "sat" || day.id === "sun" ? "weekday-col is-weekend" : "weekday-col",
    };
    if (onPick) {
      attrs.type = "button";
      attrs["aria-pressed"] = pressed ? "true" : "false";
    }
    const column = el(onPick ? "button" : "div", attrs, [
      el("span", { className: "weekday-count" }, [text(formatNumber(day.count))]),
      el("span", {
        className: "weekday-bar",
        style: `height:${Math.max(6, Math.round((day.count / max) * 168))}px`,
      }),
      el("span", { className: "weekday-name" }, [text(day.label.slice(0, 3))]),
    ]);
    if (onPick) column.addEventListener("click", () => onPick(day.id));
    return column;
  }));
}

function weekdayGroups(day, limit) {
  const groups = (day.groups || []).slice(0, limit);
  if (!groups.length) {
    return el("p", { className: "muted" }, [text(`${day.label}. No offense groups for this day.`)]);
  }
  const max = Math.max(...groups.map((group) => group.count), 1);
  return el("div", { className: "weekday-groups" }, [
    el("p", { className: "listing-caption" }, [text(day.label)]),
    ...groups.map((group) => el("div", { className: "bar-row weekday-group" }, [
      el("div", {}, [
        el("div", { className: "bar-label" }, [text(group.label)]),
        el("div", { className: "bar-track" }, [
          el("div", { className: "bar-fill", style: `width: ${(group.count / max) * 100}%` }),
        ]),
      ]),
      el("div", { className: "bar-count" }, [text(formatNumber(group.count))]),
    ])),
  ]);
}

function weekdaySummary(block, countWord) {
  const busiest = (block.days || []).reduce((best, day) => (day.count > best.count ? day : best), block.days[0]);
  const weekendShare = block.total ? Math.round((block.weekend_count / block.total) * 100) : 0;
  return `${busiest.label} is the busiest day, ${formatNumber(busiest.count)} ${countWord}. Weekend days are ${formatNumber(block.weekend_count)} (${weekendShare}%).`;
}

const WEEKDAY_CLICK = "Click a day to see its main offense groups. Click it again to clear.";

function weekdayHasGroups(block) {
  return (block?.days || []).some((day) => (day.groups || []).length);
}

function mountWeekdaySection(block, host, countWord, monthLabel, placement) {
  if (!block || (!block.total && !monthLabel)) {
    host.replaceChildren();
    return;
  }
  if (!block.total) {
    host.replaceChildren(el("section", { className: "time-section", "aria-label": "Day of week" }, [
      el("h3", { className: "section-title" }, [text("Day of week")]),
      el("p", { className: "muted" }, [text(`${monthLabel}. None in this month.`)]),
    ]));
    return;
  }
  const stacked = placement === "below";
  const limit = stacked ? 5 : 3;
  const when = monthLabel ? `${monthLabel}. ` : "";
  const clickable = weekdayHasGroups(block);
  let selected = "";
  const row = el("div", { className: stacked ? "weekday-layout is-stacked" : "weekday-layout" });

  function paint() {
    const chosen = (block.days || []).find((day) => day.id === selected);
    row.replaceChildren(
      weekdayBars(block, selected, clickable ? (id) => {
        selected = selected === id ? "" : id;
        paint();
      } : null),
      chosen ? weekdayGroups(chosen, limit) : el("span"),
    );
  }

  paint();
  host.replaceChildren(el("section", { className: "time-section", "aria-label": "Day of week" }, [
    el("h3", { className: "section-title" }, [text("Day of week")]),
    el("p", { className: "muted" }, [text(`${when}${weekdaySummary(block, countWord)}`)]),
    clickable ? el("p", { className: "muted" }, [text(WEEKDAY_CLICK)]) : el("span"),
    row,
    block.disclaimer ? el("p", { className: "terms" }, [text(block.disclaimer)]) : el("span"),
  ]));
}

function mountWeekdayPair(host, reports, nibrs, monthLabel) {
  const panels = [
    { label: "2020–2024 reports", block: reports, countWord: "incidents" },
    { label: "NIBRS offenses, Mar 2024–present", block: nibrs, countWord: "offenses" },
  ].filter((panel) => panel.block?.total);
  if (!panels.length) {
    host.replaceChildren(el("section", { className: "time-section", "aria-label": "Day of week" }, [
      el("h3", { className: "section-title" }, [text("Day of week")]),
      el("p", { className: "muted" }, [text(monthLabel ? `${monthLabel}. None in this month.` : "None in this range.")]),
    ]));
    return;
  }
  const clickable = panels.some((panel) => weekdayHasGroups(panel.block));
  const note = monthLabel ? `${monthLabel}. Yellow bars are Saturday and Sunday.` : "Yellow bars are Saturday and Sunday.";
  host.replaceChildren(el("section", { className: "time-section", "aria-label": "Day of week" }, [
    el("h3", { className: "section-title" }, [text("Day of week")]),
    el("p", { className: "muted" }, [text(clickable ? `${note} ${WEEKDAY_CLICK}` : note)]),
    el("div", { className: "pie-pair" }, panels.map((panel) => {
      let selected = "";
      const row = el("div", { className: "weekday-layout is-stacked" });
      const canPick = weekdayHasGroups(panel.block);

      function paint() {
        const chosen = (panel.block.days || []).find((day) => day.id === selected);
        row.replaceChildren(
          weekdayBars(panel.block, selected, canPick ? (id) => {
            selected = selected === id ? "" : id;
            paint();
          } : null),
          chosen ? weekdayGroups(chosen, 5) : el("span"),
        );
      }

      paint();
      return el("div", { className: "pie-column" }, [
        el("p", { className: "listing-caption" }, [text(panel.label)]),
        el("p", { className: "muted" }, [text(weekdaySummary(panel.block, panel.countWord))]),
        row,
        panel.block.disclaimer ? el("p", { className: "terms" }, [text(panel.block.disclaimer)]) : el("span"),
      ]);
    })),
  ]));
}

function mountDistanceSection(crimeDistance, host, countWord, guide, monthLabel) {
  const bands = crimeDistance?.bands || [];
  mountDrillPie(host, {
    title: "Distance from the venue",
    hint: "Click a slice to see the crimes in that distance. Click it again to clear.",
    disclaimer: crimeDistance?.disclaimer || "",
    guide,
    monthLabel,
    total: crimeDistance?.total || 0,
    countWord,
    slices: bands.map((band) => ({
      ...band,
      color: DISTANCE_COLORS[band.id] || "#4e5963",
    })),
  });
}

function scopedBlock(block, month) {
  if (!month) return block || {};
  const scoped = block?.by_month?.[month];
  if (!scoped) return { total: 0, periods: [], bands: [], disclaimer: block?.disclaimer || "" };
  return { ...scoped, disclaimer: block?.disclaimer || "" };
}

function mountTimePair(host, reports, nibrs, month) {
  mountPairedPies(host, {
    title: "Crimes by time of day",
    month,
    hint: "Click a slice to see the crimes below that chart. Click it again to clear.",
    panels: [
      {
        label: "2020–2024 reports",
        slices: pieSlices(reports, (id) => TIME_COLORS[id] || "#4e5963"),
        total: reports?.total || 0,
        countWord: "incidents",
        disclaimer: reports?.disclaimer || "",
      },
      {
        label: "NIBRS offenses, Mar 2024–present",
        slices: pieSlices(nibrs, (id) => TIME_COLORS[id] || "#4e5963"),
        total: nibrs?.total || 0,
        countWord: "offenses",
        disclaimer: nibrs?.disclaimer || "",
      },
    ],
  });
}

function mountDistancePair(host, reports, nibrs, month) {
  mountPairedPies(host, {
    title: "Distance from the venue",
    month,
    hint: "Click a slice to see the crimes below that chart. Click it again to clear.",
    panels: [
      {
        label: "2020–2024 reports",
        slices: pieSlices(reports, (id) => DISTANCE_COLORS[id] || "#4e5963"),
        total: reports?.total || 0,
        countWord: "incidents",
        disclaimer: reports?.disclaimer || "",
      },
      {
        label: "NIBRS offenses, Mar 2024–present",
        slices: pieSlices(nibrs, (id) => DISTANCE_COLORS[id] || "#4e5963"),
        total: nibrs?.total || 0,
        countWord: "offenses",
        disclaimer: nibrs?.disclaimer || "",
      },
    ],
  });
}

function renderDetail() {
  const venue = state.detail;
  if (!venue) {
    detail.replaceChildren(el("p", { className: "empty-detail" }, [text("Select a venue to open its briefing.")]));
    return;
  }
  const present = venue.present;
  const reportMonths = filledMonths(venue.crime_by_month);
  const peak = present?.peak_month
    ? { key: present.peak_month, count: present.peak_count }
    : reportMonths.reduce(
      (best, entry) => (entry.count > best.count ? entry : best),
      reportMonths[0],
    );
  const total = presentCount(venue);
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
  let tableYear = "all";
  let fromMonth = 1;
  let toMonth = 12;
  let selectedKey = "";
  let categoriesExpanded = false;

  const categoryNote = el("p", { className: "muted", "aria-live": "polite" });
  const categoryList = el("div", { className: "category-list" });
  const categoryToggle = el("button", { className: "show-more", type: "button" }, [text("Show more")]);
  const tableWrap = el("div", { className: "month-table-wrap" });
  const yearSelect = el("select", { "aria-label": "Year" }, [
    el("option", { value: "all" }, [text("All years")]),
  ]);
  const fromSelect = el("select", { "aria-label": "From month" }, MONTH_NAMES.map((name, index) => (
    el("option", { value: String(index + 1) }, [text(name)])
  )));
  const toSelect = el("select", { "aria-label": "To month" }, MONTH_NAMES.map((name, index) => (
    el("option", { value: String(index + 1) }, [text(name)])
  )));
  toSelect.value = "12";
  let seriesId = "reports";
  const permitHost = el("div");
  const homeHost = el("div");
  const listingHost = el("div");
  const timeHost = el("div");
  const distanceHost = el("div");
  const weekdayHost = el("div");
  const chartHost = el("div", { className: "chart-host" });
  const yearHost = el("div", { className: "year-row" });
  const seriesHint = el("p", { className: "muted" });
  const seriesSelect = el("select", { "aria-label": "Crime series" }, [
    el("option", { value: "reports" }, [text("2020–2024 reports")]),
  ]);
  if (venue.nibrs) {
    seriesSelect.append(el("option", { value: "nibrs" }, [text("NIBRS offenses, Mar 2024–present")]));
  }
  if (venue.merged_by_month) {
    seriesSelect.append(el("option", { value: "merged" }, [text("Merged groups, 2020–present")]));
  }
  const hasSeries = Boolean(venue.nibrs || venue.merged_by_month);

  function seriesData() {
    if (seriesId === "nibrs" && venue.nibrs) {
      return {
        byMonth: venue.nibrs.by_month || {},
        byCategory: venue.nibrs.by_category || {},
        categoriesByMonth: venue.nibrs.categories_by_month || {},
        total: venue.nibrs.count || 0,
        word: "offenses",
        allLabel: "All offenses",
        empty: "No offenses in this range.",
      };
    }
    if (seriesId === "merged" && venue.merged_by_month) {
      const labels = venue.crime_groups || {};
      const byMonth = {};
      const byCategory = {};
      const categoriesByMonth = {};
      for (const [month, groups] of Object.entries(venue.merged_by_month)) {
        let monthTotal = 0;
        const named = {};
        for (const [group, count] of Object.entries(groups)) {
          const label = labels[group] || group;
          named[label] = (named[label] || 0) + count;
          byCategory[label] = (byCategory[label] || 0) + count;
          monthTotal += count;
        }
        byMonth[month] = monthTotal;
        categoriesByMonth[month] = named;
      }
      const totalCount = Object.values(byMonth).reduce((sum, value) => sum + value, 0);
      return {
        byMonth,
        byCategory,
        categoriesByMonth,
        total: totalCount,
        word: "records",
        allLabel: "All records",
        empty: "No records in this range.",
      };
    }
    return {
      byMonth: venue.crime_by_month,
      byCategory: venue.crime_by_category,
      categoriesByMonth: venue.crime_categories_by_month || {},
      total,
      word: "incidents",
      allLabel: "All incidents",
      empty: "No incidents in this range.",
    };
  }

  function seriesMonths() {
    return filledMonths(seriesData().byMonth);
  }

  function seriesPeak() {
    const entries = seriesMonths();
    if (!entries.length) return null;
    return entries.reduce((best, entry) => (entry.count > best.count ? entry : best), entries[0]);
  }

  function rangeKeys() {
    return seriesMonths().filter((entry) => {
      const [year, month] = entry.key.split("-");
      const monthNumber = Number(month);
      if (tableYear !== "all" && year !== tableYear) return false;
      return monthNumber >= fromMonth && monthNumber <= toMonth;
    }).map((entry) => entry.key);
  }

  function isFullRange() {
    return tableYear === "all" && fromMonth === 1 && toMonth === 12;
  }

  function rangeCaption() {
    const fromName = MONTH_NAMES[fromMonth - 1];
    const toName = MONTH_NAMES[toMonth - 1];
    const span = fromMonth === toMonth ? fromName : `${fromName}–${toName}`;
    if (tableYear === "all") return span;
    if (fromMonth === 1 && toMonth === 12) return tableYear;
    return `${span} ${tableYear}`;
  }

  function summedCategories(keys) {
    const totals = {};
    const byMonth = seriesData().categoriesByMonth;
    for (const key of keys) {
      const bucket = byMonth[key] || {};
      for (const [category, count] of Object.entries(bucket)) {
        totals[category] = (totals[category] || 0) + count;
      }
    }
    return totals;
  }

  function activeCategories() {
    const bundle = seriesData();
    const entries = seriesMonths();
    if (selectedKey) {
      const entry = entries.find((item) => item.key === selectedKey);
      const byCategory = bundle.categoriesByMonth[selectedKey] || {};
      const count = entry ? entry.count : Object.values(byCategory).reduce((sum, value) => sum + value, 0);
      return { label: monthLabel(selectedKey), total: count, byCategory, word: bundle.word, empty: bundle.empty };
    }
    if (isFullRange()) {
      return {
        label: bundle.allLabel,
        total: bundle.total,
        byCategory: bundle.byCategory,
        word: bundle.word,
        empty: bundle.empty,
      };
    }
    const keys = rangeKeys();
    const byCategory = summedCategories(keys);
    const count = Object.values(byCategory).reduce((sum, value) => sum + value, 0);
    return { label: rangeCaption(), total: count, byCategory, word: bundle.word, empty: bundle.empty };
  }

  function paintCategories() {
    const view = activeCategories();
    const viewCount = Object.keys(view.byCategory || {}).length;
    const rows = categoryRows(view.byCategory, view.total, {
      limit: categoriesExpanded ? undefined : 5,
    });
    categoryNote.textContent = `${view.label} · ${formatNumber(view.total)} ${view.word}`;
    categoryList.replaceChildren(...(rows.length
      ? rows
      : [el("p", { className: "muted" }, [text(view.empty)])]));
    categoryToggle.hidden = viewCount <= 5;
    categoryToggle.textContent = categoriesExpanded ? "Show less" : "Show more";
  }

  function markMonth(key) {
    const peakKey = seriesPeak()?.key;
    detail.querySelectorAll("[data-month]").forEach((node) => {
      const active = node.dataset.month === key;
      node.classList.toggle("is-active", active);
      if (node.tagName === "rect") {
        node.setAttribute("fill", node.dataset.month === peakKey ? "#DF0024" : (active ? "#1a1a1a" : "#0085C7"));
      }
    });
  }

  function paintTable() {
    const bundle = seriesData();
    const entries = seriesMonths();
    const peakKey = seriesPeak()?.key;
    const filtered = entries.filter((entry) => {
      const [year, month] = entry.key.split("-");
      const monthNumber = Number(month);
      if (tableYear !== "all" && year !== tableYear) return false;
      return monthNumber >= fromMonth && monthNumber <= toMonth;
    });
    if (!filtered.length) {
      tableWrap.replaceChildren(el("p", { className: "muted" }, [text(bundle.empty)]));
      return;
    }
    const table = monthTable(filtered, peakKey, selectedKey, fromMonth, toMonth);
    table.addEventListener("click", (event) => {
      const cell = event.target.closest("td[data-month]");
      if (cell?.dataset.month) selectMonth(cell.dataset.month);
    });
    tableWrap.replaceChildren(table);
  }

  function selectMonth(key) {
    selectedKey = selectedKey === key ? "" : key;
    categoriesExpanded = false;
    markMonth(selectedKey);
    paintCategories();
    paintPies();
    paintWeekday();
  }

  function readRange(changed) {
    let nextFrom = Number(fromSelect.value);
    let nextTo = Number(toSelect.value);
    if (nextFrom > nextTo) {
      if (changed === "from") nextTo = nextFrom;
      else nextFrom = nextTo;
      fromSelect.value = String(nextFrom);
      toSelect.value = String(nextTo);
    }
    tableYear = yearSelect.value;
    fromMonth = nextFrom;
    toMonth = nextTo;
    selectedKey = "";
    categoriesExpanded = false;
    paintTable();
    markMonth(selectedKey);
    paintCategories();
    paintPies();
    paintWeekday();
  }

  function seriesNote() {
    if (seriesId === "nibrs") {
      return "Each row is one NIBRS offense, so one case can count more than once. Click a bar or a table cell to filter types to that month. Click it again to clear. Red is the busiest month.";
    }
    if (seriesId === "merged") {
      return "Reports before March 7, 2024, then NIBRS offenses. Types are shared groups. Permit days in this view use 2020–2024 reports. Click a bar or a table cell to filter. Click it again to clear. Red is the busiest month.";
    }
    return "Click a bar or a table cell to filter incident types to that month. Click it again to clear. Red is the busiest month.";
  }

  function paintPies() {
    const month = selectedKey;
    const monthName = month ? monthTitle(month) : "";
    const reportsTime = scopedBlock(venue.crime_time, month);
    const nibrsTime = scopedBlock(venue.nibrs_time, month);
    const reportsDistance = scopedBlock(venue.crime_distance, month);
    const nibrsDistance = scopedBlock(venue.nibrs_distance, month);
    if (seriesId === "merged") {
      mountTimePair(timeHost, reportsTime, nibrsTime, monthName);
      mountDistancePair(distanceHost, reportsDistance, nibrsDistance, monthName);
      return;
    }
    const nibrsSeries = seriesId === "nibrs";
    const countWord = nibrsSeries ? "offenses" : "incidents";
    const guide = nibrsSeries || month ? "" : PIE_GUIDE;
    const timeBlock = nibrsSeries ? nibrsTime : reportsTime;
    const distanceBlock = nibrsSeries ? nibrsDistance : reportsDistance;
    mountTimeSection(timeBlock, timeHost, countWord, guide, monthName);
    mountDistanceSection(distanceBlock, distanceHost, countWord, guide, monthName);
    if (monthName) {
      for (const host of [timeHost, distanceHost]) {
        const hint = host.querySelector(".time-section > .muted");
        if (hint && !hint.textContent.startsWith(monthName)) {
          hint.textContent = `${monthName}. ${hint.textContent}`;
        }
      }
    }
  }

  function paintWeekday() {
    const monthName = selectedKey ? monthTitle(selectedKey) : "";
    if (seriesId === "merged") {
      mountWeekdayPair(
        weekdayHost,
        scopedWeekday(venue.crime_weekday, selectedKey),
        scopedWeekday(venue.nibrs_weekday, selectedKey),
        monthName,
      );
      return;
    }
    const nibrsSeries = seriesId === "nibrs";
    mountWeekdaySection(
      scopedWeekday(nibrsSeries ? venue.nibrs_weekday : venue.crime_weekday, selectedKey),
      weekdayHost,
      nibrsSeries ? "offenses" : "incidents",
      monthName,
    );
  }

  function paintHomeGames() {
    const show = venue.venue_id === "V01" && seriesId !== "nibrs";
    homeHost.hidden = !show;
    if (show && !homeHost.dataset.loaded) {
      homeHost.dataset.loaded = "true";
      mountHomeGames(homeHost);
    }
  }

  function paintCharts() {
    paintPies();
    paintWeekday();
    mountPermitSection(venue.venue_id, permitHost, seriesId === "nibrs" ? "nibrs" : "reports");
    paintHomeGames();
  }

  function applySeries() {
    const entries = seriesMonths();
    const yearsNow = yearTotals(entries);
    const peakNow = seriesPeak();
    const peakYearNow = yearsNow.reduce(
      (best, entry) => (entry[1] > best[1] ? entry : best),
      yearsNow[0] || ["", 0],
    );
    yearSelect.replaceChildren(
      el("option", { value: "all" }, [text("All years")]),
      ...yearsNow.map(([year]) => el("option", { value: year }, [text(year)])),
    );
    yearSelect.value = tableYear;
    yearHost.replaceChildren(...yearsNow.map(([year, count]) => (
      el("article", { className: year === peakYearNow[0] ? "year-card is-peak" : "year-card" }, [
        el("span", {}, [text(year)]),
        el("strong", {}, [text(formatNumber(count))]),
      ])
    )));
    chartHost.replaceChildren(monthChart(entries, peakNow?.key, selectMonth, seriesData().word));
    seriesHint.textContent = seriesNote();
    paintCharts();
    paintTable();
    markMonth(selectedKey);
    paintCategories();
  }

  categoryToggle.addEventListener("click", () => {
    categoriesExpanded = !categoriesExpanded;
    paintCategories();
  });
  yearSelect.addEventListener("change", () => readRange("year"));
  fromSelect.addEventListener("change", () => readRange("from"));
  toSelect.addEventListener("change", () => readRange("to"));
  seriesSelect.addEventListener("change", () => {
    seriesId = seriesSelect.value;
    selectedKey = "";
    categoriesExpanded = false;
    tableYear = "all";
    fromMonth = 1;
    toMonth = 12;
    fromSelect.value = "1";
    toSelect.value = "12";
    applySeries();
  });
  applySeries();

  detail.hidden = false;
  detail.scrollTop = 0;
  detail.replaceChildren(el("div", { className: "venue-sheet" }, [
    venueToolbar(),
    el("p", { className: "zone" }, [text(`${venue.olympic_zone} · ${formatSports(venue.sports)}`)]),
    el("h2", { className: "detail-title", id: "detail-heading" }, [text(venue.venue_name)]),
    el("p", { className: "address" }, [text(`${venue.address}, ${venue.city}`)]),
    el("div", { className: "stats" }, [
      stat("Incidents", formatNumber(total), "inside the buffer, 2020–present"),
      stat("Density", formatNumber(presentRate(venue)), densityNote(venue), DENSITY_HINT),
      ...cityStats(venue),
      stat("Busiest month", peak?.key ? monthLabel(peak.key) : "None", peak?.count ? `${formatNumber(peak.count)} records` : "No dated records"),
    ]),
    venue.nibrs ? el("p", { className: "muted" }, [
      text(`NIBRS offenses since Mar 2024: ${formatNumber(venue.nibrs.count)}. A case can include more than one offense.`),
    ]) : el("span"),
    flags.length ? el("div", { className: "flags" }, flags) : el("span"),
    overlapNote(venue) ? el("p", { className: "terms" }, [text(overlapNote(venue))]) : el("span"),
    el("h3", { className: "section-title" }, [text("Incidents by month")]),
    hasSeries ? el("div", { className: "table-filters" }, [
      el("label", {}, [text("Series"), seriesSelect]),
    ]) : el("span"),
    seriesHint,
    yearHost,
    chartHost,
    el("div", { className: "table-filters" }, [
      el("label", {}, [text("Year"), yearSelect]),
      el("label", {}, [text("From"), fromSelect]),
      el("label", {}, [text("To"), toSelect]),
    ]),
    tableWrap,
    el("h3", { className: "section-title" }, [text("Incident types")]),
    categoryNote,
    categoryList,
    categoryToggle,
    timeHost,
    distanceHost,
    weekdayHost,
    permitHost,
    homeHost,
    listingHost,
    el("h3", { className: "section-title" }, [text(`Rail · ${formatNumber(venue.rail_stations_nearby.count)} ${venue.rail_stations_nearby.count === 1 ? "station" : "stations"}`)]),
    rail.length ? el("ul", { className: "place-list" }, rail) : el("p", { className: "muted" }, [text("None in the buffer.")]),
    el("h3", { className: "section-title" }, [
      text(`Bus · ${formatNumber(venue.bus_stops_nearby.count)} stops · ${venue.bus_stops_nearby.lines.length} lines`),
    ]),
    el("div", { className: "pills" }, buses.length ? buses : [el("span", { className: "muted" }, [text("None in the buffer.")])]),
    el("h3", { className: "section-title" }, [text("Nearest response and care")]),
    el("div", { className: "station-grid" }, careStations(venue)),
    el("p", { className: "terms" }, [text(state.meta.jurisdiction_method)]),
  ]));
  mountListingSection(venue.ticketmaster, listingHost);
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

function syncCrimeHint() {
  if (!mapHint || mapHint.hidden) return;
  mapHint.textContent = state.crimeView === "nibrs" ? NIBRS_CRIME_HINT : DEFAULT_CRIME_HINT;
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
    syncCrimeHint();
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
    syncCrimeHint();
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

let compareSeries = "reports";
let compareView = "overview";
let compareToken = 0;
const compareCache = new Map();

function compareRoute() {
  if (!location.hash.startsWith("#/compare")) return null;
  const match = location.hash.match(/^#\/compare\/([A-Za-z0-9_-]+)\/([A-Za-z0-9_-]+)$/);
  return { a: match ? match[1] : "", b: match ? match[2] : "" };
}

function syncCompareLink() {
  const current = location.hash.startsWith("#/compare");
  document.querySelectorAll(".compare-link").forEach((button) => {
    if (current) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
}

function rankedVenueIds() {
  return [...state.venues]
    .sort((left, right) => Number(right.crime_per_km2) - Number(left.crime_per_km2))
    .map((venue) => venue.venue_id);
}

function knownVenue(venueId) {
  return state.venues.some((venue) => venue.venue_id === venueId);
}

function resolveComparePair(a, b) {
  const ranked = rankedVenueIds();
  const left = knownVenue(a) ? a : (ranked[0] || "");
  const right = knownVenue(b) ? b : (ranked.find((venueId) => venueId !== left) || "");
  return [left, right];
}

function rememberCompare(a, b) {
  const next = a && b ? `#/compare/${a}/${b}` : "#/compare";
  if (location.hash === next) return;
  history.pushState({ compare: true }, "", next);
  syncCompareLink();
}

function closeCompare(options = {}) {
  compareToken += 1;
  if (comparePage) comparePage.hidden = true;
  if (options.history !== false && location.hash.startsWith("#/compare")) {
    history.pushState({}, "", `${location.pathname}${location.search}`);
  }
  syncCompareLink();
  resizeMap();
}

function pairOverlapNote(left, right) {
  const overlaps = (left.overlapping_venues || []).some((item) => item.venue_id === right.venue_id)
    || (right.overlapping_venues || []).some((item) => item.venue_id === left.venue_id);
  if (!overlaps) return "";
  return `The 800 m circles of ${left.venue_name} and ${right.venue_name} overlap. An incident in the overlap is counted for each venue.`;
}

function compareSeriesNote() {
  if (compareView === "overview") return "";
  if (compareView === "permits" && compareSeries !== "nibrs") {
    return "Permit days in this view use 2020–2024 reports.";
  }
  if (compareSeries === "nibrs") return "Counts are NIBRS offenses. One case can count more than once.";
  if (compareSeries === "merged") return "Each column shows 2020–2024 reports beside NIBRS offenses.";
  return "";
}

function compareHeader(venue) {
  return [
    el("p", { className: "zone compare-zone" }, [text(`${venue.olympic_zone} · ${formatSports(venue.sports)}`)]),
  ];
}

function overviewWeekdayLines(venue) {
  if (compareSeries === "nibrs") {
    const line = weekdayLine(venue.nibrs_weekday, "offenses");
    return line ? [line] : [];
  }
  if (compareSeries === "merged") {
    const reports = weekdayLine(venue.crime_weekday, "incidents");
    const nibrs = weekdayLine(venue.nibrs_weekday, "offenses");
    return [
      reports ? `2020–2024: ${reports}` : "",
      nibrs ? `NIBRS: ${nibrs}` : "",
    ].filter(Boolean);
  }
  const line = weekdayLine(venue.crime_weekday, "incidents");
  return line ? [line] : [];
}

function openComparedVenue(venueId) {
  if (!venueId) return;
  closeCompare({ history: false });
  selectVenue(venueId, { expand: true });
}

function venueBar(label, select) {
  const button = el("button", { type: "button", className: "compare-venue-link" }, [text("Venue page")]);
  button.addEventListener("click", () => openComparedVenue(select.value));
  return el("div", { className: "compare-venue-bar" }, [
    el("label", {}, [text(label), select]),
    button,
  ]);
}

function weekdayLine(block, countWord) {
  if (!block?.total || !(block.days || []).length) return "";
  return weekdaySummary(block, countWord);
}

function quietCompareChart(host) {
  host.querySelectorAll(".section-title, .pie-guide").forEach((node) => node.remove());
  host.querySelectorAll(".time-section > .muted").forEach((node) => {
    if (node.textContent.startsWith("Click a slice")) node.remove();
  });
}

function venuePick(selected) {
  const select = el("select", { className: "venue-pick" });
  const venues = [...state.venues].sort((left, right) => left.venue_name.localeCompare(right.venue_name));
  select.append(...venues.map((venue) => el("option", { value: venue.venue_id }, [text(venue.venue_name)])));
  select.value = selected;
  return select;
}

async function loadCompareVenue(venueId) {
  if (compareCache.has(venueId)) return compareCache.get(venueId);
  const venue = await fetchJson(`/api/venues/${encodeURIComponent(venueId)}`);
  compareCache.set(venueId, venue);
  return venue;
}

function compareColumn(venue) {
  if (compareView === "overview") {
    return el("div", { className: "compare-body" }, [
      ...compareHeader(venue),
      el("div", { className: "stats" }, [
        stat("Incidents", formatNumber(presentCount(venue)), "inside the buffer, 2020–present"),
        stat("Density", formatNumber(presentRate(venue)), densityNote(venue), DENSITY_HINT),
        ...cityStats(venue),
      ]),
      venue.nibrs ? el("p", { className: "muted" }, [
        text(`NIBRS offenses since Mar 2024: ${formatNumber(venue.nibrs.count)}.`),
      ]) : el("span"),
      ...overviewWeekdayLines(venue).map((line) => el("p", { className: "muted" }, [text(line)])),
    ]);
  }
  if (compareView === "permits") {
    const permitHost = el("div");
    const homeHost = el("div");
    const nibrsSeries = compareSeries === "nibrs";
    mountPermitSection(venue.venue_id, permitHost, nibrsSeries ? "nibrs" : "reports");
    if (venue.venue_id === "V01" && !nibrsSeries) mountHomeGames(homeHost);
    return el("div", { className: "compare-body" }, [
      ...compareHeader(venue),
      permitHost,
      homeHost,
    ]);
  }
  const host = el("div");
  const nibrsSeries = compareSeries === "nibrs";
  const countWord = nibrsSeries ? "offenses" : "incidents";
  const merged = compareSeries === "merged";
  if (compareView === "time") {
    if (merged) mountTimePair(host, venue.crime_time, venue.nibrs_time, "");
    else mountTimeSection(nibrsSeries ? venue.nibrs_time : venue.crime_time, host, countWord, "", "");
  } else if (compareView === "distance") {
    if (merged) mountDistancePair(host, venue.crime_distance, venue.nibrs_distance, "");
    else mountDistanceSection(nibrsSeries ? venue.nibrs_distance : venue.crime_distance, host, countWord, "", "");
  } else {
    if (merged) mountWeekdayPair(host, venue.crime_weekday, venue.nibrs_weekday, "");
    else mountWeekdaySection(nibrsSeries ? venue.nibrs_weekday : venue.crime_weekday, host, countWord, "", "below");
  }
  quietCompareChart(host);
  return el("div", { className: "compare-body" }, [
    ...compareHeader(venue),
    host,
  ]);
}

function renderCompare(idA, idB) {
  const token = ++compareToken;
  const [leftId, rightId] = resolveComparePair(idA, idB);
  const selectA = venuePick(leftId);
  const selectB = venuePick(rightId);
  const series = el("select", { "aria-label": "Crime series" }, [
    el("option", { value: "reports" }, [text("2020–2024 reports")]),
    el("option", { value: "nibrs" }, [text("NIBRS offenses, Mar 2024–present")]),
    el("option", { value: "merged" }, [text("Merged groups, 2020–present")]),
  ]);
  series.value = compareSeries;
  selectA.setAttribute("aria-label", "Venue A");
  selectB.setAttribute("aria-label", "Venue B");

  function paintPair() {
    rememberCompare(selectA.value, selectB.value);
    renderCompare(selectA.value, selectB.value);
  }
  selectA.addEventListener("change", paintPair);
  selectB.addEventListener("change", paintPair);
  series.addEventListener("change", () => {
    compareSeries = series.value;
    renderCompare(selectA.value, selectB.value);
  });

  const views = [
    ["overview", "Overview"],
    ["time", "Time of day"],
    ["distance", "Distance"],
    ["weekday", "Day of week"],
    ["permits", "Permits"],
  ];
  const viewButtons = views.map(([id, label]) => {
    const button = el("button", {
      type: "button",
      "aria-pressed": compareView === id ? "true" : "false",
    }, [text(label)]);
    button.addEventListener("click", () => {
      if (compareView === id) return;
      compareView = id;
      renderCompare(selectA.value, selectB.value);
    });
    return button;
  });
  const viewBar = el("div", { className: "compare-controls" }, [
    el("label", { className: "compare-series" }, [text("Series"), series]),
    el("div", { className: "compare-views", role: "group", "aria-label": "What to compare" }, viewButtons),
  ]);

  const note = compareSeriesNote();
  const bodyA = el("div");
  const bodyB = el("div");
  const columns = el("div", { className: "compare-columns" }, [
    el("article", { className: "compare-column" }, [venueBar("Venue A", selectA), bodyA]),
    el("article", { className: "compare-column" }, [venueBar("Venue B", selectB), bodyB]),
  ]);
  const sheet = el("div", { className: compareView === "permits" ? "compare-sheet is-permits" : "compare-sheet" }, [
    el("div", { className: "compare-toolbar" }, [
      el("button", { className: "venue-back", type: "button", "data-close-compare": "true" }, [text("Back to map")]),
      el("button", { className: "compare-link", type: "button" }, [text("Compare")]),
    ]),
    el("h2", { className: "compare-kicker", id: "compare-heading" }, [text("Compare Venues")]),
    viewBar,
    note ? el("p", { className: "muted" }, [text(note)]) : el("span"),
    columns,
  ]);
  comparePage.replaceChildren(sheet);
  syncCompareLink();
  comparePage.scrollTop = 0;

  if (!leftId || !rightId) {
    bodyA.replaceChildren(el("p", { className: "muted" }, [text("Venue list is still loading.")]));
    return;
  }
  if (leftId === rightId) {
    bodyA.replaceChildren(el("p", { className: "muted" }, [text("Choose two different venues.")]));
    return;
  }

  bodyA.replaceChildren(el("p", { className: "muted" }, [text("Loading…")]));
  Promise.all([loadCompareVenue(leftId), loadCompareVenue(rightId)]).then(([left, right]) => {
    if (token !== compareToken) return;
    const overlap = pairOverlapNote(left, right);
    if (overlap) columns.after(el("p", { className: "terms compare-overlap" }, [text(overlap)]));
    bodyA.replaceChildren(compareColumn(left));
    bodyB.replaceChildren(compareColumn(right));
  }).catch((error) => {
    if (token !== compareToken) return;
    bodyA.replaceChildren(el("p", { className: "muted" }, [text(error.message)]));
  });
}

function openCompare(options = {}) {
  closeVenuePage({ history: false });
  const route = options.a || options.b ? options : (compareRoute() || {});
  const [left, right] = resolveComparePair(route.a || options.a || "", route.b || options.b || "");
  const next = left && right ? `#/compare/${left}/${right}` : "#/compare";
  if (options.history === false) {
    if (location.hash !== next) history.replaceState({ compare: true }, "", next);
  } else {
    rememberCompare(left, right);
  }
  syncCompareLink();
  comparePage.hidden = false;
  renderCompare(left, right);
}

comparePage.addEventListener("click", (event) => {
  if (event.target.closest("[data-close-compare]")) closeCompare();
});
document.addEventListener("click", (event) => {
  if (event.target.closest(".compare-link")) openCompare();
});

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
  const compare = compareRoute();
  if (compare) openCompare({ history: false, a: compare.a, b: compare.b });
  else {
    const routed = venueIdFromLocation();
    if (routed) selectVenue(routed, { expand: true, history: false });
  }
}

window.addEventListener("popstate", () => {
  const compare = compareRoute();
  if (compare) {
    openCompare({ history: false, a: compare.a, b: compare.b });
    return;
  }
  if (comparePage) comparePage.hidden = true;
  syncCompareLink();
  const routed = venueIdFromLocation();
  if (routed) selectVenue(routed, { expand: true, history: false });
  else closeVenuePage({ history: false });
});

init();
