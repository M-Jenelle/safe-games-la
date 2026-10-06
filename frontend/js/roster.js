import { el, formatNumber, formatSports, formatWhen, text } from "./dom.js";
import { metaStrip, rosterList, state, zoneFilter } from "./state.js";

function visibleVenues() {
  const filtered = state.venues.filter((venue) => (
    state.zone === "all" || venue.olympic_zone === state.zone
  ));
  const sorted = [...filtered];
  if (state.sort === "name") {
    sorted.sort((a, b) => a.venue_name.localeCompare(b.venue_name));
  } else {
    sorted.sort((a, b) => b.crime_per_km2 - a.crime_per_km2);
  }
  return sorted;
}

export function jurisdictionLabel(value) {
  if (value === true) return "LAPD";
  if (value === false) return "Not LAPD";
  return "Unknown";
}

export function renderMeta() {
  if (!metaStrip || !state.meta) return;
  const metaData = state.meta;
  metaStrip.replaceChildren(
    el("p", { className: "meta-line" }, [
      text(`${Math.round(metaData.buffer_radius_m)} m buffer · ${metaData.venue_count} venues`),
    ]),
    el("p", { className: "meta-line" }, [
      text(`Updated ${formatWhen(metaData.generated_at)}`),
    ]),
    el("p", { className: "meta-line meta-quiet" }, [
      text(`Densest ${metaData.densest_venue.venue_name} (${formatNumber(metaData.densest_venue.crime_per_km2)}/km²) · quietest ${metaData.quietest_venue.venue_name}`),
    ]),
  );
}

export function renderZoneOptions() {
  const zones = [...new Set(state.venues.map((venue) => venue.olympic_zone))].sort();
  for (const zone of zones) {
    zoneFilter.append(el("option", { value: zone }, [text(zone)]));
  }
}

export function renderRoster() {
  const venues = visibleVenues();
  if (!venues.length) {
    rosterList.replaceChildren(el("p", { className: "muted" }, [text("No venues in this zone.")]));
    return;
  }
  rosterList.replaceChildren(...venues.map((venue) => {
    const selected = venue.venue_id === state.selectedId;
    return el("button", {
      className: selected ? "venue-card selected" : "venue-card",
      type: "button",
      "data-venue-id": venue.venue_id,
      "aria-pressed": selected ? "true" : "false",
    }, [
      el("div", { className: "card-top" }, [
        el("span", { className: "zone" }, [text(venue.olympic_zone)]),
        el("span", { className: "rate" }, [text(`${formatNumber(venue.crime_per_km2)}/km²`)]),
      ]),
      el("h3", {}, [text(venue.venue_name)]),
      el("div", { className: "sports" }, [text(formatSports(venue.sports))]),
      el("div", { className: "card-foot" }, [
        text(`${formatNumber(venue.crime_count_nearby)} incidents · ${jurisdictionLabel(venue.lapd_jurisdiction)}`),
      ]),
      venue.nibrs_count == null ? el("span") : el("div", { className: "card-foot" }, [
        text(`NIBRS · ${formatNumber(venue.nibrs_count)} offenses`),
      ]),
    ]);
  }));
  rosterList.querySelector(".venue-card.selected")?.scrollIntoView({ block: "nearest" });
}
