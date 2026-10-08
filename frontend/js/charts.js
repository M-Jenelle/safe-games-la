import { MONTH_NAMES, el, formatNumber, monthLabel, text } from "./dom.js";
import { bindChartHover, chartTip, ensureChartTip, hideChartTip, pageCopy, placeChartTip } from "./tips.js";

export function filledMonths(byMonth) {
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

export function monthChart(entries, peakKey, onFocus, word = "incidents", estimates = []) {
  hideChartTip();
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "chart");
  svg.setAttribute("viewBox", "0 0 960 200");
  svg.setAttribute("role", "img");
  if (!entries.length) {
    svg.setAttribute("aria-label", "No dated incidents in this buffer");
    return svg;
  }
  const bars = [
    ...entries.map((entry) => ({ ...entry, estimate: false })),
    ...estimates.map((entry) => ({ ...entry, estimate: true })),
  ];
  const max = Math.max(...bars.map((entry) => entry.count), 1);
  const peak = entries.find((entry) => entry.key === peakKey) || entries[0];
  const estimateNote = estimates.length
    ? ` Estimates for ${monthLabel(estimates[0].key)} to ${monthLabel(estimates[estimates.length - 1].key)} are seasonal averages, not recorded crime.`
    : "";
  svg.setAttribute(
    "aria-label",
    `Monthly ${word} from ${monthLabel(entries[0].key)} to ${monthLabel(entries[entries.length - 1].key)}. Peak ${monthLabel(peak.key)} with ${formatNumber(peak.count)}.${estimateNote}`,
  );
  const width = 960 / bars.length;
  let labeledYear = "";
  bars.forEach((entry, index) => {
    const height = Math.max((entry.count / max) * 168, entry.count ? 2 : 0);
    const x = index * width + 0.6;
    const barWidth = Math.max(width - 1.2, 0.6);
    const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    rect.setAttribute("x", String(x));
    rect.setAttribute("y", String(176 - height));
    rect.setAttribute("width", String(barWidth));
    rect.setAttribute("height", String(height));
    if (entry.estimate) {
      rect.setAttribute("class", "is-estimate");
      rect.setAttribute("fill", "#b9b6b0");
    } else {
      rect.setAttribute("fill", entry.key === peakKey ? "#DF0024" : "#0085C7");
      rect.dataset.month = entry.key;
    }
    svg.append(rect);
    const hit = document.createElementNS("http://www.w3.org/2000/svg", "rect");
    hit.setAttribute("x", String(x));
    hit.setAttribute("y", "0");
    hit.setAttribute("width", String(barWidth));
    hit.setAttribute("height", "176");
    hit.setAttribute("fill", "transparent");
    if (entry.estimate) hit.setAttribute("class", "is-estimate");
    else hit.addEventListener("click", () => onFocus(entry.key));
    bindChartHover(hit, entry, entry.estimate ? "estimated records" : word);
    svg.append(hit);
    const year = entry.key.slice(0, 4);
    if (year !== labeledYear) {
      labeledYear = year;
      const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
      label.setAttribute("x", String(index * width));
      label.setAttribute("y", "194");
      label.setAttribute("fill", "#4e5963");
      label.setAttribute("font-size", "11");
      label.textContent = year;
      svg.append(label);
    }
  });
  svg.addEventListener("mouseleave", hideChartTip);
  return svg;
}

export function categoryRows(byCategory, total, options = {}) {
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

export function overviewChart(byMonth) {
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

export function yearTotals(entries) {
  const totals = new Map();
  for (const entry of entries) {
    const year = entry.key.slice(0, 4);
    totals.set(year, (totals.get(year) || 0) + entry.count);
  }
  return [...totals.entries()];
}

export function monthTable(entries, peakKey, activeKey, estimates = []) {
  const monthsPresent = [...new Set([
    ...entries.map((entry) => Number(entry.key.slice(5))),
    ...estimates.map((entry) => Number(entry.key.slice(5))),
  ])].sort((a, b) => a - b);
  const byYear = new Map();
  for (const entry of entries) {
    const [year, month] = entry.key.split("-");
    const monthNumber = Number(month);
    if (!byYear.has(year)) byYear.set(year, new Map());
    byYear.get(year).set(monthNumber, { ...entry, estimate: false });
  }
  for (const entry of estimates) {
    const [year, month] = entry.key.split("-");
    const monthNumber = Number(month);
    if (!byYear.has(year)) byYear.set(year, new Map());
    if (!byYear.get(year).has(monthNumber)) {
      byYear.get(year).set(monthNumber, { ...entry, estimate: true });
    }
  }
  const head = el("tr", {}, [
    el("th", {}, [text("Year")]),
    ...monthsPresent.map((month) => el("th", {}, [text(MONTH_NAMES[month - 1])])),
  ]);
  const rows = [...byYear.entries()].map(([year, months]) => el("tr", {}, [
    el("th", {}, [text(year)]),
    ...monthsPresent.map((month) => {
      const entry = months.get(month);
      if (entry?.estimate) {
        return el("td", { className: "is-estimate" }, [text(formatNumber(entry.count))]);
      }
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

function chartSection(host, title, children, id) {
  const nodes = children.filter(Boolean);
  const heading = { className: "section-title" };
  if (id) heading.id = id;
  if (host.classList.contains("chart-flat")) {
    host.replaceChildren(el("section", { className: "time-section", "aria-label": title }, [
      el("h3", heading, [text(title)]),
      ...nodes,
    ]));
    return;
  }
  const previous = host.querySelector("details.section-accordion");
  const details = el("details", { className: "section-accordion time-section" });
  details.open = Boolean(previous?.open);
  details.append(
    el("summary", {}, [el("h3", heading, [text(title)])]),
    ...nodes,
  );
  host.replaceChildren(details);
}

function mountDrillPie(host, options) {
  const slices = (options.slices || []).filter((slice) => slice.count);
  if (!slices.length) {
    if (!options.monthLabel) {
      host.replaceChildren();
      return;
    }
    chartSection(host, options.title, [
      el("p", { className: "muted" }, [text(`${options.monthLabel}. None in this month.`)]),
    ]);
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

  chartSection(host, options.title, [
    el("p", { className: "muted" }, [text(options.hint)]),
    row,
    el("p", { className: "terms" }, [text(options.disclaimer || "")]),
    options.guide ? el("p", { className: "muted pie-guide" }, [text(options.guide)]) : el("span"),
  ]);
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
    chartSection(host, options.title, [
      el("p", { className: "muted" }, [text(options.month ? `${options.month}. None in this month.` : "None in this range.")]),
    ]);
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
    chartSection(host, options.title, [
      el("p", { className: "muted" }, [text(options.month ? `${options.month}. ${hint}` : hint)]),
      el("div", { className: "pie-pair pie-pair-slots" }, blank[0] ? [crimeColumn, liveColumn] : [liveColumn, crimeColumn]),
    ]);
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

  chartSection(host, options.title, [
    el("p", { className: "muted" }, [text(options.month ? `${options.month}. ${hint}` : hint)]),
    el("div", { className: "pie-pair" }, columns),
  ]);
}

export function mountTimeSection(crimeTime, host, countWord, guide, monthLabel) {
  const periods = crimeTime?.periods || [];
  mountDrillPie(host, {
    title: "Crimes by Time of Day",
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

export function scopedWeekday(block, month) {
  if (!block) return null;
  if (!month) return block;
  const scoped = block.by_month?.[month];
  if (!scoped || !scoped.total) return { total: 0, days: [], disclaimer: block.disclaimer || "" };
  return { ...scoped, disclaimer: block.disclaimer || "" };
}

const WEEKDAY_ORDER = [
  ["mon", "Monday"],
  ["tue", "Tuesday"],
  ["wed", "Wednesday"],
  ["thu", "Thursday"],
  ["fri", "Friday"],
  ["sat", "Saturday"],
  ["sun", "Sunday"],
];

function addGroups(lists) {
  const counts = new Map();
  for (const groups of lists) {
    for (const group of groups || []) {
      const prev = counts.get(group.id) || { id: group.id, label: group.label, count: 0 };
      prev.count += group.count || 0;
      if (group.label) prev.label = group.label;
      counts.set(group.id, prev);
    }
  }
  return [...counts.values()].filter((group) => group.count).sort((left, right) => right.count - left.count || left.label.localeCompare(right.label));
}

function addSlices(blocks, key, order) {
  return order.map(([id, label]) => {
    const matches = blocks.map((block) => (block?.[key] || []).find((item) => item.id === id)).filter(Boolean);
    const groups = addGroups(matches.map((item) => item.groups));
    return {
      id,
      label: matches.find((item) => item.label)?.label || label,
      count: groups.reduce((sum, group) => sum + group.count, 0),
      groups,
    };
  });
}

function weekdaySummaryText(block) {
  if (!block?.total || !(block.days || []).length) return "";
  let busiest = block.days[0];
  for (const day of block.days.slice(1)) {
    if ((day.count || 0) > (busiest.count || 0)) busiest = day;
  }
  const weekend = block.weekend_count || 0;
  const share = block.total ? Math.floor((weekend / block.total) * 100 + 0.5) : 0;
  return `${busiest.label} is the busiest day, ${busiest.count.toLocaleString("en-US")} records. Weekend days are ${weekend.toLocaleString("en-US")} (${share}%).`;
}

export function blockForRange(block, start, end) {
  if (!block) return null;
  const months = Object.keys(block.by_month || {}).sort();
  const chosen = months.filter((key) => (!start || key >= start) && (!end || key <= end));
  if (!chosen.length) {
    return { total: 0, periods: [], bands: [], days: [], disclaimer: block.disclaimer || "" };
  }
  if (chosen.length === months.length && chosen[0] === months[0] && chosen[chosen.length - 1] === months[months.length - 1]) {
    return block;
  }
  const parts = chosen.map((key) => block.by_month[key]).filter(Boolean);
  const disclaimer = block.disclaimer || "";
  if (block.periods || parts.some((part) => part.periods)) {
    const periods = addSlices(parts, "periods", [
      ["night", "12am–6am"],
      ["morning", "6am–12pm"],
      ["afternoon", "12pm–6pm"],
      ["evening", "6pm–12am"],
    ]);
    return {
      total: parts.reduce((sum, part) => sum + (part.total || 0), 0),
      periods,
      disclaimer,
    };
  }
  if (block.bands || parts.some((part) => part.bands)) {
    const bands = addSlices(parts, "bands", [
      ["near", "Within 200 m"],
      ["mid", "200–400 m"],
      ["far", "400–800 m"],
    ]);
    return {
      total: parts.reduce((sum, part) => sum + (part.total || 0), 0),
      bands,
      disclaimer,
    };
  }
  const days = WEEKDAY_ORDER.map(([id, label]) => {
    const matches = parts.map((part) => (part.days || []).find((day) => day.id === id)).filter(Boolean);
    const groups = addGroups(matches.map((day) => day.groups));
    const count = matches.reduce((sum, day) => sum + (day.count || 0), 0);
    const row = { id, label: matches.find((day) => day.label)?.label || label, count };
    if (groups.length) row.groups = groups;
    return row;
  });
  const total = days.reduce((sum, day) => sum + day.count, 0);
  const weekend = days.filter((day) => day.id === "sat" || day.id === "sun").reduce((sum, day) => sum + day.count, 0);
  const ranged = {
    total,
    weekday_count: total - weekend,
    weekend_count: weekend,
    days,
    disclaimer,
  };
  ranged.summary = weekdaySummaryText(ranged);
  return ranged;
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

export function weekdaySummary(block) {
  return block?.summary || "";
}

function weekdayHasGroups(block) {
  return (block?.days || []).some((day) => (day.groups || []).length);
}

export function mountWeekdaySection(block, host, countWord, monthLabel, placement) {
  if (!block || (!block.total && !monthLabel)) {
    host.replaceChildren();
    return;
  }
  if (!block.total) {
    chartSection(host, "Day of Week", [
      el("p", { className: "muted" }, [text(`${monthLabel}. None in this month.`)]),
    ], "section-weekday");
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
  chartSection(host, "Day of Week", [
    el("p", { className: "muted" }, [text(`${when}${weekdaySummary(block)}`)]),
    clickable ? el("p", { className: "muted" }, [text(pageCopy("weekday_click"))]) : el("span"),
    row,
    block.disclaimer ? el("p", { className: "terms" }, [text(block.disclaimer)]) : el("span"),
  ], "section-weekday");
}

export function mountWeekdayPair(host, reports, nibrs, monthLabel) {
  const panels = [
    { label: "2020–2024 reports", block: reports, countWord: "incidents" },
    { label: "NIBRS offenses, Mar 2024–present", block: nibrs, countWord: "offenses" },
  ].filter((panel) => panel.block?.total);
  if (!panels.length) {
    chartSection(host, "Day of Week", [
      el("p", { className: "muted" }, [text(monthLabel ? `${monthLabel}. None in this month.` : "None in this range.")]),
    ], "section-weekday");
    return;
  }
  const clickable = panels.some((panel) => weekdayHasGroups(panel.block));
  const note = monthLabel ? `${monthLabel}. Yellow bars are Saturday and Sunday.` : "Yellow bars are Saturday and Sunday.";
  chartSection(host, "Day of Week", [
    el("p", { className: "muted" }, [text(clickable ? `${note} ${pageCopy("weekday_click")}` : note)]),
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
        el("p", { className: "muted" }, [text(weekdaySummary(panel.block))]),
        row,
        panel.block.disclaimer ? el("p", { className: "terms" }, [text(panel.block.disclaimer)]) : el("span"),
      ]);
    })),
  ], "section-weekday");
}

export function mountDistanceSection(crimeDistance, host, countWord, guide, monthLabel) {
  const bands = crimeDistance?.bands || [];
  mountDrillPie(host, {
    title: "Distance from the Venue",
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

export function scopedBlock(block, month) {
  if (!month) return block || {};
  const scoped = block?.by_month?.[month];
  if (!scoped) return { total: 0, periods: [], bands: [], disclaimer: block?.disclaimer || "" };
  return { ...scoped, disclaimer: block?.disclaimer || "" };
}

export function mountTimePair(host, reports, nibrs, month) {
  mountPairedPies(host, {
    title: "Crimes by Time of Day",
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

export function mountDistancePair(host, reports, nibrs, month) {
  mountPairedPies(host, {
    title: "Distance from the Venue",
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
