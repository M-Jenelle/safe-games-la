export const state = {
  meta: null,
  map: null,
  googleMap: null,
  venueMarkers: [],
  heatOverlay: null,
  bufferCircle: null,
  facilities: null,
  crimePoints: null,
  crimeView: "all",
  crimeHot: false,
  crimeCache: {},
  heatStart: "",
  heatEnd: "",
  heatMonths: [],
  layerViews: {
    fire: "all",
    hospitals: "all",
    police: "all",
    rail: "all",
    bus: "all",
  },
  layers: {
    venues: true,
    crime: true,
    fire: false,
    hospitals: false,
    police: false,
    rail: false,
    bus: false,
  },
  layerInfo: null,
  tipKey: "",
  venues: [],
  selectedId: null,
  detail: null,
  zone: "all",
  sort: "density",
};

export const requests = { token: 0 };

export const rosterList = document.querySelector("#roster-list");
export const zoneFilter = document.querySelector("#zone-filter");
export const sortMode = document.querySelector("#sort-mode");
export const metaStrip = document.querySelector("#meta-strip");
export const overview = document.querySelector("#detail");
export const detail = document.querySelector("#venue-page");
export const comparePage = document.querySelector("#compare-page");
const mapFrame = document.querySelector("#map-frame");
export const mapPanel = document.querySelector(".map-panel");
export const mapHint = document.querySelector("#map-hint");
const ZOOM = 7;
export const appShell = document.querySelector(".app");
export const roster = document.querySelector("#roster");
export const rosterToggle = document.querySelector("#roster-toggle");
export const rosterToggleLabel = rosterToggle.querySelector(".visually-hidden");
export const rosterBackdrop = document.querySelector("#roster-backdrop");
