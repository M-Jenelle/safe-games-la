import { closeCompare } from "./compare.js";
import { renderMap } from "./map.js";
import { clearSelection, closeVenuePage, openVenuePage } from "./nav.js";
import { renderRoster } from "./roster.js";
import { appShell, comparePage, detail, overview, roster, rosterBackdrop, rosterList, rosterToggle, rosterToggleLabel, sortMode, state, zoneFilter } from "./state.js";
import { selectVenue } from "./venue.js";

const NARROW_ROSTER = 980;
let rosterNarrow = window.innerWidth <= NARROW_ROSTER;

function rosterIsNarrow() {
  return window.innerWidth <= NARROW_ROSTER;
}

function syncHeaderHeight() {
  const header = document.querySelector(".topbar");
  if (header) document.documentElement.style.setProperty("--header-h", `${header.offsetHeight}px`);
}

export function resizeMap() {
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

window.addEventListener("resize", () => {
  syncHeaderHeight();
  const narrow = rosterIsNarrow();
  if (narrow !== rosterNarrow) {
    rosterNarrow = narrow;
    setRosterCollapsed(narrow);
  }
  resizeMap();
});
