import { el, formatNumber, monthTitle, text } from "./dom.js";
import { state } from "./state.js";

let infoTip = null;
let infoAnchor = null;
let infoPinned = false;
let infoHideTimer = 0;
export let chartTip = null;

function ensureInfoTip() {
  if (infoTip) return infoTip;
  infoTip = el("div", { className: "info-tip", role: "tooltip", hidden: "hidden" });
  document.body.append(infoTip);
  infoTip.addEventListener("mouseenter", () => window.clearTimeout(infoHideTimer));
  infoTip.addEventListener("mouseleave", () => {
    if (!infoPinned) scheduleInfoHide();
  });
  document.addEventListener("click", (event) => {
    if (!infoTip || infoTip.hidden) return;
    if (event.target.closest(".info-mark, .info-tip")) return;
    hideInfoTip(true);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") hideInfoTip(true);
  });
  window.addEventListener("scroll", () => {
    hideInfoTip(true);
    hideChartTip();
  }, true);
  return infoTip;
}

function placeInfoTip(anchor) {
  const tip = ensureInfoTip();
  const rect = anchor.getBoundingClientRect();
  const margin = 8;
  tip.hidden = false;
  const width = tip.offsetWidth;
  const height = tip.offsetHeight;
  let left = rect.left + (rect.width / 2) - (width / 2);
  let top = rect.bottom + margin;
  if (left + width > window.innerWidth - margin) left = window.innerWidth - width - margin;
  if (left < margin) left = margin;
  if (top + height > window.innerHeight - margin) top = Math.max(margin, rect.top - height - margin);
  tip.style.left = `${Math.round(left)}px`;
  tip.style.top = `${Math.round(top)}px`;
}

function showInfoTip(anchor, message, pinned) {
  window.clearTimeout(infoHideTimer);
  const tip = ensureInfoTip();
  if (infoAnchor && infoAnchor !== anchor) infoAnchor.setAttribute("aria-expanded", "false");
  infoAnchor = anchor;
  infoPinned = pinned;
  tip.textContent = message;
  anchor.setAttribute("aria-expanded", "true");
  placeInfoTip(anchor);
}

function hideInfoTip(force) {
  window.clearTimeout(infoHideTimer);
  if (!infoTip || infoTip.hidden) return;
  if (infoPinned && !force) return;
  infoTip.hidden = true;
  if (infoAnchor) infoAnchor.setAttribute("aria-expanded", "false");
  infoAnchor = null;
  infoPinned = false;
}

function scheduleInfoHide() {
  window.clearTimeout(infoHideTimer);
  infoHideTimer = window.setTimeout(() => hideInfoTip(false), 160);
}

export function infoMark(message) {
  const button = el("button", {
    type: "button",
    className: "info-mark",
    "aria-label": "What this means",
    "aria-expanded": "false",
  }, [text("i")]);
  button.addEventListener("mouseenter", () => showInfoTip(button, message, infoAnchor === button && infoPinned));
  button.addEventListener("mouseleave", () => {
    if (infoAnchor === button && !infoPinned) scheduleInfoHide();
  });
  button.addEventListener("focus", () => showInfoTip(button, message, false));
  button.addEventListener("blur", () => {
    if (infoAnchor === button && !infoPinned) scheduleInfoHide();
  });
  button.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (infoAnchor === button && infoPinned) hideInfoTip(true);
    else showInfoTip(button, message, true);
  });
  return button;
}

export function pageCopy(key) {
  return state.meta?.copy?.[key] || "";
}

export function withInfo(label, group) {
  const hints = group === "weather" ? state.meta?.copy?.weather_hints : state.meta?.copy?.permit_hints;
  const hint = hints?.[label];
  if (!hint) return text(label);
  return el("span", { className: "info-label" }, [text(label), infoMark(hint)]);
}

export function ensureChartTip() {
  if (chartTip) return chartTip;
  chartTip = el("div", { className: "chart-tip", hidden: "hidden" });
  document.body.append(chartTip);
  return chartTip;
}

export function placeChartTip(event) {
  const tip = ensureChartTip();
  tip.hidden = false;
  const margin = 8;
  const width = tip.offsetWidth;
  const height = tip.offsetHeight;
  let left = event.clientX + 14;
  let top = event.clientY - height - 12;
  if (left + width > window.innerWidth - margin) left = event.clientX - width - 14;
  if (left < margin) left = margin;
  if (top < margin) top = event.clientY + 16;
  tip.style.left = `${Math.round(left)}px`;
  tip.style.top = `${Math.round(top)}px`;
}

function showChartTip(event, key, count, word) {
  const tip = ensureChartTip();
  tip.replaceChildren(
    el("strong", {}, [text(monthTitle(key))]),
    el("span", {}, [text(`${formatNumber(count)} ${word}`)]),
  );
  placeChartTip(event);
}

export function hideChartTip() {
  if (chartTip) chartTip.hidden = true;
}

export function bindChartHover(svg, entry, word) {
  const show = (event) => showChartTip(event, entry.key, entry.count, word);
  svg.addEventListener("mouseenter", show);
  svg.addEventListener("mousemove", (event) => {
    if (!chartTip || chartTip.hidden) show(event);
    else placeChartTip(event);
  });
}
