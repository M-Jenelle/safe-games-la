import { el, fetchJson, formatNumber, text } from "./dom.js";
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

function info(label) {
  return withInfo(label, "weather");
}

function weatherTable(rows) {
  const labels = ["", "Days", "Other Days", "Mean", "Other-Day Mean", "Median", "Difference"];
  return el("div", { className: "month-table-wrap" }, [
    el("table", { className: "month-table permit-table" }, [
      el("thead", {}, [el("tr", {}, labels.map((label) => el("th", {}, [info(label)])))]),
      el("tbody", {}, rows.map((row) => el("tr", {}, [
        el("th", { scope: "row" }, [info(row.label)]),
        el("td", {}, [text(formatNumber(row.event_day_count))]),
        el("td", {}, [text(formatNumber(row.other_day_count))]),
        el("td", {}, [text(formatMean(row.event_day_mean))]),
        el("td", {}, [text(formatMean(row.other_day_mean))]),
        el("td", {}, [text(`${formatMedian(row.event_day_median)} vs ${formatMedian(row.other_day_median)}`)]),
        el("td", {}, [text(formatGap(row))]),
      ]))),
    ]),
  ]);
}

function sectionClass(boxed) {
  return boxed ? "permit-section crime-unit" : "permit-section";
}

export function mountWeatherSection(host, venueId, boxed) {
  host.replaceChildren();
  fetchJson(`/api/venues/${encodeURIComponent(venueId)}/weather`).then((body) => {
    if (!body.available) {
      host.replaceChildren();
      return;
    }
    const rows = (body.comparisons || []).flatMap((block) => block.rows || []);
    const shown = rows.filter((row) => row.available);
    const notes = rows
      .filter((row) => !row.available && row.note)
      .map((row) => el("p", { className: "muted" }, [text(`${row.label}: ${row.note}`)]));
    host.replaceChildren(el("section", {
      className: sectionClass(boxed),
      "aria-label": "Weather",
    }, [
      el("h3", { className: "section-title" }, [info("Weather")]),
      el("p", { className: "muted" }, [text(body.intro || "")]),
      shown.length ? weatherTable(shown) : el("span"),
      ...notes,
      el("p", { className: "terms" }, [text(body.disclaimer || "")]),
    ]));
  }).catch(() => {
    host.replaceChildren();
  });
}
