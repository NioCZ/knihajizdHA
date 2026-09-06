import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

class FakeElement {}

class FakeHTMLElement {
  attachShadow() {
    this.shadowRoot = {
      innerHTML: "",
      querySelector: () => null,
      querySelectorAll: () => [],
      getElementById: () => null,
    };
    return this.shadowRoot;
  }

  addEventListener() {}
}

globalThis.Element = FakeElement;
globalThis.HTMLElement = FakeHTMLElement;
globalThis.window = { confirm: () => true };
globalThis.customElements = {
  elements: new Map(),
  get(name) {
    return this.elements.get(name);
  },
  define(name, elementClass) {
    this.elements.set(name, elementClass);
  },
};

const panelPath = new URL(
  "../custom_components/kniha_jizd/frontend/kniha-jizd-panel.js",
  import.meta.url,
);
const panelSource = (await readFile(panelPath, "utf8")).replace(
  /^import\s+"\.\/kniha-jizd-map\.js\?v=[^"]+";\s*/,
  "",
);
await import(`data:text/javascript;base64,${Buffer.from(panelSource).toString("base64")}`);
const Panel = customElements.get("kniha-jizd-panel");
const panel = new Panel();
panel._render = () => {};
panel._syncMapElement = () => {};

panel._placesData = {
  configured_places: [
    { label: "Domov", place_role: "home", trip_type: "contextual", radius_m: 300 },
    { label: "Altium", place_role: "company", trip_type: "contextual", radius_m: 300 },
  ],
  places: [],
};
const configuredPlaceRows = panel._placesTable();
assert.equal(
  (configuredPlaceRows.match(/Podle směru jízdy/g) || []).length,
  2,
  "home and company must both use direction-dependent classification",
);

const trackedDetails = {
  dataset: { detailsKey: "input-checks" },
  open: true,
};
panel.shadowRoot.querySelectorAll = (selector) => (
  selector === "details[data-details-key]" ? [trackedDetails] : []
);
panel._captureInteractiveState();
assert.equal(panel._openDetails.has("input-checks"), true);
trackedDetails.open = false;
panel._captureInteractiveState();
assert.equal(panel._openDetails.has("input-checks"), false);

assert.ok(
  panelSource.indexOf("Hlavní přehled") < panelSource.indexOf("Technický stav"),
  "the trip table should be rendered before diagnostics",
);

let overviewGeneratedAt = "2026-09-02T08:00:00+00:00";
let overviewRenders = 0;
panel._render = () => { overviewRenders += 1; };
panel._hass = {
  async callApi(method, path) {
    assert.equal(method, "GET");
    assert.equal(path, "kniha_jizd/overview");
    return {
      generated_at: overviewGeneratedAt,
      diagnostics: { today_trips: [] },
      statistics: {},
    };
  },
};
await panel._loadOverviewData();
overviewGeneratedAt = "2026-09-02T08:00:01+00:00";
await panel._loadOverviewData();
assert.equal(overviewRenders, 1, "timestamp-only overview refresh should not rebuild the UI");
panel._render = () => {};

const historyRequests = [];
panel._hass = {
  callApi(method, path) {
    return new Promise((resolve) => historyRequests.push({ method, path, resolve }));
  },
};
panel._historyMonth = "2026-08";
panel._historyDate = "2026-08-24";
const firstHistory = panel._loadHistoryData();
panel._historyDate = "2026-08-25";
const latestHistory = panel._loadHistoryData();

historyRequests[1].resolve({ selected: "latest" });
await latestHistory;
historyRequests[0].resolve({ selected: "stale" });
await firstHistory;

assert.equal(panel._historyData.selected, "latest");
assert.equal(panel._historyLoading, false);

const tabRefreshRequests = [];
panel._mapData = { learned_places: [{ id: "stale-map-place" }] };
panel._placesData = { places: [{ id: "stale-managed-place" }] };
panel._hass = {
  async callApi(method, path) {
    tabRefreshRequests.push({ method, path });
    if (path === "kniha_jizd/map") {
      return { generated_at: "2026-09-06T08:00:00+00:00", learned_places: [{ id: "fresh-map-place" }] };
    }
    if (path === "kniha_jizd/places") {
      return { generated_at: "2026-09-06T08:00:00+00:00", places: [{ id: "fresh-managed-place" }] };
    }
    throw new Error(`unexpected API path ${path}`);
  },
};
await panel._selectTab("map");
await panel._selectTab("places");
assert.deepEqual(tabRefreshRequests, [
  { method: "GET", path: "kniha_jizd/map" },
  { method: "GET", path: "kniha_jizd/places" },
]);
assert.equal(panel._mapData.learned_places[0].id, "fresh-map-place");
assert.equal(panel._placesData.places[0].id, "fresh-managed-place");

const mapRequests = [];
panel._hass = {
  async callApi(method, path, payload) {
    mapRequests.push({ method, path, payload });
    if (method === "POST") return { data: { places: [] } };
    return { generated_at: "2026-08-25T12:00:00+00:00", learned_places: [] };
  },
};

await panel._deleteMapPlace({
  markerId: "place:1",
  placeId: "place",
  anchorIndex: 1,
  label: "Chybný bod",
});

assert.deepEqual(mapRequests[0], {
  method: "POST",
  path: "kniha_jizd/places",
  payload: {
    action: "delete_anchor",
    place_id: "place",
    anchor_index: 1,
  },
});
assert.equal(mapRequests[1].method, "GET");
assert.equal(mapRequests[1].path, "kniha_jizd/map");
assert.equal(panel._mapMessage, "Označený bod byl odstraněn.");

const staleDraft = {
  start: "Start",
  end: "Ruční cíl",
  distance: "12",
  purpose: "Klient",
  type: "business",
  confirmedAt: Date.now() - 60_000,
};
panel._tripDrafts.set("trip-1", staleDraft);
const staleHtml = panel._tripTable([
  {
    id: "trip-1",
    started_at: "2026-08-25T08:00:00+00:00",
    start_address: "Start",
    end_address: "Starý serverový cíl",
    distance_km: 10,
    purpose: "",
    trip_type: "business",
    editable: true,
    status: "saved",
    odometer_ready: true,
  },
]);

assert.equal(panel._tripDrafts.get("trip-1"), staleDraft);
assert.match(staleHtml, /Ruční cíl/);
assert.match(staleHtml, /Uloženo/);

panel._tripTable([
  {
    id: "trip-1",
    started_at: "2026-08-25T08:00:00+00:00",
    start_address: "Start",
    end_address: "Ruční cíl",
    distance_km: 12,
    purpose: "Klient",
    trip_type: "business",
    editable: true,
    status: "saved",
    odometer_ready: true,
  },
]);
assert.equal(panel._tripDrafts.has("trip-1"), false);

const privateTripHtml = panel._tripTable([
  {
    id: "private-1",
    started_at: "2026-08-25T09:00:00+00:00",
    start_address: "Start",
    end_address: "Soukromý cíl",
    distance_km: 4,
    purpose: "Soukromá",
    trip_type: "private",
    editable: true,
    status: "saved",
    odometer_ready: true,
  },
]);
assert.match(privateTripHtml, /class="trip-purpose"[^>]*disabled/);
assert.match(privateTripHtml, /U soukromé jízdy se neeviduje/);

const resolutionCalls = [];
panel._hass = {
  async callService(domain, service, payload) {
    resolutionCalls.push({ domain, service, payload });
  },
  async callApi(method, path) {
    assert.equal(method, "GET");
    assert.equal(path, "kniha_jizd/overview");
    return { diagnostics: { today_trips: [] }, statistics: {} };
  },
};
const questionCard = {
  dataset: { segmentId: "pending-1" },
  querySelector() {
    return { value: "" };
  },
};
await panel._resolveTrip({
  dataset: { action: "business" },
  closest(selector) {
    assert.equal(selector, ".question-card");
    return questionCard;
  },
});

assert.deepEqual(resolutionCalls, [
  {
    domain: "kniha_jizd",
    service: "resolve_trip",
    payload: { segment_id: "pending-1", action: "business" },
  },
]);
assert.equal(panel._mapData, null, "classifying a trip must invalidate cached map data");
assert.equal(panel._placesData, null, "classifying a trip must invalidate cached management data");

const placeQuestionCard = {
  dataset: { segmentId: "saved-1" },
  querySelector() {
    return { value: "Genetická laboratoř" };
  },
};
await panel._resolvePlace({
  dataset: { action: "save" },
  closest(selector) {
    assert.equal(selector, ".place-question-card");
    return placeQuestionCard;
  },
});

assert.deepEqual(resolutionCalls[1], {
  domain: "kniha_jizd",
  service: "save_trip_place",
  payload: {
    segment_id: "saved-1",
    action: "save",
    value: "Genetická laboratoř",
  },
});
assert.equal(panel._mapData, null, "saving a place must invalidate cached map data");
assert.equal(panel._placesData, null, "saving a place must invalidate cached management data");

const privatePlaceHtml = panel._placeQuestionCard({
  id: "private-place-1",
  started_at: "2026-08-25T10:00:00+00:00",
  start_address: "Start",
  end_address: "Soukromý cíl",
  place_question: {
    trip_type: "private",
    name_input_allowed: false,
    suggested_label: "Soukromý cíl",
  },
});
assert.doesNotMatch(privatePlaceHtml, /class="place-question-value"/);
assert.match(privatePlaceHtml, /jméno zákazníka ani provozovny se neeviduje/);

const privatePlaceQuestionCard = {
  dataset: { segmentId: "private-place-1", tripType: "private" },
  querySelector() {
    return null;
  },
};
await panel._resolvePlace({
  dataset: { action: "save" },
  closest(selector) {
    assert.equal(selector, ".place-question-card");
    return privatePlaceQuestionCard;
  },
});

assert.deepEqual(resolutionCalls[2], {
  domain: "kniha_jizd",
  service: "save_trip_place",
  payload: {
    segment_id: "private-place-1",
    action: "save",
  },
});

panel._placesData = {
  places: [
    {
      id: "address-only",
      label: "Adresa bez GPS",
      classification: "business",
      radius_m: 250,
      anchors: [{ address: "Náměstí 1", latitude: null, longitude: null }],
    },
  ],
  configured_places: [],
  visible_learned_point_ids: [],
};
const addressOnlyPlaceHtml = panel._placesTable();
assert.match(addressOnlyPlaceHtml, /bez GPS souřadnic/);
assert.doesNotMatch(addressOnlyPlaceHtml, /0,00000, 0,00000/);

const mapPath = new URL(
  "../custom_components/kniha_jizd/frontend/kniha-jizd-map.js",
  import.meta.url,
);
const mapSource = await readFile(mapPath, "utf8");
await import(`data:text/javascript;base64,${Buffer.from(mapSource).toString("base64")}`);
const MapElement = customElements.get("kniha-jizd-map");
const map = new MapElement();
assert.equal(map._routeClass({ trip_type: "business" }), "business");
assert.equal(map._routeClass({ trip_type: "private" }), "private");
assert.equal(map._routeClass({ trip_type: null, status: "waiting_journey" }), "pending");
map._data = {
  car: { latitude: null, longitude: null },
  configured_places: [],
  learned_places: [],
  today_routes: [
    {
      start_latitude: null,
      start_longitude: null,
      end_latitude: "",
      end_longitude: "",
    },
  ],
  short_stops: [
    {
      end_latitude: 49.3,
      end_longitude: 17.4,
      short_stop_label: "Čerpací stanice",
      short_stop_confirmed: true,
    },
  ],
};
assert.deepEqual(
  map._points(),
  [{ latitude: 49.3, longitude: 17.4 }],
  "short stops must be included in the fitted map area",
);
assert.equal(map._shortStops().length, 1, "short stops must have their own markers");
assert.match(mapSource, /Krátká zastávka/, "the map legend must explain short stops");
assert.equal(map._finiteValue(null), false, "missing accuracy must stay unknown");
