class KnihaJizdPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._exporting = false;
    this._message = "";
  }

  set hass(value) {
    this._hass = value;
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

  _number(value, digits = 1) {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return "—";
    return new Intl.NumberFormat("cs-CZ", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    }).format(parsed);
  }

  _statusLabel(value) {
    return {
      idle: "Připraveno",
      driving: "Probíhá jízda",
      waiting_odometer: "Čeká se na tachometr",
      waiting_classification: "Čeká na zařazení",
      error: "Chyba",
    }[value] || this._text(value);
  }

  _check(label, ok, detail) {
    const status = ok ? "ok" : "bad";
    return `<div class="check"><span class="dot ${status}"></span><div><strong>${label}</strong><small>${this._text(detail)}</small></div></div>`;
  }

  async _exportExcel() {
    if (!this._hass || this._exporting) return;
    this._exporting = true;
    this._message = "Generuji Excel…";
    this._render();
    try {
      await this._hass.callService("kniha_jizd", "export_excel", {});
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
      link.download = "kniha_jizd.xlsx";
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

  _render() {
    if (!this.shadowRoot) return;
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
    const last = lastTrip?.attributes || {};
    const rawDownloadUrl = exportEntity?.attributes?.download_url;
    const expiresAt = Date.parse(exportEntity?.attributes?.expires_at || "");
    const downloadUrl = rawDownloadUrl && expiresAt > Date.now() ? rawDownloadUrl : null;

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
        button, a.button { border:0; border-radius:10px; padding:12px 18px; font:inherit; font-weight:600; cursor:pointer; text-decoration:none; }
        button { color:var(--text-primary-color,#fff); background:var(--primary-color); }
        button:disabled { opacity:.6; cursor:wait; } a.button { color:var(--primary-color); background:var(--secondary-background-color); }
        .message { margin-top:12px; color:var(--secondary-text-color); }
        @media (max-width:600px) { main { padding:16px; } dl { grid-template-columns:1fr; gap:3px; } dd { margin-bottom:8px; } }
      </style>
      <main>
        <header><div><h1>Kniha jízd</h1><div class="muted">Průběžný stav integrace a export reportů</div></div>
          <span class="pill ${ready?.state === "on" ? "ready" : "error"}">${ready?.state === "on" ? this._statusLabel(status?.state) : "Vstupy nejsou připravené"}</span>
        </header>
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
            ${this._check("GPS telefonu", attrs.gps_ok, attrs.gps_entity)}
            ${this._check("Geokódovaná adresa", attrs.address_ok, attrs.address_entity)}
            ${this._check("Tachometr", attrs.odometer_ok, `${attrs.odometer_entity}: ${this._text(attrs.odometer_km)} km`)}
            ${this._check("Notifikace", attrs.notify_ok, attrs.notify_service)}
          </article>
          <article class="card"><h2>Aktuální zpracování</h2><dl>
            <dt>Stav</dt><dd>${this._statusLabel(status?.state)}</dd>
            <dt>Aktivní segment</dt><dd>${this._text(attrs.active_segment_id)}</dd>
            <dt>Čeká tachometr</dt><dd>${this._text(attrs.closing_count, "0")}</dd>
            <dt>Čeká zařazení</dt><dd>${this._text(attrs.pending_count, "0")}</dd>
            <dt>Poslední chyba</dt><dd>${this._text(attrs.last_error)}</dd>
          </dl></article>
          <article class="card"><h2>Poslední jízda</h2><dl>
            <dt>Vzdálenost</dt><dd>${this._number(lastTrip?.state)} km</dd>
            <dt>Zákazník</dt><dd>${this._text(last.purpose)}</dd>
            <dt>Typ</dt><dd>${last.trip_type === "private" ? "Soukromá" : this._text(last.trip_type)}</dd>
            <dt>Start</dt><dd>${this._text(last.start_address)}</dd>
            <dt>Cíl</dt><dd>${this._text(last.end_address)}</dd>
            <dt>Konec</dt><dd>${this._text(last.ended_at)}</dd>
          </dl></article>
        </section>
        <section class="card"><h2>Excel report</h2>
          <div class="muted">Vygeneruje souhrnnou Knihu jízd a úplný list Raw data.</div>
          <div class="actions"><button id="export" ${this._exporting ? "disabled" : ""}>${this._exporting ? "Generuji…" : "Vygenerovat a stáhnout Excel"}</button>
          ${downloadUrl ? `<a class="button" href="${downloadUrl}" download="kniha_jizd.xlsx">Stáhnout poslední export</a>` : ""}</div>
          <div class="message">${this._text(this._message, exportEntity?.attributes?.generated_at ? `Poslední export: ${exportEntity.attributes.generated_at}` : "Dosud nebyl vytvořen export.")}</div>
        </section>
      </main>`;
    this.shadowRoot.getElementById("export")?.addEventListener("click", () => this._exportExcel());
  }
}

if (!customElements.get("kniha-jizd-panel")) {
  customElements.define("kniha-jizd-panel", KnihaJizdPanel);
}
