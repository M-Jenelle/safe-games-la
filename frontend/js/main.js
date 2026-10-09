import { compareRoute, openCompare, syncCompareLink } from "./compare.js";
import { fetchJson } from "./dom.js";
import { bindCrimeView, bindFacilityViews, bindHeatRange, bindLayerToggles, ensureGoogleMap, fillLayerView, loadCrimeHeat, renderMap } from "./map.js?v=heat-load";
import { closeVenuePage, renderChatContext, venueIdFromLocation } from "./nav.js";
import { renderMeta, renderRoster, renderZoneOptions } from "./roster.js";
import { comparePage, mapHint, metaStrip, state } from "./state.js";
import { pageCopy } from "./tips.js";
import { selectVenue } from "./venue.js";

async function init() {
  bindLayerToggles();
  bindCrimeView();
  bindHeatRange();
  bindFacilityViews();
  try {
    const [metaData, list, mapData, facilities] = await Promise.all([
      fetchJson("/api/meta"),
      fetchJson("/api/venues"),
      fetchJson("/api/map"),
      fetchJson("/api/map/layers").catch(() => null),
    ]);
    state.meta = metaData;
    if (mapHint && mapHint.textContent === "Loading the map.") {
      mapHint.textContent = pageCopy("default_crime_hint");
    }
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

function followLocation() {
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
}

window.addEventListener("popstate", followLocation);
window.addEventListener("hashchange", followLocation);

init();
