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
