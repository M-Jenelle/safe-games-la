import { MONTH_NAMES, el, fetchJson, formatNumber, text } from "./dom.js";
import { withInfo } from "./tips.js";

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

function headingLabel(label) {
  return {
    "One permit": "One Permit",
    "Several permits": "Several Permits",
    "Other days": "Other Days",
  }[label] || label;
}

function permitTable(rows) {
  const labels = ["", "Permit Days", "Other Days", "Permit-Day Mean", "Other-Day Mean", "Difference"];
  const bodyRows = rows.map((row, index) => el("tr", { className: index === 0 ? "is-overall" : "" }, [
    el("th", { scope: "row" }, [withInfo(headingLabel(row.label))]),
    el("td", {}, [text(formatNumber(row.event_day_count))]),
    el("td", {}, [text(formatNumber(row.other_day_count))]),
    el("td", {}, [text(formatMean(row.event_day_mean))]),
    el("td", {}, [text(formatMean(row.other_day_mean))]),
    el("td", {}, [text(formatGap(row))]),
  ]));
  return el("div", { className: "month-table-wrap" }, [
    el("table", { className: "month-table permit-table" }, [
      el("thead", {}, [el("tr", {}, labels.map((label) => el("th", {}, [withInfo(label)])))]),
      el("tbody", {}, bodyRows),
    ]),
  ]);
}

function groupRows(groups, eventLabel = "Permit-Day Mean") {
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
  const labels = ["", "Days", unit === "offenses" ? "Offenses per Day" : "Reports per Day", "Median", "Difference vs Other Days"];
  return el("div", { className: "month-table-wrap" }, [
    el("table", { className: "month-table permit-table" }, [
      el("thead", {}, [el("tr", {}, labels.map((label) => el("th", {}, [withInfo(label)])))]),
      el("tbody", {}, rows.map((row) => el("tr", {}, [
        el("th", { scope: "row" }, [withInfo(headingLabel(row.label))]),
        el("td", {}, [text(formatNumber(row.day_count))]),
        el("td", {}, [text(formatMaybeMean(row.mean))]),
        el("td", {}, [text(row.median == null ? "—" : formatMedian(row.median))]),
        el("td", {}, [text(row.label === "Other days" ? "—" : formatGap(row))]),
      ]))),
    ]),
  ]);
}

function sectionClass(boxed, extra) {
  return [extra, boxed ? "crime-unit" : ""].filter(Boolean).join(" ");
}

function loadAccordion(title, children) {
  const details = el("details", { className: "section-accordion" });
  details.append(
    el("summary", {}, [el("h3", { className: "section-title" }, [title])]),
    ...children,
  );
  return details;
}

export function mountPermitSection(venueId, host, source, boxed) {
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
        el("h3", { className: "section-title" }, [withInfo("Offense Groups")]),
        el("p", { className: "muted" }, [text(body.group_gap_note || "")]),
        groupList,
        groupToggle,
      ]
      : (body.group_gap_empty
        ? [el("p", { className: "muted" }, [text(body.group_gap_empty)])]
        : []);
    if (groups.length) paintGroups();
    const loadRows = body.permit_load || [];
    const loadIntro = el("p", { className: "muted" }, [text(body.load_intro || "")]);
    const loadTable = permitLoadTable(loadRows, unit);
    const loadBlock = !loadRows.length ? [] : boxed
      ? [loadAccordion(withInfo("Permits on the Same Day"), [loadIntro, loadTable])]
      : [
        el("h3", { className: "section-title" }, [withInfo("Permits on the Same Day")]),
        loadIntro,
        loadTable,
      ];
    host.replaceChildren(el("section", {
      className: sectionClass(boxed, "permit-section"),
      "aria-label": "Permit Days",
    }, [
      el("h3", { className: "section-title" }, [withInfo("Permit Days vs Other Days")]),
      el("p", { className: "muted" }, [text(body.intro || "")]),
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

function gameRow(label, summary) {
  return el("tr", { className: "is-overall" }, [
    el("th", { scope: "row" }, [withInfo(label)]),
    el("td", {}, [text(formatNumber(summary.event_day_count))]),
    el("td", {}, [text(formatNumber(summary.other_day_count))]),
    el("td", {}, [text(formatMean(summary.event_day_mean))]),
    el("td", {}, [text(formatMean(summary.other_day_mean))]),
    el("td", {}, [text(formatGap(summary))]),
  ]);
}

function gameTable(summary, label) {
  const labels = ["", "Game Days", "Other Days in Season", "Game-Day Mean", "Other-Day Mean in Season", "Difference"];
  const rows = [gameRow(label || "2020–2026", summary)];
  return el("div", { className: "month-table-wrap" }, [
    el("table", { className: "month-table permit-table" }, [
      el("thead", {}, [el("tr", {}, labels.map((label) => el("th", {}, [withInfo(label)])))]),
      el("tbody", {}, rows),
    ]),
  ]);
}

export function mountHomeGames(host, venueId, boxed) {
  host.replaceChildren();
  fetchJson(`/api/venues/${encodeURIComponent(venueId)}/home-games`).then((body) => {
    if (!body.available) {
      host.replaceChildren();
      return;
    }
    const groups = body.groups || [];
    host.replaceChildren(el("section", {
      className: sectionClass(boxed, "permit-section"),
      "aria-label": "Home Games",
    }, [
      el("h3", { className: "section-title" }, [withInfo("Home Games vs Other Days")]),
      el("p", { className: "muted" }, [text(body.note || "")]),
      gameTable(body.summary, body.period_label),
      groups.length
        ? el("h3", { className: "section-title" }, [withInfo("Game-Day Groups")])
        : el("span"),
      groups.length
        ? el("p", { className: "muted" }, [text(body.group_gap_note || "")])
        : el("span"),
      groups.length ? el("div", { className: "category-list" }, groupRows(groups, "Game-Day Mean")) : el("span"),
      el("p", { className: "terms" }, [text(body.disclaimer || "")]),
    ]));
  }).catch(() => {
    host.replaceChildren();
  });
}
