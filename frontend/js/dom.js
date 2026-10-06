export function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (key === "className") node.className = value;
    else node.setAttribute(key, value);
  }
  for (const child of children) node.append(child);
  return node;
}

export function text(value) {
  return document.createTextNode(value == null ? "" : String(value));
}

export async function fetchJson(path) {
  const response = await fetch(path);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detailText = typeof body.detail === "string" ? body.detail : response.statusText;
    throw new Error(detailText);
  }
  return body;
}

export function formatNumber(value) {
  return Number(value).toLocaleString("en-US");
}

export function formatDistance(meters) {
  if (meters == null) return "Unknown distance";
  if (meters < 1000) return `${Math.round(meters)} m`;
  return `${(meters / 1000).toFixed(1)} km`;
}

export function formatSports(value) {
  return String(value || "")
    .split(";")
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => part.replace(
      /(^|[^A-Za-z0-9])([a-z])/g,
      (_, boundary, letter) => boundary + letter.toUpperCase(),
    ))
    .join(" · ");
}

export function formatWhen(iso) {
  if (!iso) return "unknown date";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  });
}

export function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
  }[character]));
}

export const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MONTH_FULL = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];

export function monthLabel(key) {
  const [year, month] = key.split("-");
  return `${MONTH_NAMES[Number(month) - 1]} ${year}`;
}

export function monthTitle(key) {
  const [year, month] = key.split("-");
  return `${MONTH_FULL[Number(month) - 1]} ${year}`;
}

export function ordinal(value) {
  const number = Number(value);
  const mod100 = number % 100;
  if (mod100 >= 11 && mod100 <= 13) return `${number}th`;
  return `${number}${["th", "st", "nd", "rd"][number % 10] || "th"}`;
}
