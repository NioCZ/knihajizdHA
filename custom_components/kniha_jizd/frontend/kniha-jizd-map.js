class KnihaJizdMap extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._data = null;
    this._center = null;
    this._zoom = 12;
    this._selectedId = null;
    this._drag = null;
    this._drawFrame = null;
    this._resizeObserver = null;
    this.addEventListener("click", (event) => {
      const elements = event.composedPath().filter((item) => item instanceof Element);
      const deleteButton = elements.find((item) => item.matches(".delete-selected-place"));
      if (deleteButton) {
        const selected = this._markers().find((item) => String(item.id) === String(this._selectedId));
        const anchorIndex = Number(selected?.anchor_index);
        if (selected?.place_id && Number.isInteger(anchorIndex) && anchorIndex >= 0) {
          this.dispatchEvent(new CustomEvent("kniha-jizd-delete-map-place", {
            bubbles: true,
            composed: true,
            detail: {
              markerId: selected.id,
              placeId: selected.place_id,
              anchorIndex,
              label: selected.label,
              address: selected.address,
            },
          }));
        }
        return;
      }
      const marker = elements.find((item) => item.matches(".marker[data-id]"));
      if (marker) {
        this._selectedId = marker.dataset.id;
        this._renderInfo();
        this._scheduleDraw();
        return;
      }
      if (elements.some((item) => item.matches(".zoom-in"))) this._changeZoom(1);
      else if (elements.some((item) => item.matches(".zoom-out"))) this._changeZoom(-1);
      else if (elements.some((item) => item.matches(".fit"))) this._fitAll();
    });
  }

  set data(value) {
    const hadData = Boolean(this._data);
    this._data = value && typeof value === "object" ? value : null;
    const markerIds = new Set(this._markers().map((item) => String(item.id)));
    if (!this._selectedId || !markerIds.has(String(this._selectedId))) {
      this._selectedId = this._data?.car?.current_zone?.id || null;
    }
    this._renderShell();
    if (hadData && this._center) {
      this._renderInfo();
      this._scheduleDraw();
    } else {
      this._fitCurrent();
    }
  }

  connectedCallback() {
    this._renderShell();
    if (typeof ResizeObserver !== "undefined") {
      this._resizeObserver = new ResizeObserver(() => this._scheduleDraw());
      this._resizeObserver.observe(this);
    }
  }

  disconnectedCallback() {
    this._resizeObserver?.disconnect();
    if (this._drawFrame) cancelAnimationFrame(this._drawFrame);
  }

  _text(value, fallback = "—") {
    const selected = value === undefined || value === null || value === "" ? fallback : value;
    return String(selected)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  _number(value, digits = 0) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return "—";
    return new Intl.NumberFormat("cs-CZ", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(parsed);
  }

  _roleLabel(role) {
    return {
      home: "Domov",
      company: "Firma",
      client: "Klient",
      mixed: "Služební / soukromé",
      private: "Soukromé místo",
      car: "Auto",
    }[role] || "Místo";
  }

  _markers() {
    if (!this._data) return [];
    return [
      ...(Array.isArray(this._data.configured_places) ? this._data.configured_places : []),
      ...(Array.isArray(this._data.learned_places) ? this._data.learned_places : []),
    ].filter((item) => Number.isFinite(Number(item.latitude)) && Number.isFinite(Number(item.longitude)));
  }

  _points() {
    const points = this._markers().map((item) => ({
      latitude: Number(item.latitude),
      longitude: Number(item.longitude),
    }));
    const car = this._data?.car;
    if (Number.isFinite(Number(car?.latitude)) && Number.isFinite(Number(car?.longitude))) {
      points.push({ latitude: Number(car.latitude), longitude: Number(car.longitude) });
    }
    for (const route of this._data?.today_routes || []) {
      for (const side of ["start", "end"]) {
        const latitude = Number(route[`${side}_latitude`]);
        const longitude = Number(route[`${side}_longitude`]);
        if (Number.isFinite(latitude) && Number.isFinite(longitude)) {
          points.push({ latitude, longitude });
        }
      }
    }
    return points;
  }

  _project(latitude, longitude, zoom = this._zoom) {
    const size = 256 * (2 ** zoom);
    const limitedLatitude = Math.max(-85.05112878, Math.min(85.05112878, Number(latitude)));
    const sinLatitude = Math.sin((limitedLatitude * Math.PI) / 180);
    return {
      x: ((Number(longitude) + 180) / 360) * size,
      y: (0.5 - Math.log((1 + sinLatitude) / (1 - sinLatitude)) / (4 * Math.PI)) * size,
    };
  }

  _unproject(x, y, zoom = this._zoom) {
    const size = 256 * (2 ** zoom);
    const longitude = (x / size) * 360 - 180;
    const n = Math.PI - (2 * Math.PI * y) / size;
    return {
      latitude: (180 / Math.PI) * Math.atan(Math.sinh(n)),
      longitude,
    };
  }

  _fitAll() {
    this._fitPoints(this._points());
  }

  _fitCurrent() {
    const car = this._data?.car;
    const carLatitude = Number(car?.latitude);
    const carLongitude = Number(car?.longitude);
    if (!Number.isFinite(carLatitude) || !Number.isFinite(carLongitude)) {
      this._fitAll();
      return;
    }
    const nearby = this._points().filter((point) => {
      const latitudeMeters = (point.latitude - carLatitude) * 111320;
      const longitudeMeters = (point.longitude - carLongitude) * 111320 * Math.cos((carLatitude * Math.PI) / 180);
      return Math.hypot(latitudeMeters, longitudeMeters) <= 25000;
    });
    this._fitPoints(nearby.length > 1 ? nearby : [
      { latitude:carLatitude - 0.01, longitude:carLongitude - 0.01 },
      { latitude:carLatitude + 0.01, longitude:carLongitude + 0.01 },
    ]);
  }

  _fitPoints(points) {
    const canvas = this.shadowRoot?.querySelector(".map-canvas");
    if (!canvas || points.length === 0) {
      this._center = { latitude: 49.8, longitude: 15.4 };
      this._zoom = 7;
      this._scheduleDraw();
      return;
    }
    const width = Math.max(320, canvas.clientWidth || 800);
    const height = Math.max(320, canvas.clientHeight || 560);
    let selectedZoom = 18;
    let bounds = null;
    for (let zoom = 18; zoom >= 3; zoom -= 1) {
      const projected = points.map((point) => this._project(point.latitude, point.longitude, zoom));
      const candidate = {
        minX: Math.min(...projected.map((point) => point.x)),
        maxX: Math.max(...projected.map((point) => point.x)),
        minY: Math.min(...projected.map((point) => point.y)),
        maxY: Math.max(...projected.map((point) => point.y)),
      };
      selectedZoom = zoom;
      bounds = candidate;
      if (candidate.maxX - candidate.minX <= width - 110 && candidate.maxY - candidate.minY <= height - 110) break;
    }
    this._zoom = selectedZoom;
    this._center = this._unproject(
      (bounds.minX + bounds.maxX) / 2,
      (bounds.minY + bounds.maxY) / 2,
      selectedZoom,
    );
    this._scheduleDraw();
  }

  _renderShell() {
    if (!this.shadowRoot || this.shadowRoot.querySelector(".map-layout")) {
      this._renderInfo();
      this._scheduleDraw();
      return;
    }
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; width:100%; min-width:0; color:var(--primary-text-color); }
        * { box-sizing:border-box; }
        .map-layout { display:grid; min-width:0; grid-template-columns:minmax(0,1fr) 290px; gap:16px; }
        .map-canvas { position:relative; min-width:0; height:min(68vh,680px); min-height:460px; overflow:hidden; border-radius:12px; background:#d9e3e8; cursor:grab; touch-action:pan-y pinch-zoom; user-select:none; }
        .map-canvas.dragging { cursor:grabbing; }
        .tile-layer, .zone-layer, .marker-layer { position:absolute; inset:0; overflow:hidden; }
        .tile-layer img { position:absolute; width:256px; height:256px; max-width:none; user-select:none; pointer-events:none; }
        .zone-layer { pointer-events:none; }
        .route { stroke:#1769aa; stroke-width:4; stroke-linecap:round; opacity:.8; filter:drop-shadow(0 1px 1px rgba(255,255,255,.8)); }
        .route.private { stroke:#8e44ad; }
        .zone { stroke-width:2; fill-opacity:.12; stroke-opacity:.7; }
        .zone.active { stroke-width:4; fill-opacity:.2; stroke-opacity:1; }
        .zone.client { fill:#1976d2; stroke:#1976d2; } .zone.private { fill:#8e44ad; stroke:#8e44ad; }
        .zone.mixed { fill:#6d4c41; stroke:#6d4c41; }
        .zone.transient { fill:#ef6c00; stroke:#ef6c00; }
        .zone.home { fill:#2e7d32; stroke:#2e7d32; } .zone.company { fill:#00897b; stroke:#00897b; }
        .marker { position:absolute; transform:translate(-50%,-100%); border:2px solid white; border-radius:999px; width:24px; height:24px; padding:0; box-shadow:0 2px 7px rgba(0,0,0,.4); cursor:pointer; background:#1976d2; color:white; }
        .marker::after { content:""; position:absolute; left:7px; bottom:-6px; width:7px; height:7px; background:inherit; transform:rotate(45deg); border-right:2px solid white; border-bottom:2px solid white; }
        .marker.private { background:#8e44ad; } .marker.transient { background:#ef6c00; }
        .marker.mixed { background:#6d4c41; }
        .marker.home { background:#2e7d32; }
        .marker.company { background:#00897b; } .marker.selected { outline:3px solid var(--warning-color,#fbc02d); z-index:4; }
        .marker.car { width:32px; height:32px; background:#d32f2f; z-index:6; transform:translate(-50%,-50%); font-size:17px; line-height:27px; }
        .marker.car::after { display:none; }
        .marker-label { position:absolute; left:50%; bottom:27px; transform:translateX(-50%); max-width:180px; padding:3px 7px; border-radius:6px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; color:#202124; background:rgba(255,255,255,.92); box-shadow:0 1px 4px rgba(0,0,0,.25); font-size:12px; pointer-events:none; }
        .map-controls { position:absolute; top:12px; right:12px; z-index:10; display:flex; flex-direction:column; gap:6px; }
        .map-controls button { width:38px; height:38px; border:0; border-radius:9px; background:var(--card-background-color,#fff); color:var(--primary-text-color,#222); box-shadow:0 1px 5px rgba(0,0,0,.28); cursor:pointer; font:bold 20px sans-serif; }
        .map-controls .fit { font-size:15px; }
        .attribution { position:absolute; right:5px; bottom:4px; z-index:8; padding:2px 5px; color:#333; background:rgba(255,255,255,.8); font-size:10px; }
        .attribution a { color:#1565c0; }
        aside { min-width:0; }
        .info-card { padding:16px; border-radius:12px; background:var(--secondary-background-color); margin-bottom:12px; }
        .info-card h3 { margin:0 0 10px; font-size:16px; }
        .info-card dl { display:grid; grid-template-columns:95px minmax(0,1fr); gap:7px 9px; margin:0; }
        .info-card dt { color:var(--secondary-text-color); } .info-card dd { margin:0; overflow-wrap:anywhere; }
        .selection-actions { display:flex; margin-top:14px; }
        .selection-actions button { width:100%; border:0; border-radius:9px; padding:10px 12px; color:#fff; background:var(--error-color,#c62828); cursor:pointer; font:600 14px sans-serif; }
        .zone-state { font-weight:700; color:var(--success-color,#2e7d32); }
        .zone-state.outside { color:var(--warning-color,#ef6c00); }
        .legend { display:grid; grid-template-columns:1fr 1fr; gap:8px; font-size:13px; }
        .legend span { display:flex; gap:7px; align-items:center; }
        .swatch { width:11px; height:11px; border-radius:50%; background:#1976d2; }
        .swatch.private { background:#8e44ad; } .swatch.transient { background:#ef6c00; }
        .swatch.mixed { background:#6d4c41; }
        .swatch.home { background:#2e7d32; }
        .swatch.company { background:#00897b; } .swatch.car { background:#d32f2f; }
        .empty { display:grid; place-items:center; height:100%; color:#455a64; padding:24px; text-align:center; }
        @media (max-width:850px) { .map-layout { grid-template-columns:1fr; } .map-canvas { min-height:420px; height:58vh; } aside { display:grid; grid-template-columns:1fr 1fr; gap:12px; } .info-card { margin:0; } }
        @media (max-width:560px) { .map-canvas { min-height:360px; } aside { grid-template-columns:1fr; } .marker-label { display:none; } }
      </style>
      <div class="map-layout">
        <div class="map-canvas" aria-label="Mapa uložených míst">
          <div class="tile-layer"></div><svg class="zone-layer"></svg><div class="marker-layer"></div>
          <div class="map-controls"><button class="zoom-in" title="Přiblížit">+</button><button class="zoom-out" title="Oddálit">−</button><button class="fit" title="Zobrazit všechna místa">⌖</button></div>
          <div class="attribution">© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a></div>
        </div>
        <aside><div class="car-info info-card"></div><div class="selection-info info-card"></div><div class="info-card"><h3>Legenda</h3><div class="legend">
          <span><i class="swatch car"></i>Auto</span><span><i class="swatch client"></i>Klient</span>
          <span><i class="swatch private"></i>Soukromé</span>
          <span><i class="swatch mixed"></i>Služební / soukromé</span>
          <span><i class="swatch home"></i>Domov</span>
          <span><i class="swatch company"></i>Firma</span>
        </div></div></aside>
      </div>`;
    const canvas = this.shadowRoot.querySelector(".map-canvas");
    canvas.addEventListener("pointerdown", (event) => this._startDrag(event));
    canvas.addEventListener("pointermove", (event) => this._moveDrag(event));
    canvas.addEventListener("pointerup", (event) => this._endDrag(event));
    canvas.addEventListener("pointercancel", (event) => this._endDrag(event));
    canvas.addEventListener("wheel", (event) => {
      event.preventDefault();
      this._changeZoom(event.deltaY < 0 ? 1 : -1);
    }, { passive: false });
    this._renderInfo();
    this._scheduleDraw();
  }

  _renderInfo() {
    if (!this.shadowRoot?.querySelector(".car-info")) return;
    const car = this._data?.car || {};
    const zone = car.current_zone;
    const hasCar = Number.isFinite(Number(car.latitude)) && Number.isFinite(Number(car.longitude));
    this.shadowRoot.querySelector(".car-info").innerHTML = `<h3>Aktuální poloha auta</h3><dl>
      <dt>Stav</dt><dd>${car.driving ? "Právě jede" : "Zaparkováno"}</dd>
      <dt>Zóna</dt><dd class="zone-state ${zone ? "" : "outside"}">${zone ? `${this._text(zone.label)} · ${this._number(zone.distance_m)} m od bodu` : "Mimo známé zóny"}</dd>
      <dt>Poloha</dt><dd>${hasCar ? `${this._number(car.latitude, 5)}, ${this._number(car.longitude, 5)}` : "GPS není dostupná"}</dd>
      <dt>Adresa</dt><dd>${this._text(car.address)}</dd>
      <dt>Zdroj</dt><dd>${this._text(car.coordinate_source)}</dd>
      <dt>Aktualizace</dt><dd>${this._text(car.updated_at)}</dd>
    </dl>`;
    const selected = this._markers().find((item) => String(item.id) === String(this._selectedId));
    const selection = this.shadowRoot.querySelector(".selection-info");
    if (selected) {
      const anchorIndex = Number(selected.anchor_index);
      const canDelete = selected.place_id
        && !String(selected.place_id).startsWith("configured:")
        && Number.isInteger(anchorIndex)
        && anchorIndex >= 0;
      selection.innerHTML = `<h3>${this._text(selected.label)}</h3><dl>
        <dt>Typ</dt><dd>${this._roleLabel(selected.place_role)}</dd>
        <dt>Zóna</dt><dd>${this._number(selected.radius_m)} m</dd>
        <dt>Adresa</dt><dd>${this._text(selected.address)}</dd>
        <dt>Souřadnice</dt><dd>${this._number(selected.latitude, 5)}, ${this._number(selected.longitude, 5)}</dd>
        <dt>Naposledy</dt><dd>${this._text(selected.updated_at)}</dd>
      </dl>${canDelete
        ? '<div class="selection-actions"><button class="delete-selected-place">Odstranit označený bod</button></div>'
        : String(selected.place_id || "").startsWith("configured:")
          ? '<small>Domov a firma se upravují v nastavení integrace.</small>'
          : ""}`;
    } else {
      const learnedCount = this._data?.learned_places?.length || 0;
      const routeCount = this._data?.today_routes?.length || 0;
      selection.innerHTML = `<h3>Co mapa zobrazuje</h3><dl>
        <dt>Místa</dt><dd>${learnedCount} naučených parkovacích bodů</dd>
        <dt>Dnes</dt><dd>${routeCount} úseků jízdy</dd>
        <dt>Ovládání</dt><dd>Tažením mapu posunete, kolečkem nebo +/− změníte přiblížení. Kliknutím vyberete místo.</dd>
      </dl>`;
    }
  }

  _startDrag(event) {
    if (event.button !== 0 || event.target.closest?.("button, a")) return;
    const canvas = this.shadowRoot.querySelector(".map-canvas");
    const center = this._project(this._center.latitude, this._center.longitude);
    this._drag = { x: event.clientX, y: event.clientY, center };
    canvas.classList.add("dragging");
    canvas.setPointerCapture(event.pointerId);
  }

  _moveDrag(event) {
    if (!this._drag) return;
    this._center = this._unproject(
      this._drag.center.x - (event.clientX - this._drag.x),
      this._drag.center.y - (event.clientY - this._drag.y),
    );
    this._scheduleDraw();
  }

  _endDrag(event) {
    if (!this._drag) return;
    this._drag = null;
    const canvas = this.shadowRoot.querySelector(".map-canvas");
    canvas.classList.remove("dragging");
    if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
  }

  _changeZoom(delta) {
    this._zoom = Math.max(3, Math.min(19, this._zoom + delta));
    this._scheduleDraw();
  }

  _scheduleDraw() {
    if (!this.isConnected || this._drawFrame) return;
    this._drawFrame = requestAnimationFrame(() => {
      this._drawFrame = null;
      this._drawMap();
    });
  }

  _screenPoint(latitude, longitude, width, height) {
    const point = this._project(latitude, longitude);
    const center = this._project(this._center.latitude, this._center.longitude);
    return { x: point.x - center.x + width / 2, y: point.y - center.y + height / 2 };
  }

  _drawMap() {
    const canvas = this.shadowRoot?.querySelector(".map-canvas");
    if (!canvas || !this._center) return;
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    if (!width || !height) return;
    const tileLayer = this.shadowRoot.querySelector(".tile-layer");
    const center = this._project(this._center.latitude, this._center.longitude);
    const minTileX = Math.floor((center.x - width / 2) / 256);
    const maxTileX = Math.floor((center.x + width / 2) / 256);
    const minTileY = Math.floor((center.y - height / 2) / 256);
    const maxTileY = Math.floor((center.y + height / 2) / 256);
    const tileCount = 2 ** this._zoom;
    const tiles = [];
    for (let x = minTileX; x <= maxTileX; x += 1) {
      for (let y = minTileY; y <= maxTileY; y += 1) {
        if (y < 0 || y >= tileCount) continue;
        const wrappedX = ((x % tileCount) + tileCount) % tileCount;
        tiles.push(`<img draggable="false" alt="" src="https://tile.openstreetmap.org/${this._zoom}/${wrappedX}/${y}.png" style="left:${Math.round(x * 256 - center.x + width / 2)}px;top:${Math.round(y * 256 - center.y + height / 2)}px">`);
      }
    }
    tileLayer.innerHTML = tiles.join("");

    const markers = this._markers();
    const activeZoneId = this._data?.car?.current_zone?.id;
    const zones = markers.map((marker) => {
      const point = this._screenPoint(marker.latitude, marker.longitude, width, height);
      const metersPerPixel = Math.cos((Number(marker.latitude) * Math.PI) / 180) * 2 * Math.PI * 6378137 / (256 * (2 ** this._zoom));
      const radius = Math.max(2, Number(marker.radius_m || 0) / metersPerPixel);
      const role = this._text(marker.place_role || "client", "client");
      return `<circle class="zone ${role} ${String(marker.id) === String(activeZoneId) ? "active" : ""}" cx="${point.x.toFixed(1)}" cy="${point.y.toFixed(1)}" r="${radius.toFixed(1)}"></circle>`;
    });
    const routes = [];
    for (const route of this._data?.today_routes || []) {
      const values = [route.start_latitude, route.start_longitude, route.end_latitude, route.end_longitude].map(Number);
      if (!values.every(Number.isFinite)) continue;
      const start = this._screenPoint(values[0], values[1], width, height);
      const end = this._screenPoint(values[2], values[3], width, height);
      routes.push(`<line class="route ${route.trip_type === "private" ? "private" : ""}" x1="${start.x.toFixed(1)}" y1="${start.y.toFixed(1)}" x2="${end.x.toFixed(1)}" y2="${end.y.toFixed(1)}"><title>${this._text(route.purpose || route.end_address || "Dnešní jízda")}</title></line>`);
    }
    const svg = this.shadowRoot.querySelector(".zone-layer");
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.innerHTML = `${zones.join("")}${routes.join("")}`;

    const markerHtml = markers.map((marker) => {
      const point = this._screenPoint(marker.latitude, marker.longitude, width, height);
      if (point.x < -100 || point.x > width + 100 || point.y < -100 || point.y > height + 100) return "";
      const id = this._text(marker.id);
      const role = this._text(marker.place_role || "client", "client");
      const selected = String(marker.id) === String(this._selectedId);
      return `<button class="marker ${role} ${selected ? "selected" : ""}" data-id="${id}" style="left:${point.x.toFixed(1)}px;top:${point.y.toFixed(1)}px" title="${this._text(marker.label)}"><span class="marker-label">${this._text(marker.label)}</span></button>`;
    });
    const car = this._data?.car;
    if (Number.isFinite(Number(car?.latitude)) && Number.isFinite(Number(car?.longitude))) {
      const point = this._screenPoint(car.latitude, car.longitude, width, height);
      markerHtml.push(`<button class="marker car" style="left:${point.x.toFixed(1)}px;top:${point.y.toFixed(1)}px" title="Aktuální poloha auta">🚗</button>`);
    }
    const markerLayer = this.shadowRoot.querySelector(".marker-layer");
    markerLayer.innerHTML = markerHtml.join("");
  }
}

if (!customElements.get("kniha-jizd-map")) {
  customElements.define("kniha-jizd-map", KnihaJizdMap);
}
