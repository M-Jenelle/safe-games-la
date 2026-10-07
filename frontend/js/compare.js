import { mountDistancePair, mountDistanceSection, mountTimePair, mountTimeSection, mountWeekdayPair, mountWeekdaySection, weekdaySummary } from "./charts.js";
import { el, fetchJson, formatNumber, formatSports, monthLabel, text } from "./dom.js";
import { closeVenuePage } from "./nav.js";
import { mountHomeGames, mountPermitSection } from "./permits.js";
import { resizeMap } from "./shell.js";
import { comparePage, state } from "./state.js";
import { cityStats, densityHint, densityNote, incidentNote, presentCount, presentRate, stat } from "./stats.js";
import { selectVenue } from "./venue.js";

let compareSeries = "reports";
let compareView = "overview";
let compareToken = 0;
const compareCache = new Map();

export function compareRoute() {
  if (!location.hash.startsWith("#/compare")) return null;
  const match = location.hash.match(/^#\/compare\/([A-Za-z0-9_-]+)\/([A-Za-z0-9_-]+)$/);
  return { a: match ? match[1] : "", b: match ? match[2] : "" };
}

export function syncCompareLink() {
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

export function closeCompare(options = {}) {
  compareToken += 1;
  if (comparePage) comparePage.hidden = true;
  if (options.history !== false && location.hash.startsWith("#/compare")) {
    history.pushState({}, "", `${location.pathname}${location.search}`);
  }
  syncCompareLink();
  resizeMap();
}

function pairOverlapNote(left, right) {
  return left?.display?.overlap_with?.[right.venue_id]
    || right?.display?.overlap_with?.[left.venue_id]
    || "";
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

function compareIdentity(venue) {
  const peak = venue.present?.peak_month
    ? { key: venue.present.peak_month, count: venue.present.peak_count }
    : null;
  return el("section", { className: "crime-unit identity-unit", "aria-label": venue.venue_name }, [
    el("p", { className: "zone" }, [text(`${venue.olympic_zone} · ${formatSports(venue.sports)}`)]),
    el("h2", { className: "detail-title" }, [text(venue.venue_name)]),
    venue.address ? el("p", { className: "address" }, [text(`${venue.address}, ${venue.city}`)]) : el("span"),
    el("div", { className: "stats" }, [
      stat("Incidents", formatNumber(presentCount(venue)), incidentNote(venue)),
      stat("Density", formatNumber(presentRate(venue)), densityNote(venue), densityHint(venue)),
      ...cityStats(venue),
      stat("Busiest month", peak?.key ? monthLabel(peak.key) : "None", peak?.count ? `${formatNumber(peak.count)} records` : "No dated records"),
    ]),
    ...overviewWeekdayLines(venue).map((line) => el("p", { className: "muted" }, [text(line)])),
  ]);
}

function overviewWeekdayLines(venue) {
  if (compareSeries === "nibrs") {
    const line = weekdayLine(venue.nibrs_weekday);
    return line ? [line] : [];
  }
  if (compareSeries === "merged") {
    const reports = weekdayLine(venue.crime_weekday);
    const nibrs = weekdayLine(venue.nibrs_weekday);
    return [
      reports ? `2020–2024: ${reports}` : "",
      nibrs ? `NIBRS: ${nibrs}` : "",
    ].filter(Boolean);
  }
  const line = weekdayLine(venue.crime_weekday);
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

function weekdayLine(block) {
  if (!block?.total || !(block.days || []).length) return "";
  return weekdaySummary(block);
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
      compareIdentity(venue),
    ]);
  }
  if (compareView === "permits") {
    const permitHost = el("div");
    const homeHost = el("div");
    const nibrsSeries = compareSeries === "nibrs";
    mountPermitSection(venue.venue_id, permitHost, nibrsSeries ? "nibrs" : "reports");
    if (venue.home_games_available && !nibrsSeries) mountHomeGames(homeHost, venue.venue_id);
    return el("div", { className: "compare-body" }, [
      el("section", { className: "crime-unit", "aria-label": "Permit days" }, [
        permitHost,
        homeHost,
      ]),
    ]);
  }
  const host = el("div", { className: "chart-flat" });
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
    el("section", { className: "crime-unit", "aria-label": venue.venue_name }, [host]),
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

export function openCompare(options = {}) {
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
