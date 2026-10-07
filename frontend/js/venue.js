import { categoryRows, filledMonths, monthChart, monthTable, mountDistancePair, mountDistanceSection, mountTimePair, mountTimeSection, mountWeekdayPair, mountWeekdaySection, scopedBlock, scopedWeekday, yearTotals } from "./charts.js";
import { MONTH_NAMES, el, fetchJson, formatDistance, formatNumber, formatSports, monthLabel, monthTitle, text } from "./dom.js";
import { renderMap } from "./map.js";
import { closeVenuePage, openVenuePage, renderChatContext, venueToolbar } from "./nav.js";
import { mountHomeGames, mountListingSection, mountPermitSection } from "./permits.js";
import { renderRoster } from "./roster.js";
import { FLAG_LABELS, detail, overview, requests, state, zoneFilter } from "./state.js";
import { careStations, cityStats, densityHint, densityNote, incidentNote, overlapNote, presentCount, presentRate, renderOverview, stat } from "./stats.js";
import { pageCopy } from "./tips.js";

export function renderDetail() {
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
    const guide = nibrsSeries || month ? "" : pageCopy("pie_guide");
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
    const show = venue.home_games_available && seriesId !== "nibrs";
    homeHost.hidden = !show;
    if (show && !homeHost.dataset.loaded) {
      homeHost.dataset.loaded = "true";
      mountHomeGames(homeHost, venue.venue_id);
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
  const openedSection = (location.hash.match(/^#\/venue\/[^/]+\/(categories|weekday|months)/) || [])[1];
  if (openedSection && venue.merged_by_month) {
    seriesId = "merged";
    seriesSelect.value = "merged";
  }
  applySeries();

  detail.hidden = false;
  detail.scrollTop = 0;
  detail.replaceChildren(el("div", { className: "venue-sheet" }, [
    venueToolbar(),
    el("p", { className: "zone" }, [text(`${venue.olympic_zone} · ${formatSports(venue.sports)}`)]),
    el("h2", { className: "detail-title", id: "detail-heading" }, [text(venue.venue_name)]),
    el("p", { className: "address" }, [text(`${venue.address}, ${venue.city}`)]),
    el("div", { className: "stats" }, [
      stat("Incidents", formatNumber(total), incidentNote(venue)),
      stat("Density", formatNumber(presentRate(venue)), densityNote(venue), densityHint(venue)),
      ...cityStats(venue),
      stat("Busiest month", peak?.key ? monthLabel(peak.key) : "None", peak?.count ? `${formatNumber(peak.count)} records` : "No dated records"),
    ]),
    venue.nibrs ? el("p", { className: "muted" }, [
      text(`NIBRS offenses since Mar 2024: ${formatNumber(venue.nibrs.count)}. A case can include more than one offense.`),
    ]) : el("span"),
    flags.length ? el("div", { className: "flags" }, flags) : el("span"),
    overlapNote(venue) ? el("p", { className: "terms" }, [text(overlapNote(venue))]) : el("span"),
    el("h3", { className: "section-title", id: "section-months" }, [text("Incidents by month")]),
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
    el("h3", { className: "section-title", id: "section-categories" }, [text("Incident types")]),
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
  const sectionId = {
    categories: "section-categories",
    weekday: "section-weekday",
    months: "section-months",
  }[openedSection];
  const section = sectionId && document.getElementById(sectionId);
  if (section) section.scrollIntoView({ block: "start" });
}

export async function selectVenue(venueId, options = {}) {
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
  const token = ++requests.token;
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
    if (token !== requests.token) return;
    state.detail = venue;
    renderOverview();
    if (expand || !detail.hidden) openVenuePage({ history: options.history !== false });
  } catch (error) {
    if (token !== requests.token) return;
    overview.replaceChildren(
      el("div", { className: "detail-head" }, [
        el("h2", { className: "detail-title", id: "overview-heading" }, [text("Briefing")]),
        el("button", { className: "close-briefing", type: "button", "data-close-briefing": "true" }, [text("Close")]),
      ]),
      el("p", { className: "empty-detail" }, [text(error.message)]),
    );
  }
}
