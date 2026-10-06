import { syncCompareLink } from "./compare.js";
import { el, text } from "./dom.js";
import { renderMap, syncCrimeHint } from "./map.js";
import { renderRoster } from "./roster.js";
import { resizeMap } from "./shell.js";
import { analysisLead, chatContext, comparePage, detail, mapHint, overview, requests, state } from "./state.js";
import { renderDetail } from "./venue.js";

export function renderChatContext() {
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

export function venueIdFromLocation() {
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

export function closeVenuePage(options = {}) {
  if (options.history !== false) forgetVenue();
  detail.hidden = true;
  resizeMap();
}

export function venueToolbar() {
  return el("div", { className: "venue-toolbar" }, [
    el("button", { className: "venue-back", type: "button", "data-close-briefing": "true" }, [text("Back to map")]),
    el("button", { className: "compare-link", type: "button" }, [text("Compare")]),
  ]);
}

export function openVenuePage(options = {}) {
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

export function clearSelection(options = {}) {
  requests.token += 1;
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
