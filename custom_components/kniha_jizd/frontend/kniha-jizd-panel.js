import "./kniha-jizd-map.js?v=1.9.0";

class KnihaJizdPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._exporting = false;
    this._message = "";
    this._savingTrip = null;
    this._tableScrollLeft = 0;
    this._activeTab = "overview";
    this._mapData = null;
    this._mapLoading = false;
    this._mapError = "";
    this._mapLoadedAt = 0;
    this._mapRefreshTimer = null;
    this._month = this._currentMonth();
  }

  set hass(value) {
    this._hass = value;
    if (this._activeTab === "map" && this.shadowRoot?.querySelector("kniha-jizd-map")) {
      if (
        !this._mapLoading
        && !this._mapRefreshTimer
        && Date.now() - this._mapLoadedAt > 10000
      ) {
        this._mapRefreshTimer = setTimeout(() => {
          this._mapRefreshTimer = null;
          this._loadMapData();
        }, 750);
      }
      return;
    }
    const activeElement = this.shadowRoot?.activeElement;
    if (activeElement?.matches?.("input, select, textarea")) return;
    this._render();
  }

  set narrow(value) {
    this._narrow = value;
  }

  set panel(value) {
    this._panel = value;
  }

  connectedCallback() {
    this._render();
  }

  disconnectedCallback() {
    if (this._mapRefreshTimer) clearTimeout(this._mapRefreshTimer);
    this._mapRefreshTimer = null;
  }

  _entity(kind) {
    if (!this._hass) return null;
    return Object.values(this._hass.states).find(
      (state) => state.attributes.kniha_jizd_kind === kind,
    );
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
    if (value === undefined || value === null || value === "") return "—";
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return "—";
    return new Intl.NumberFormat("cs-CZ", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(parsed);
  }

  _currentMonth() {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  }

  _statusLabel(value) {
    return {
      idle: "Připraveno",
      driving: "Probíhá jízda",
      waiting_odometer: "Čeká se na tachometr",
      waiting_classification: "Čeká na zařazení",
      waiting_journey: "Čeká na pokračování jízdy",
      processing_destination: "Určuje se cíl",
      saved: "Uloženo",
      error: "Chyba",
    }[value] || this._text(value);
  }

  _time(value) {
    const parsed = new Date(value || "");
    if (Number.isNaN(parsed.getTime())) return "—";
    return new Intl.DateTimeFormat("cs-CZ", {
      hour: "2-digit",
      minute: "2-digit",
    }).format(parsed);
  }

  async _saveTrip(button) {
    if (!this._hass || this._savingTrip) return;
    const row = button.closest("tr");
    const segmentId = row?.dataset?.segmentId;
    const purpose = row?.querySelector(".trip-purpose")?.value?.trim() || "";
    const tripType = row?.querySelector(".trip-type")?.value || "business";
    const startAddress = row?.querySelector(".trip-start")?.value?.trim() || "";
    const endAddress = row?.querySelector(".trip-end")?.value?.trim() || "";
    const distanceValue = row?.querySelector(".trip-distance")?.value;
    const distanceKm = distanceValue === "" ? undefined : Number(distanceValue);
    if (!segmentId) return;
    this._savingTrip = segmentId;
    this._message = "Ukládám opravu jízdy…";
    this._render();
    try {
      await this._hass.callService("kniha_jizd", "update_trip", {
        segment_id: segmentId,
        purpose,
        trip_type: tripType,
        start_address: startAddress,
        end_address: endAddress,
        ...(Number.isFinite(distanceKm) ? { distance_km: distanceKm } : {}),
      });
      this._message = "Jízda byla upravena. Pokud tachometr ještě čeká, dokončí se automaticky.";
    } catch (error) {
      this._message = `Úprava se nezdařila: ${error.message || error}`;
    } finally {
      this._savingTrip = null;
      this._render();
    }
  }

  _tripTable(rows) {
    if (!Array.isArray(rows) || rows.length === 0) {
      return '<div class="muted">Dnes zatím není zaznamenána žádná jízda.</div>';
    }
    return `<div class="table-wrap"><table><thead><tr>
      <th>Čas</th><th>Odkud</th><th>Kam</th><th>km</th><th>Zákazník / účel</th><th>Typ</th><th>Stav</th><th></th>
    </tr></thead><tbody>${rows.map((trip) => {
      const privateSelected = trip.trip_type === "private";
      const disabled = !trip.editable || this._savingTrip === trip.id;
      return `<tr data-segment-id="${this._text(trip.id)}">
        <td>${this._time(trip.started_at)}</td>
        <td><input class="trip-start" type="text" value="${this._text(trip.start_address, "")}" placeholder="Místo odjezdu" ${disabled ? "disabled" : ""}></td>
        <td><input class="trip-end" type="text" value="${this._text(trip.end_address, "")}" placeholder="Místo příjezdu" ${disabled ? "disabled" : ""}></td>
        <td><input class="trip-distance" type="number" min="0" step="1" value="${trip.distance_km ?? ""}" ${disabled ? "disabled" : ""}></td>
        <td><input class="trip-purpose" type="text" value="${this._text(trip.purpose, "")}" placeholder="Volitelný zákazník / účel" ${disabled ? "disabled" : ""}></td>
        <td><select class="trip-type" ${disabled ? "disabled" : ""}>
          <option value="business" ${privateSelected ? "" : "selected"}>Služební</option>
          <option value="private" ${privateSelected ? "selected" : ""}>Soukromá</option>
        </select></td>
        <td>${this._statusLabel(trip.status)}${trip.odometer_ready ? "" : " · čeká km"}<small>${this._text(trip.distance_reconciliation_source)}</small></td>
        <td><button class="save-trip" ${disabled ? "disabled" : ""}>Uložit</button></td>
      </tr>`;
    }).join("")}</tbody></table></div>`;
  }

  _check(label, ok, detail) {
    const status = ok ? "ok" : "bad";
    return `<div class="check"><span class="dot ${status}"></span><div><strong>${label}</strong><small>${this._text(detail)}</small></div></div>`;
  }

  async _exportExcel() {
    if (!this._hass || this._exporting) return;
    const selectedMonth = this.shadowRoot.getElementById("month")?.value;
    if (selectedMonth) this._month = selectedMonth;
    this._exporting = true;
    this._message = `Generuji Excel za ${this._month}…`;
    this._render();
    try {
      await this._hass.callService(
        "kniha_jizd",
        "export_excel",
        { month: this._month },
      );
      let exportEntity = null;
      for (let attempt = 0; attempt < 20; attempt += 1) {
        exportEntity = this._entity("export");
        if (exportEntity?.attributes?.download_url) break;
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
      const url = exportEntity?.attributes?.download_url;
      if (!url) throw new Error("Odkaz ke stažení nebyl vytvořen");
      this._message = "Excel je hotový. Stahování začíná…";
      const link = document.createElement("a");
      link.href = url;
      link.download = exportEntity?.attributes?.filename || `kniha_jizd_${this._month}.xlsx`;
      document.body.appendChild(link);
      link.click();
      link.remove();
    } catch (error) {
      this._message = `Export se nezdařil: ${error.message || error}`;
    } finally {
      this._exporting = false;
      this._render();
    }
  }

  async _selectTab(tab) {
    if (!new Set(["overview", "map"]).has(tab)) return;
    this._activeTab = tab;
    this._render();
    if (tab === "map" && !this._mapData) await this._loadMapData();
  }

  _syncMapElement() {
    const map = this.shadowRoot?.querySelector("kniha-jizd-map");
    if (map && this._mapData) map.data = this._mapData;
    const status = this.shadowRoot?.querySelector(".map-loading");
    if (status) {
      status.textContent = this._mapError
        ? `Mapová data se nepodařilo načíst: ${this._mapError}`
        : this._mapLoading
          ? "Načítám aktuální polohu, místa a zóny…"
          : this._mapData
            ? `Aktualizováno ${new Date(this._mapData.generated_at || Date.now()).toLocaleString("cs-CZ")}`
            : "Mapová data ještě nejsou načtená.";
    }
    const refresh = this.shadowRoot?.getElementById("refresh-map");
    if (refresh) refresh.disabled = this._mapLoading;
  }

  async _loadMapData() {
    if (!this._hass || this._mapLoading) return;
    this._mapLoading = true;
    this._mapError = "";
    this._syncMapElement();
    try {
      this._mapData = await this._hass.callApi("GET", "kniha_jizd/map");
    } catch (error) {
      this._mapError = error.message || String(error);
    } finally {
      this._mapLoadedAt = Date.now();
      this._mapLoading = false;
      this._syncMapElement();
    }
  }

  _render() {
    if (!this.shadowRoot) return;
    const currentTableWrap = this.shadowRoot.querySelector?.(".table-wrap");
    if (currentTableWrap) this._tableScrollLeft = currentTableWrap.scrollLeft;
    if (!this._hass) {
      this.shadowRoot.innerHTML = "<p>Načítám Knihu jízd…</p>";
      return;
    }

    const status = this._entity("status");
    const ready = this._entity("ready");
    const business = this._entity("today_business_km");
    const privateKm = this._entity("today_private_km");
    const todayTrips = this._entity("today_segments");
    const pending = this._entity("pending");
    const totalTrips = this._entity("segments_total");
    const totalBusiness = this._entity("business_km_total");
    const totalPrivate = this._entity("private_km_total");
    const lastTrip = this._entity("last_trip");
    const exportEntity = this._entity("export");
    const attrs = status?.attributes || {};
    const odometerCheck = attrs.odometer_day_check || {};
    const todayTripRows = attrs.today_trips || [];
    const last = lastTrip?.attributes || {};
    const rawDownloadUrl = exportEntity?.attributes?.download_url;
    const expiresAt = Date.parse(exportEntity?.attributes?.expires_at || "");
    const downloadUrl = rawDownloadUrl && expiresAt > Date.now() ? rawDownloadUrl : null;
    const downloadFilename = exportEntity?.attributes?.filename || "kniha_jizd.xlsx";
    const gpsDetail = attrs.gps_ok
      ? `${attrs.gps_entity}: ${attrs.latitude}, ${attrs.longitude} · ${attrs.gps_coordinate_source}`
      : `${attrs.gps_entity}: stav ${attrs.gps_state ?? "—"} · souřadnice nenalezeny ani v ${attrs.address_entity}`;
    const odometerDetail = attrs.odometer_ok
      ? `${attrs.odometer_entity}: ${attrs.odometer_km} km · ${attrs.odometer_value_source}`
      : `${attrs.odometer_entity}: stav ${attrs.odometer_state ?? "—"} · číselná hodnota nenalezena`;

    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; min-height:100%; background:var(--primary-background-color); color:var(--primary-text-color); }
        * { box-sizing:border-box; }
        main { max-width:1200px; margin:0 auto; padding:24px; font-family:var(--paper-font-body1_-_font-family, sans-serif); }
        header { display:flex; justify-content:space-between; gap:16px; align-items:center; margin-bottom:24px; flex-wrap:wrap; }
        h1 { margin:0; font-size:28px; } h2 { margin:0 0 16px; font-size:18px; }
        .pill { border-radius:999px; padding:8px 14px; font-weight:600; background:var(--secondary-background-color); }
        .pill.ready { color:var(--success-color, #2e7d32); } .pill.error { color:var(--error-color, #c62828); }
        .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:16px; margin-bottom:16px; }
        .card { background:var(--card-background-color); border-radius:14px; padding:18px; box-shadow:var(--ha-card-box-shadow, 0 2px 8px rgba(0,0,0,.12)); }
        .metric { font-size:28px; font-weight:700; margin-top:8px; } .muted, small { color:var(--secondary-text-color); }
        small { display:block; margin-top:3px; overflow-wrap:anywhere; }
        .check { display:flex; gap:10px; align-items:flex-start; padding:9px 0; border-bottom:1px solid var(--divider-color); }
        .check:last-child { border-bottom:0; }
        .dot { width:11px; height:11px; border-radius:50%; margin-top:4px; flex:0 0 auto; }
        .dot.ok { background:var(--success-color,#2e7d32); } .dot.bad { background:var(--error-color,#c62828); }
        dl { display:grid; grid-template-columns:minmax(110px,160px) 1fr; gap:9px 14px; margin:0; }
        dt { color:var(--secondary-text-color); } dd { margin:0; overflow-wrap:anywhere; }
        .actions { display:flex; gap:12px; flex-wrap:wrap; margin-top:18px; }
        .month-control { display:flex; flex-direction:column; gap:6px; max-width:220px; margin-top:16px; }
        .month-control label { color:var(--secondary-text-color); }
        input[type="month"] { color:var(--primary-text-color); background:var(--card-background-color); border:1px solid var(--divider-color); border-radius:8px; padding:10px 12px; font:inherit; }
        input[type="text"], input[type="number"], select { width:100%; min-width:130px; color:var(--primary-text-color); background:var(--card-background-color); border:1px solid var(--divider-color); border-radius:7px; padding:8px; font:inherit; }
        .trip-distance { min-width:85px !important; width:95px !important; }
        button, a.button { border:0; border-radius:10px; padding:12px 18px; font:inherit; font-weight:600; cursor:pointer; text-decoration:none; }
        button { color:var(--text-primary-color,#fff); background:var(--primary-color); }
        button:disabled { opacity:.6; cursor:wait; } a.button { color:var(--primary-color); background:var(--secondary-background-color); }
        .message { margin-top:12px; color:var(--secondary-text-color); }
        .table-wrap { max-width:100%; overflow-x:auto; overflow-y:hidden; overscroll-behavior-x:contain; -webkit-overflow-scrolling:touch; }
        table { width:max-content; min-width:100%; border-collapse:collapse; margin-top:12px; }
        th, td { text-align:left; vertical-align:top; border-bottom:1px solid var(--divider-color); padding:9px 8px; min-width:75px; }
        th { color:var(--secondary-text-color); font-weight:600; } td:nth-child(2), td:nth-child(3) { min-width:180px; }
        .save-trip { padding:9px 13px; white-space:nowrap; }
        .daily-trips { min-width:0; overflow:hidden; margin-bottom:16px; }
        .tabs { display:flex; gap:6px; margin:-8px 0 22px; padding:5px; width:max-content; max-width:100%; border-radius:12px; background:var(--secondary-background-color); }
        .tab { min-width:130px; padding:10px 16px; color:var(--primary-text-color); background:transparent; }
        .tab.active { color:var(--text-primary-color,#fff); background:var(--primary-color); }
        .map-heading { display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom:12px; flex-wrap:wrap; }
        .map-heading h2 { margin:0; }
        .map-heading button { padding:9px 14px; }
        .map-loading { min-height:20px; margin-bottom:12px; color:var(--secondary-text-color); }
        @media (max-width:600px) { main { padding:16px; } dl { grid-template-columns:1fr; gap:3px; } dd { margin-bottom:8px; } }
      </style>
      <main>
        <header><div><h1>Kniha jízd</h1><div class="muted">Průběžný stav integrace a export reportů</div></div>
          <span class="pill ${ready?.state === "on" ? "ready" : "error"}">${ready?.state === "on" ? this._statusLabel(status?.state) : "Vstupy nejsou připravené"}</span>
        </header>
        <nav class="tabs" aria-label="Části panelu">
          <button class="tab ${this._activeTab === "overview" ? "active" : ""}" data-tab="overview">Přehled</button>
          <button class="tab ${this._activeTab === "map" ? "active" : ""}" data-tab="map">Mapa míst</button>
        </nav>
        <div ${this._activeTab === "overview" ? "" : "hidden"}>
        <section class="grid">
          <article class="card"><div class="muted">Dnes služební</div><div class="metric">${this._number(business?.state)} km</div></article>
          <article class="card"><div class="muted">Dnes soukromé</div><div class="metric">${this._number(privateKm?.state)} km</div></article>
          <article class="card"><div class="muted">Dnešní jízdy</div><div class="metric">${this._text(todayTrips?.state, "0")}</div></article>
          <article class="card"><div class="muted">Čekající jízdy</div><div class="metric">${this._text(pending?.state, "0")}</div></article>
          <article class="card"><div class="muted">Celkem záznamů</div><div class="metric">${this._text(totalTrips?.state, "0")}</div></article>
          <article class="card"><div class="muted">Celkem služební</div><div class="metric">${this._number(totalBusiness?.state)} km</div></article>
          <article class="card"><div class="muted">Celkem soukromé</div><div class="metric">${this._number(totalPrivate?.state)} km</div></article>
        </section>
        <section class="grid">
          <article class="card"><h2>Kontrola vstupů</h2>
            ${this._check("Android Auto", attrs.trigger_ok, `${attrs.trigger_entity}: ${attrs.trigger_state}`)}
            ${this._check("GPS telefonu", attrs.gps_ok, gpsDetail)}
            ${this._check("Geokódovaná adresa", attrs.address_ok, attrs.address_entity)}
            ${this._check("Tachometr", attrs.odometer_ok, odometerDetail)}
            ${this._check("Notifikace", attrs.notify_ok, attrs.notify_service)}
          </article>
          <article class="card"><h2>Aktuální zpracování</h2><dl>
            <dt>Stav</dt><dd>${this._statusLabel(status?.state)}</dd>
            <dt>Aktivní segment</dt><dd>${this._text(attrs.active_segment_id)}</dd>
            <dt>Čeká tachometr</dt><dd>${this._text(attrs.closing_count, "0")}</dd>
            <dt>Čeká zařazení</dt><dd>${this._text(attrs.pending_count, "0")}</dd>
            <dt>Čeká na cíl celé jízdy</dt><dd>${this._text(attrs.transient_count, "0")}</dd>
            <dt>Návaznost návratu</dt><dd>${this._text(attrs.return_context_hours)} h</dd>
            <dt>Limit mezizastávky</dt><dd>${this._text(attrs.transient_stop_minutes)} min</dd>
            <dt>Ustálení cíle</dt><dd>${this._text(attrs.location_settle_seconds)} s</dd>
            <dt>Denní kontrola km</dt><dd>${odometerCheck.consistent ? "Sedí" : "Čeká / rozdíl"} · odometer ${this._number(odometerCheck.odometer_delta_km)} km · potvrzené segmenty ${this._number(odometerCheck.assigned_segment_km)} km · čekající ${this._number(odometerCheck.pending_segment_km)} km · rozdíl ${this._number(odometerCheck.difference_km)} km</dd>
            <dt>Domov</dt><dd>${this._text(attrs.home_address)} · ${this._text(attrs.home_latitude)}, ${this._text(attrs.home_longitude)}</dd>
            <dt>Firma</dt><dd>${this._text(attrs.company_address)} · ${this._text(attrs.company_latitude)}, ${this._text(attrs.company_longitude)} → ${this._text(attrs.company_label)}</dd>
            <dt>Poslední volba z telefonu</dt><dd>${attrs.last_notification_action ? `${this._text(attrs.last_notification_action.action)} · ${this._text(attrs.last_notification_action.processed_at)}` : "—"}</dd>
            <dt>Poslední chyba</dt><dd>${this._text(attrs.last_error)}</dd>
          </dl></article>
          <article class="card"><h2>Poslední jízda</h2><dl>
            <dt>Vzdálenost</dt><dd>${this._number(lastTrip?.state)} km</dd>
            <dt>Celá cesta</dt><dd>${this._number(last.journey_distance_km)} km / ${this._text(last.journey_segment_count, "1")} segmentů</dd>
            <dt>Zákazník</dt><dd>${this._text(last.purpose)}</dd>
            <dt>Typ</dt><dd>${last.journey_role === "return" ? "Služební návrat" : last.trip_type === "private" ? "Soukromá" : this._text(last.trip_type)}</dd>
            <dt>Start</dt><dd>${this._text(last.start_address)}</dd>
            <dt>Cíl</dt><dd>${this._text(last.end_address)}</dd>
            <dt>Konec</dt><dd>${this._text(last.ended_at)}</dd>
          </dl></article>
        </section>
        <section class="card daily-trips"><h2>Dnešní jízdy</h2>
          <div class="muted">Uložené i čekající jízdy lze opravit. Zákazník je u služební jízdy volitelný. Segmenty stejné celé cesty se upraví společně.</div>
          ${this._tripTable(todayTripRows)}
        </section>
        <section class="card"><h2>Excel report</h2>
          <div class="muted">Oba listy budou obsahovat pouze jízdy z vybraného měsíce.</div>
          <div class="month-control"><label for="month">Měsíc reportu</label><input id="month" type="month" value="${this._text(this._month)}"></div>
          <div class="actions"><button id="export" ${this._exporting ? "disabled" : ""}>${this._exporting ? "Generuji…" : "Vygenerovat a stáhnout Excel"}</button>
          ${downloadUrl ? `<a class="button" href="${downloadUrl}" download="${this._text(downloadFilename)}">Stáhnout poslední export (${this._text(exportEntity?.attributes?.month)})</a>` : ""}</div>
          <div class="message">${this._text(this._message, exportEntity?.attributes?.generated_at ? `Poslední export: ${exportEntity.attributes.generated_at}, měsíc ${exportEntity.attributes.month}` : "Dosud nebyl vytvořen export.")}</div>
        </section>
        </div>
        ${this._activeTab === "map" ? `<section class="card">
          <div class="map-heading"><div><h2>Mapa uložených míst a zón</h2><div class="muted">Aktuální auto, naučené parkovací body, rozpoznávací poloměry a dnešní úseky.</div></div><button id="refresh-map" ${this._mapLoading ? "disabled" : ""}>Aktualizovat</button></div>
          <div class="map-loading"></div>
          <kniha-jizd-map></kniha-jizd-map>
        </section>` : ""}
      </main>`;
    this.shadowRoot.querySelectorAll(".tab").forEach((button) => {
      button.addEventListener("click", () => this._selectTab(button.dataset.tab));
    });
    this.shadowRoot.getElementById("refresh-map")?.addEventListener("click", () => this._loadMapData());
    this.shadowRoot.getElementById("month")?.addEventListener("change", (event) => {
      if (event.target.value) this._month = event.target.value;
    });
    this.shadowRoot.getElementById("export")?.addEventListener("click", () => this._exportExcel());
    this.shadowRoot.querySelectorAll(".save-trip").forEach((button) => {
      button.addEventListener("click", () => this._saveTrip(button));
    });
    const tableWrap = this.shadowRoot.querySelector(".table-wrap");
    if (tableWrap) {
      tableWrap.scrollLeft = Math.min(
        this._tableScrollLeft,
        Math.max(0, tableWrap.scrollWidth - tableWrap.clientWidth),
      );
      tableWrap.addEventListener("scroll", () => {
        this._tableScrollLeft = tableWrap.scrollLeft;
      }, { passive: true });
    }
    this._syncMapElement();
  }
}

if (!customElements.get("kniha-jizd-panel")) {
  customElements.define("kniha-jizd-panel", KnihaJizdPanel);
}
