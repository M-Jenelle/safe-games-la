import { blockForRange, mountDistanceSection, mountTimeSection, mountWeekdaySection, weekdaySummary } from "./charts.js";
import { MONTH_NAMES, el, fetchJson, formatNumber, formatSports, monthLabel, text } from "./dom.js";
import { closeVenuePage } from "./nav.js";
import { mountHomeGames, mountPermitSection } from "./permits.js";
import { mountWeatherSection } from "./weather.js";
import { resizeMap } from "./shell.js";
import { comparePage, state } from "./state.js";
import { cityStats, densityHint, densityNote, incidentNote, presentCount, presentRate, stat } from "./stats.js";
import { selectVenue } from "./venue.js";

let compareStart = "";
let compareEnd = "";
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

function compareRangeNote() {
  if (compareView === "permits") return "Permit days in this view use 2020–2024 reports.";
  if (compareView === "weather") return "Wet and hot days are compared with other days in the same months.";
  if (compareView === "overview" || !compareStart || !compareEnd) return "";
  const span = compareStart === compareEnd
    ? monthLabel(compareStart)
    : `${monthLabel(compareStart)}–${monthLabel(compareEnd)}`;
  return span;
}

function cachedMonths() {
  const keys = [];
  for (const venue of compareCache.values()) {
    keys.push(...Object.keys(venue.merged_by_month || {}));
  }
  return [...new Set(keys)].sort();
}

function padMonth(value) {
  return String(value).padStart(2, "0");
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
  const block = blockForRange(venue.merged_weekday || venue.crime_weekday, compareStart, compareEnd);
  const line = block?.summary || weekdayLine(block);
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
    mountPermitSection(venue.venue_id, permitHost, "reports");
    if (venue.home_games_available) mountHomeGames(homeHost, venue.venue_id);
    return el("div", { className: "compare-body" }, [
      el("section", { className: "crime-unit", "aria-label": "Permit days" }, [
        permitHost,
        homeHost,
      ]),
    ]);
  }
  if (compareView === "weather") {
    const weatherHost = el("div");
    mountWeatherSection(weatherHost, venue.venue_id);
    return el("div", { className: "compare-body" }, [
      el("section", { className: "crime-unit", "aria-label": "Weather" }, [weatherHost]),
    ]);
  }
  const host = el("div", { className: "chart-flat" });
  const span = compareRangeNote();
  if (compareView === "time") {
    mountTimeSection(blockForRange(venue.merged_time || venue.crime_time, compareStart, compareEnd), host, "records", "", span);
  } else if (compareView === "distance") {
    mountDistanceSection(blockForRange(venue.merged_distance || venue.crime_distance, compareStart, compareEnd), host, "records", "", span);
  } else {
    mountWeekdaySection(blockForRange(venue.merged_weekday || venue.crime_weekday, compareStart, compareEnd), host, "records", span, "below");
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
  const months = cachedMonths();
  const years = [...new Set((months.length ? months : ["2020-01", "2026-12"]).map((key) => key.slice(0, 4)))];
  if (!compareStart || !compareEnd) {
    compareStart = months[0] || "2020-01";
    compareEnd = months[months.length - 1] || "2026-12";
  }
  const yearSelect = el("select", { "aria-label": "Year" });
  const fromMonth = el("select", { "aria-label": "From month" }, MONTH_NAMES.map((name, index) => (
    el("option", { value: String(index + 1) }, [text(name)])
  )));
  const toMonth = el("select", { "aria-label": "To month" }, MONTH_NAMES.map((name, index) => (
    el("option", { value: String(index + 1) }, [text(name)])
  )));
  const fromYear = el("select", { "aria-label": "From year" }, years.map((year) => el("option", { value: year }, [text(year)])));
  const toYear = el("select", { "aria-label": "To year" }, years.map((year) => el("option", { value: year }, [text(year)])));
  const full = !months.length || (compareStart === months[0] && compareEnd === months[months.length - 1]);
  const sameYear = compareStart.slice(0, 4) === compareEnd.slice(0, 4);
  const yearMonths = months.filter((key) => key.startsWith(`${compareStart.slice(0, 4)}-`));
  const yearMode = full
    ? "all"
    : (sameYear && yearMonths.length && compareStart === yearMonths[0] && compareEnd === yearMonths[yearMonths.length - 1]
      ? compareStart.slice(0, 4)
      : "custom");
  yearSelect.replaceChildren(
    el("option", { value: "all" }, [text("All years")]),
    ...years.map((year) => el("option", { value: year }, [text(year)])),
    ...(yearMode === "custom" ? [el("option", { value: "custom" }, [text("Custom")])] : []),
  );
  yearSelect.value = yearMode;
  fromMonth.value = String(Number(compareStart.slice(5)));
  toMonth.value = String(Number(compareEnd.slice(5)));
  fromYear.value = compareStart.slice(0, 4);
  toYear.value = compareEnd.slice(0, 4);
  selectA.setAttribute("aria-label", "Venue A");
  selectB.setAttribute("aria-label", "Venue B");

  function paintPair() {
    rememberCompare(selectA.value, selectB.value);
    renderCompare(selectA.value, selectB.value);
  }
  selectA.addEventListener("change", paintPair);
  selectB.addEventListener("change", paintPair);
  function paintRange() {
    renderCompare(selectA.value, selectB.value);
  }
  yearSelect.addEventListener("change", () => {
    const known = cachedMonths();
    const choice = yearSelect.value;
    if (choice === "custom" || !known.length) return;
    if (choice === "all") {
      compareStart = known[0];
      compareEnd = known[known.length - 1];
    } else {
      const picked = known.filter((key) => key.startsWith(`${choice}-`));
      if (!picked.length) return;
      compareStart = picked[0];
      compareEnd = picked[picked.length - 1];
    }
    paintRange();
  });
  const onEdge = (changed) => {
    const known = cachedMonths();
    const first = known[0] || "2020-01";
    const last = known[known.length - 1] || "2026-12";
    let start = `${fromYear.value}-${padMonth(fromMonth.value)}`;
    let end = `${toYear.value}-${padMonth(toMonth.value)}`;
    if (start < first) start = first;
    if (end > last) end = last;
    if (known.length && !known.includes(start)) {
      start = known.find((month) => month >= start) || last;
    }
    if (known.length && !known.includes(end)) {
      const earlier = known.filter((month) => month <= end);
      end = earlier.length ? earlier[earlier.length - 1] : first;
    }
    if (start > end) {
      if (changed === "from") end = start;
      else start = end;
    }
    compareStart = start;
    compareEnd = end;
    paintRange();
  };
  fromMonth.addEventListener("change", () => onEdge("from"));
  fromYear.addEventListener("change", () => onEdge("from"));
  toMonth.addEventListener("change", () => onEdge("to"));
  toYear.addEventListener("change", () => onEdge("to"));

  const views = [
    ["overview", "Overview"],
    ["time", "Time of Day"],
    ["distance", "Distance"],
    ["weekday", "Day of Week"],
    ["permits", "Permits"],
    ["weather", "Weather"],
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
    el("div", { className: "table-filters compare-range" }, [
      el("label", {}, [text("Year"), yearSelect]),
      el("label", {}, [text("From"), el("span", { className: "range-pair" }, [fromMonth, fromYear])]),
      el("label", {}, [text("To"), el("span", { className: "range-pair" }, [toMonth, toYear])]),
    ]),
    el("div", { className: "compare-views", role: "group", "aria-label": "What to compare" }, viewButtons),
  ]);

  const note = compareRangeNote();
  const bodyA = el("div");
  const bodyB = el("div");
  const columns = el("div", { className: "compare-columns" }, [
    el("article", { className: "compare-column" }, [venueBar("Venue A", selectA), bodyA]),
    el("article", { className: "compare-column" }, [venueBar("Venue B", selectB), bodyB]),
  ]);
  const sheetClass = compareView === "permits" || compareView === "weather"
    ? "compare-sheet is-permits"
    : "compare-sheet";
  const sheet = el("div", { className: sheetClass }, [
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
    const known = cachedMonths();
    if (yearSelect.value === "all" && known.length && compareEnd !== known[known.length - 1]) {
      compareStart = known[0];
      compareEnd = known[known.length - 1];
      renderCompare(selectA.value, selectB.value);
      return;
    }
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
