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
