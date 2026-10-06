import { el, escapeHtml, fetchJson, text } from "./dom.js";
import { resizeMap } from "./shell.js";
import { mapHint, mapPanel, state } from "./state.js";
import { pageCopy } from "./tips.js";
import { selectVenue } from "./venue.js";

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

export function renderMap() {
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

export function fillLayerView(name, options) {
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

export function bindLayerToggles() {
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

export async function ensureGoogleMap() {
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

let crimeRequest = 0;

export function bindFacilityViews() {
  for (const name of Object.keys(state.layerViews)) {
    const select = document.querySelector(`#${name}-view`);
    if (!select) continue;
    select.addEventListener("change", () => {
      state.layerViews[name] = select.value;
      if (state.layers[name]) syncOverlay();
    });
  }
}

export function bindCrimeView() {
  const select = document.querySelector("#crime-view");
  if (!select) return;
  select.addEventListener("change", () => {
    state.crimeView = select.value;
    loadCrimeHeat(select.value);
  });
}

export function syncCrimeHint() {
  if (!mapHint || mapHint.hidden) return;
  mapHint.textContent = state.crimeView === "nibrs"
    ? pageCopy("nibrs_crime_hint")
    : pageCopy("default_crime_hint");
}

export async function loadCrimeHeat(view = state.crimeView || "all") {
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
