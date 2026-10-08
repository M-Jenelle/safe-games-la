import { categoryRows, filledMonths, monthChart, monthTable, mountDistanceSection, mountTimeSection, mountWeekdaySection, scopedBlock, scopedWeekday, yearTotals } from "./charts.js";
import { MONTH_NAMES, el, fetchJson, formatNumber, formatSports, monthLabel, monthTitle, text } from "./dom.js";
import { renderMap } from "./map.js";
import { closeVenuePage, openVenuePage, renderChatContext, venueToolbar } from "./nav.js";
import { mountHomeGames, mountListingSection, mountPermitSection } from "./permits.js";
import { renderRoster } from "./roster.js";
import { FLAG_LABELS, HIDDEN_FLAGS, detail, overview, requests, state, zoneFilter } from "./state.js";
import { careStations, cityStats, densityHint, densityNote, incidentNote, overlapNote, presentCount, presentRate, renderOverview, stat, transitStations } from "./stats.js";

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
  const flags = venue.data_quality_flags.filter((flag) => !HIDDEN_FLAGS.has(flag)).map((flag) => (
    el("span", { className: "flag" }, [text(FLAG_LABELS[flag] || flag)])
  ));
  let fromKey = "";
  let toKey = "";
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
  const fromYearSelect = el("select", { "aria-label": "From year" });
  const toYearSelect = el("select", { "aria-label": "To year" });
  toSelect.value = "12";
  const permitHost = el("div");
  const homeHost = el("div");
  const listingHost = el("div");
  const timeHost = el("div");
  const distanceHost = el("div");
  const weekdayHost = el("div");
  const chartHost = el("div", { className: "chart-host" });
  const yearHost = el("div", { className: "year-row" });
  const seriesHint = el("p", { className: "muted" });
  const estimateNote = el("p", { className: "muted" });

  function seriesData() {
    if (venue.merged_by_month) {
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

  function padMonth(value) {
    return String(value).padStart(2, "0");
  }

  function filteredMonths() {
    return seriesMonths().filter((entry) => entry.key >= fromKey && entry.key <= toKey);
  }

  function rangePeak() {
    const entries = filteredMonths();
    if (!entries.length) return null;
    return entries.reduce((best, entry) => (entry.count > best.count ? entry : best), entries[0]);
  }

  function rangeKeys() {
    return filteredMonths().map((entry) => entry.key);
  }

  function isFullRange() {
    const entries = seriesMonths();
    if (!entries.length) return true;
    return fromKey === entries[0].key && toKey === entries[entries.length - 1].key;
  }

  function rangeCaption() {
    if (!fromKey || !toKey) return "";
    if (fromKey === toKey) return monthLabel(fromKey);
    const fromYear = fromKey.slice(0, 4);
    const toYear = toKey.slice(0, 4);
    if (fromYear === toYear) {
      const yearEntries = seriesMonths().filter((entry) => entry.key.startsWith(`${fromYear}-`));
      if (yearEntries.length && fromKey === yearEntries[0].key && toKey === yearEntries[yearEntries.length - 1].key) {
        return fromYear;
      }
      return `${MONTH_NAMES[Number(fromKey.slice(5)) - 1]}–${MONTH_NAMES[Number(toKey.slice(5)) - 1]} ${fromYear}`;
    }
    return `${monthLabel(fromKey)}–${monthLabel(toKey)}`;
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
    const peakKey = rangePeak()?.key;
    detail.querySelectorAll("[data-month]").forEach((node) => {
      const active = node.dataset.month === key;
      node.classList.toggle("is-active", active);
      if (node.tagName === "rect") {
        node.setAttribute("fill", node.dataset.month === peakKey ? "#DF0024" : (active ? "#1a1a1a" : "#0085C7"));
      }
    });
  }

  function paintTable(estimates = []) {
    const bundle = seriesData();
    const filtered = filteredMonths();
    const peakKey = rangePeak()?.key;
    if (!filtered.length) {
      tableWrap.replaceChildren(el("p", { className: "muted" }, [text(bundle.empty)]));
      return;
    }
    const table = monthTable(filtered, peakKey, selectedKey, estimates);
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

  function yearChoices() {
    return [...new Set(seriesMonths().map((entry) => entry.key.slice(0, 4)))];
  }

  function rangeMode() {
    const entries = seriesMonths();
    if (!entries.length || !fromKey || !toKey) return "all";
    if (fromKey === entries[0].key && toKey === entries[entries.length - 1].key) return "all";
    const fromYear = fromKey.slice(0, 4);
    if (fromYear === toKey.slice(0, 4)) {
      const yearEntries = entries.filter((entry) => entry.key.startsWith(`${fromYear}-`));
      if (yearEntries.length && fromKey === yearEntries[0].key && toKey === yearEntries[yearEntries.length - 1].key) {
        return fromYear;
      }
    }
    return "custom";
  }

  function syncYearSelect() {
    const mode = rangeMode();
    const options = [
      el("option", { value: "all" }, [text("All years")]),
      ...yearChoices().map((year) => el("option", { value: year }, [text(year)])),
    ];
    if (mode === "custom") options.push(el("option", { value: "custom" }, [text("Custom")]));
    yearSelect.replaceChildren(...options);
    yearSelect.value = mode;
  }

  function setBoundsOnSelects() {
    if (!fromKey || !toKey) return;
    fromSelect.value = String(Number(fromKey.slice(5)));
    toSelect.value = String(Number(toKey.slice(5)));
    fromYearSelect.value = fromKey.slice(0, 4);
    toYearSelect.value = toKey.slice(0, 4);
    syncYearSelect();
  }

  function seasonalEstimates(byMonth) {
    const entries = filledMonths(byMonth);
    if (!entries.length) return [];
    const counts = new Map(entries.map((entry) => [entry.key, entry.count]));
    let year = Number(entries[entries.length - 1].key.slice(0, 4));
    let month = Number(entries[entries.length - 1].key.slice(5));
    const estimates = [];
    for (let step = 0; step < 3; step += 1) {
      month += 1;
      if (month === 13) {
        month = 1;
        year += 1;
      }
      const samples = [];
      for (let prior = year - 1; prior >= year - 3; prior -= 1) {
        const key = `${prior}-${padMonth(month)}`;
        if (counts.has(key)) samples.push(counts.get(key));
      }
      if (!samples.length) continue;
      const sum = samples.reduce((total, value) => total + value, 0);
      estimates.push({
        key: `${year}-${padMonth(month)}`,
        count: Math.floor((sum + Math.floor(samples.length / 2)) / samples.length),
      });
    }
    return withSchedule(estimates);
  }

  function daysInMonth(key) {
    return new Date(Number(key.slice(0, 4)), Number(key.slice(5)), 0).getDate();
  }

  function withSchedule(estimates) {
    const dates = venue.scheduled_home_games || [];
    const multiplier = Number(venue.home_game_multiplier);
    if (!dates.length || !Number.isFinite(multiplier)) return estimates;
    return estimates.map((entry) => {
      const games = dates.filter((day) => day.startsWith(entry.key)).length;
      if (!games) return entry;
      const days = daysInMonth(entry.key);
      const count = Math.round(entry.count * (1 + (games / days) * (multiplier - 1)));
      return { ...entry, count, scheduledGames: games };
    });
  }

  function paintSpan() {
    const bundle = seriesData();
    const entries = filteredMonths();
    const yearsNow = yearTotals(entries);
    const peakNow = rangePeak();
    const peakYearNow = yearsNow.reduce(
      (best, entry) => (entry[1] > best[1] ? entry : best),
      yearsNow[0] || ["", 0],
    );
    const series = seriesMonths();
    const lastKey = series.length ? series[series.length - 1].key : "";
    const estimates = lastKey && entries.some((entry) => entry.key === lastKey)
      ? seasonalEstimates(bundle.byMonth)
      : [];
    yearHost.replaceChildren(...yearsNow.map(([year, count]) => (
      el("article", { className: year === peakYearNow[0] ? "year-card is-peak" : "year-card" }, [
        el("span", {}, [text(year)]),
        el("strong", {}, [text(formatNumber(count))]),
      ])
    )));
    chartHost.replaceChildren(entries.length
      ? monthChart(entries, peakNow?.key, selectMonth, bundle.word, estimates)
      : el("p", { className: "muted" }, [text(bundle.empty)]));
    if (estimates.length) {
      estimateNote.hidden = false;
      const scheduleNote = venue.home_games_available
        ? (estimates.some((entry) => entry.scheduledGames)
          ? " Months with a listed home game are scaled by the count-model multiplier."
          : " No home game on the stored schedule falls in these months, so the home-game multiplier is not applied.")
        : "";
      estimateNote.textContent = `The gray bars and gray table numbers after ${monthLabel(lastKey)} are estimates: the average of that month in up to three earlier years. They are not recorded crime and not a certainty.${scheduleNote}`;
    } else {
      estimateNote.hidden = true;
      estimateNote.textContent = "";
    }
    paintTable(estimates);
    markMonth(selectedKey);
    paintCategories();
    paintPies();
    paintWeekday();
  }

  function readRange(changed) {
    const entries = seriesMonths();
    if (!entries.length) return;
    const first = entries[0].key;
    const last = entries[entries.length - 1].key;
    let start = `${fromYearSelect.value}-${padMonth(fromSelect.value)}`;
    let end = `${toYearSelect.value}-${padMonth(toSelect.value)}`;
    if (start < first) start = first;
    if (start > last) start = last;
    if (end < first) end = first;
    if (end > last) end = last;
    if (start > end) {
      if (changed === "from" || changed === "fromYear") end = start;
      else start = end;
    }
    fromKey = start;
    toKey = end;
    selectedKey = "";
    categoriesExpanded = false;
    setBoundsOnSelects();
    paintSpan();
  }

  function applyYearChoice() {
    const choice = yearSelect.value;
    if (choice === "custom") return;
    const entries = seriesMonths();
    if (!entries.length) return;
    if (choice === "all") {
      fromKey = entries[0].key;
      toKey = entries[entries.length - 1].key;
    } else {
      const yearEntries = entries.filter((entry) => entry.key.startsWith(`${choice}-`));
      if (!yearEntries.length) return;
      fromKey = yearEntries[0].key;
      toKey = yearEntries[yearEntries.length - 1].key;
    }
    selectedKey = "";
    categoriesExpanded = false;
    setBoundsOnSelects();
    paintSpan();
  }

  function seriesNote() {
    const hint = "Click a bar or a table cell to filter incident types to that month. Click it again to clear. Red is the busiest month.";
    return hint;
  }

  function presentChart(merged, reportsBlock, nibrsBlock, scope) {
    if (merged && (merged.total || merged.by_month)) {
      return { block: scope(merged, selectedKey), word: "records" };
    }
    if (!selectedKey) return { block: scope(reportsBlock, ""), word: "incidents" };
    const reports = scope(reportsBlock, selectedKey);
    if (reports?.total) return { block: reports, word: "incidents" };
    const nibrs = scope(nibrsBlock, selectedKey);
    if (nibrs?.total) return { block: nibrs, word: "offenses" };
    return { block: reports, word: "incidents" };
  }

  function paintPies() {
    const monthName = selectedKey ? monthTitle(selectedKey) : "";
    const time = presentChart(venue.merged_time, venue.crime_time, venue.nibrs_time, scopedBlock);
    const distance = presentChart(venue.merged_distance, venue.crime_distance, venue.nibrs_distance, scopedBlock);
    const guide = "";
    mountTimeSection(time.block, timeHost, time.word, guide, monthName);
    mountDistanceSection(distance.block, distanceHost, distance.word, guide, monthName);
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
    const picked = presentChart(venue.merged_weekday, venue.crime_weekday, venue.nibrs_weekday, scopedWeekday);
    mountWeekdaySection(picked.block, weekdayHost, picked.word, monthName);
  }

  function paintHomeGames() {
    const show = Boolean(venue.home_games_available);
    homeHost.hidden = !show;
    if (show && !homeHost.dataset.loaded) {
      homeHost.dataset.loaded = "true";
      mountHomeGames(homeHost, venue.venue_id, true);
    }
  }

  function applySeries() {
    const entries = seriesMonths();
    const options = () => yearChoices().map((year) => el("option", { value: year }, [text(year)]));
    fromYearSelect.replaceChildren(...options());
    toYearSelect.replaceChildren(...options());
    fromKey = entries[0]?.key || "";
    toKey = entries[entries.length - 1]?.key || "";
    seriesHint.textContent = seriesNote();
    setBoundsOnSelects();
    mountPermitSection(venue.venue_id, permitHost, "reports", true);
    paintHomeGames();
    paintSpan();
  }

  categoryToggle.addEventListener("click", () => {
    categoriesExpanded = !categoriesExpanded;
    paintCategories();
  });
  yearSelect.addEventListener("change", applyYearChoice);
  fromSelect.addEventListener("change", () => readRange("from"));
  toSelect.addEventListener("change", () => readRange("to"));
  fromYearSelect.addEventListener("change", () => readRange("fromYear"));
  toYearSelect.addEventListener("change", () => readRange("toYear"));
  const openedSection = (location.hash.match(/^#\/venue\/[^/]+\/(categories|weekday|months)/) || [])[1];
  applySeries();

  detail.hidden = false;
  detail.scrollTop = 0;
  detail.replaceChildren(el("div", { className: "venue-sheet" }, [
    venueToolbar(),
    el("section", { className: "crime-unit identity-unit", "aria-label": venue.venue_name }, [
      el("p", { className: "zone" }, [text(`${venue.olympic_zone} · ${formatSports(venue.sports)}`)]),
      el("h2", { className: "detail-title", id: "detail-heading" }, [text(venue.venue_name)]),
      el("p", { className: "address" }, [text(`${venue.address}, ${venue.city}`)]),
      el("div", { className: "stats" }, [
        stat("Incidents", formatNumber(total), incidentNote(venue)),
        stat("Density", formatNumber(presentRate(venue)), densityNote(venue), densityHint(venue)),
        ...cityStats(venue),
        stat("Busiest month", peak?.key ? monthLabel(peak.key) : "None", peak?.count ? `${formatNumber(peak.count)} records` : "No dated records"),
      ]),
    ]),
    flags.length ? el("div", { className: "flags" }, flags) : el("span"),
    overlapNote(venue) ? el("p", { className: "terms" }, [text(overlapNote(venue))]) : el("span"),
    el("section", { className: "crime-unit", "aria-label": "Crime patterns" }, [
      el("h3", { className: "section-title", id: "section-months" }, [text("Incidents by Month")]),
      el("div", { className: "table-filters" }, [
        el("label", {}, [text("Year"), yearSelect]),
        el("label", {}, [
          text("From"),
          el("span", { className: "range-pair" }, [fromSelect, fromYearSelect]),
        ]),
        el("label", {}, [
          text("To"),
          el("span", { className: "range-pair" }, [toSelect, toYearSelect]),
        ]),
      ]),
      seriesHint,
      yearHost,
      chartHost,
      estimateNote,
      tableWrap,
      el("h3", { className: "section-title", id: "section-categories" }, [text("Incident Types")]),
      categoryNote,
      categoryList,
      categoryToggle,
      timeHost,
      distanceHost,
      weekdayHost,
    ]),
    permitHost,
    homeHost,
    listingHost,
    el("section", { className: "crime-unit places-unit", "aria-label": "Transportation" }, [
      el("h3", { className: "section-title" }, [text("Transportation")]),
      el("div", { className: "station-grid" }, transitStations(venue)),
    ]),
    el("section", { className: "crime-unit places-unit", "aria-label": "Nearest Response and Care" }, [
      el("h3", { className: "section-title" }, [text("Nearest Response and Care")]),
      el("div", { className: "station-grid" }, careStations(venue)),
      el("p", { className: "terms" }, [text(state.meta.jurisdiction_method)]),
    ]),
  ]));
  mountListingSection(venue.ticketmaster, listingHost, true);
  const sectionId = {
    categories: "section-categories",
    weekday: "section-weekday",
    months: "section-months",
  }[openedSection];
  const section = sectionId && document.getElementById(sectionId);
  if (section) {
    const panel = section.closest("details");
    if (panel) panel.open = true;
    section.scrollIntoView({ block: "start" });
  }
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
