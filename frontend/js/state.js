export const FLAG_LABELS = {
  no_crime_within_radius: "No crime reports in the buffer",
  no_rail_stations_within_radius: "No rail stations in the buffer",
  no_bus_stops_within_radius: "No bus stops in the buffer",
  no_fire_stations_loaded: "No fire stations in the dataset",
  no_police_stations_loaded: "No police stations in the dataset",
  no_hospitals_loaded: "No hospitals in the dataset",
  geocode_mismatch: "Listed coordinates differ from the geocoder",
  geocode_not_found: "Geocoder could not confirm this address",
  city_of_la_but_nearest_station_is_not_lapd: "Nearest station is not LAPD",
};

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
export const chatContext = document.querySelector("#chat-context");
export const analysisLead = document.querySelector("#analysis-lead");
export const chatPanel = document.querySelector("#chat-panel");
export const chatLauncher = document.querySelector("#chat-launcher");
export const chatClose = document.querySelector("#chat-close");

export const appShell = document.querySelector(".app");
export const roster = document.querySelector("#roster");
export const rosterToggle = document.querySelector("#roster-toggle");
export const rosterToggleLabel = rosterToggle.querySelector(".visually-hidden");
export const rosterBackdrop = document.querySelector("#roster-backdrop");
