import "./kniha-jizd-map.js?v=1.11.0";

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
    this._historyMonth = this._currentMonth();
    this._historyDate = this._currentDate();
    this._historyData = null;
    this._historyLoading = false;
    this._historyError = "";
    this._placesData = null;
    this._placesLoading = false;
    this._placesError = "";
    this._placesMessage = "";
    this._savingPlace = null;
    this._selectedPlaces = new Set();
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

  _currentDate() {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
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
    if (tripType === "unclassified") {
      this._message = "Před uložením vyberte služební nebo soukromý typ jízdy.";
      this._render();
      return;
    }
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
      if (this._activeTab === "history") {
        await this._loadHistoryData();
      } else {
        this._render();
      }
    }
  }

  _tripTable(rows, emptyMessage = "Pro vybraný den není zaznamenána žádná jízda.") {
    if (!Array.isArray(rows) || rows.length === 0) {
      return `<div class="muted">${this._text(emptyMessage)}</div>`;
    }
    return `<div class="table-wrap"><table><thead><tr>
      <th>Čas</th><th>Odkud</th><th>Kam</th><th>km</th><th>Zákazník / účel</th><th>Typ</th><th>Rozhodnutí</th><th>Stav</th><th></th>
    </tr></thead><tbody>${rows.map((trip) => {
      const privateSelected = trip.trip_type === "private";
      const reviewSelected = trip.trip_type === "unclassified";
      const disabled = !trip.editable || this._savingTrip === trip.id;
      return `<tr data-segment-id="${this._text(trip.id)}">
        <td>${this._time(trip.started_at)}</td>
        <td><input class="trip-start" type="text" value="${this._text(trip.start_address, "")}" placeholder="Místo odjezdu" ${disabled ? "disabled" : ""}></td>
        <td><input class="trip-end" type="text" value="${this._text(trip.end_address, "")}" placeholder="Místo příjezdu" ${disabled ? "disabled" : ""}></td>
        <td><input class="trip-distance" type="number" min="0" step="1" value="${trip.distance_km ?? ""}" ${disabled ? "disabled" : ""}></td>
        <td><input class="trip-purpose" type="text" value="${this._text(trip.purpose, "")}" placeholder="Volitelný zákazník / účel" ${disabled ? "disabled" : ""}></td>
        <td><select class="trip-type" ${disabled ? "disabled" : ""}>
          ${reviewSelected ? '<option value="unclassified" selected disabled>Nevyřešená – vyberte typ</option>' : ""}
          <option value="business" ${privateSelected || reviewSelected ? "" : "selected"}>Služební</option>
          <option value="private" ${privateSelected ? "selected" : ""}>Soukromá</option>
        </select></td>
        <td>${this._decisionDetails(trip)}</td>
        <td>${trip.needs_review ? '<strong class="review-label">K revizi</strong> · ' : ""}${this._statusLabel(trip.status)}${trip.odometer_ready ? "" : " · čeká km"}<small>${this._text(trip.distance_reconciliation_source)}</small></td>
        <td><button class="save-trip" ${disabled ? "disabled" : ""}>Uložit</button></td>
      </tr>`;
    }).join("")}</tbody></table></div>`;
  }

  _decisionDetails(trip) {
    const decision = trip?.decision || {};
    const distance = decision.distance_m === undefined || decision.distance_m === null
      ? "—"
      : `${this._number(decision.distance_m)} m`;
    const radius = decision.radius_m === undefined || decision.radius_m === null
      ? "—"
      : `${this._number(decision.radius_m)} m`;
    const searchStatus = {
      ok: "nalezeny návrhy",
      empty: "bez výsledku",
      cached: "výsledek z cache",
      empty_cached: "cache bez výsledku",
      stale_cache: "starší výsledek z cache",
      error: "chyba služby",
      skipped: "bez souřadnic",
    }[decision.candidate_search_status] || decision.candidate_search_status;
    return `<details class="decision"><summary>${this._text(decision.source_label, "Proč takto?")}</summary>
      <div>${this._text(decision.explanation)}</div>
      <small>Zdroj: ${this._text(decision.source)} · jistota: ${this._text(decision.confidence)}</small>
      ${decision.matched_place_id || decision.distance_m !== null && decision.distance_m !== undefined
        ? `<small>Místo: ${this._text(decision.matched_place_label)} · vzdálenost ${distance} / poloměr ${radius} · ${this._text(decision.match_method)}</small>`
        : ""}
      ${decision.return_gap_minutes !== null && decision.return_gap_minutes !== undefined
        ? `<small>Návaznost: ${this._number(decision.return_gap_minutes, 1)} min · ${this._text(decision.return_reason)}</small>`
        : ""}
      ${searchStatus
        ? `<small>Hledání institucí: ${this._text(searchStatus)} · pokusy ${this._text(decision.candidate_search_attempts, "0")}${decision.candidate_search_cache_hit ? " · cache" : ""}${decision.candidate_search_error ? ` · ${this._text(decision.candidate_search_error)}` : ""}</small>`
        : ""}
    </details>`;
  }

  _historyDateLabel(value) {
    const parts = String(value || "").split("-").map(Number);
    if (parts.length !== 3 || parts.some((part) => !Number.isFinite(part))) return "—";
    return new Intl.DateTimeFormat("cs-CZ", {
      weekday: "long",
      day: "numeric",
      month: "long",
      year: "numeric",
    }).format(new Date(parts[0], parts[1] - 1, parts[2], 12));
  }

  _historyMonthLabel(value) {
    const parts = String(value || "").split("-").map(Number);
    if (parts.length !== 2 || parts.some((part) => !Number.isFinite(part))) return "—";
    return new Intl.DateTimeFormat("cs-CZ", {
      month: "long",
      year: "numeric",
    }).format(new Date(parts[0], parts[1] - 1, 1, 12));
  }

  _historyCalendar() {
    const [year, month] = this._historyMonth.split("-").map(Number);
    if (!Number.isInteger(year) || !Number.isInteger(month)) return "";
    const summaries = new Map(
      (this._historyData?.days || []).map((day) => [day.date, day]),
    );
    const firstWeekday = (new Date(year, month - 1, 1, 12).getDay() + 6) % 7;
    const daysInMonth = new Date(year, month, 0, 12).getDate();
    const cells = Array.from(
      { length: firstWeekday },
      () => '<div class="calendar-empty" aria-hidden="true"></div>',
    );
    for (let dayNumber = 1; dayNumber <= daysInMonth; dayNumber += 1) {
      const dateValue = `${this._historyMonth}-${String(dayNumber).padStart(2, "0")}`;
      const summary = summaries.get(dateValue) || {};
      const businessKm = Number(summary.business_km || 0);
      const privateKm = Number(summary.private_km || 0);
      const businessTrips = Number(summary.business_trips || 0);
      const privateTrips = Number(summary.private_trips || 0);
      const reviewTrips = Number(summary.review_trips || 0);
      const classes = [
        "calendar-day",
        dateValue === this._historyDate ? "selected" : "",
        dateValue === this._currentDate() ? "today" : "",
        summary.trips ? "has-trips" : "",
      ].filter(Boolean).join(" ");
      cells.push(`<button class="${classes}" data-history-date="${dateValue}" aria-label="${this._text(this._historyDateLabel(dateValue))}">
        <span class="day-number">${dayNumber}</span>
        <span class="day-values">
          ${businessTrips ? `<span class="calendar-value business" title="Služební">${this._number(businessKm)} km</span>` : ""}
          ${privateTrips ? `<span class="calendar-value private" title="Soukromé">${this._number(privateKm)} km</span>` : ""}
          ${reviewTrips ? `<span class="calendar-value review" title="Nevyřešené">${this._number(reviewTrips)} k revizi</span>` : ""}
        </span>
      </button>`);
    }
    return `<div class="calendar-weekdays" aria-hidden="true">
      ${["Po", "Út", "St", "Čt", "Pá", "So", "Ne"].map((day) => `<span>${day}</span>`).join("")}
    </div><div class="calendar-grid">${cells.join("")}</div>`;
  }

  async _loadHistoryData() {
    if (!this._hass || this._historyLoading) return;
    this._historyLoading = true;
    this._historyError = "";
    this._render();
    try {
      const query = `month=${encodeURIComponent(this._historyMonth)}&date=${encodeURIComponent(this._historyDate)}`;
      this._historyData = await this._hass.callApi(
        "GET",
        `kniha_jizd/history?${query}`,
      );
    } catch (error) {
      this._historyError = error.message || String(error);
    } finally {
      this._historyLoading = false;
      this._render();
    }
  }

  async _changeHistoryMonth(value, preferLatest = true) {
    if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(value)) return;
    this._historyMonth = value;
    this._historyDate = value === this._currentMonth()
      ? this._currentDate()
      : `${value}-01`;
    this._historyData = null;
    await this._loadHistoryData();
    const days = this._historyData?.days || [];
    if (preferLatest && value !== this._currentMonth() && days.length) {
      const latestDate = days[days.length - 1]?.date;
      if (latestDate && latestDate !== this._historyDate) {
        this._historyDate = latestDate;
        await this._loadHistoryData();
      }
    }
  }

  async _shiftHistoryMonth(offset) {
    const [year, month] = this._historyMonth.split("-").map(Number);
    const shifted = new Date(year, month - 1 + offset, 1, 12);
    const value = `${shifted.getFullYear()}-${String(shifted.getMonth() + 1).padStart(2, "0")}`;
    await this._changeHistoryMonth(value);
  }

  async _selectHistoryDate(value) {
    if (!String(value).startsWith(`${this._historyMonth}-`)) return;
    this._historyDate = value;
    await this._loadHistoryData();
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
    if (!new Set(["overview", "history", "map", "places"]).has(tab)) return;
    this._activeTab = tab;
    this._render();
    if (tab === "map" && !this._mapData) await this._loadMapData();
    if (tab === "history" && !this._historyData) await this._loadHistoryData();
    if (tab === "places" && !this._placesData) await this._loadPlacesData();
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

  async _loadPlacesData() {
    if (!this._hass || this._placesLoading) return;
    this._placesLoading = true;
    this._placesError = "";
    this._render();
    try {
      this._placesData = await this._hass.callApi("GET", "kniha_jizd/places");
      const validIds = new Set((this._placesData?.places || []).map((place) => String(place.id)));
      this._selectedPlaces = new Set(
        [...this._selectedPlaces].filter((placeId) => validIds.has(placeId)),
      );
    } catch (error) {
      this._placesError = error.message || String(error);
    } finally {
      this._placesLoading = false;
      this._render();
    }
  }

  async _placeAction(payload, workingId) {
    if (!this._hass || this._savingPlace) return;
    this._savingPlace = workingId;
    this._placesError = "";
    this._placesMessage = "Ukládám změnu místa…";
    this._render();
    try {
      const result = await this._hass.callApi("POST", "kniha_jizd/places", payload);
      this._placesData = result.data || this._placesData;
      this._placesMessage = "Změna místa byla uložena.";
      if (payload.action === "delete" || payload.action === "merge") {
        this._selectedPlaces = new Set();
      }
      this._mapData = null;
    } catch (error) {
      this._placesError = error.message || String(error);
    } finally {
      this._savingPlace = null;
      this._render();
    }
  }

  _savePlace(button) {
    const row = button.closest("tr");
    const placeId = row?.dataset?.placeId;
    if (!placeId) return;
    this._placeAction({
      action: "update",
      place_id: placeId,
      label: row.querySelector(".place-label")?.value?.trim() || "",
      classification: row.querySelector(".place-classification")?.value || "business",
      radius_m: Number(row.querySelector(".place-radius")?.value),
    }, placeId);
  }

  _deletePlace(button) {
    const row = button.closest("tr");
    const placeId = row?.dataset?.placeId;
    const label = row?.querySelector(".place-label")?.value || "toto místo";
    if (!placeId || !window.confirm(`Opravdu odstranit ${label}? Historické jízdy zůstanou zachované.`)) return;
    this._placeAction({ action: "delete", place_id: placeId }, placeId);
  }

  _togglePlaceSelection(input) {
    const selected = new Set(this._selectedPlaces);
    if (input.checked) selected.add(String(input.value));
    else selected.delete(String(input.value));
    this._selectedPlaces = selected;
    const mergeButton = this.shadowRoot?.getElementById("merge-places");
    if (mergeButton) mergeButton.disabled = selected.size < 2 || Boolean(this._savingPlace);
    const count = this.shadowRoot?.querySelector(".selected-place-count");
    if (count) count.textContent = `${selected.size} vybráno`;
  }

  _mergeSelectedPlaces() {
    const placeIds = [...this._selectedPlaces];
    if (placeIds.length < 2) return;
    if (!window.confirm(`Sloučit ${placeIds.length} vybraná místa do jednoho záznamu?`)) return;
    this._placeAction({ action: "merge", place_ids: placeIds }, "merge");
  }

  _placesTable() {
    const places = this._placesData?.places || [];
    if (!places.length) return '<div class="muted">Zatím nejsou uložena žádná naučená místa.</div>';
    return `<div class="table-wrap places-table"><table><thead><tr>
      <th></th><th>Název</th><th>Typ</th><th>Poloměr</th><th>Kotvy</th><th>Poslední známé místo</th><th></th>
    </tr></thead><tbody>${places.map((place) => {
      const anchor = place.anchors?.[place.anchors.length - 1] || {};
      const disabled = Boolean(this._savingPlace);
      return `<tr data-place-id="${this._text(place.id)}">
        <td><input class="place-select" type="checkbox" value="${this._text(place.id)}" ${this._selectedPlaces.has(String(place.id)) ? "checked" : ""} ${disabled ? "disabled" : ""}></td>
        <td><input class="place-label" type="text" value="${this._text(place.label, "")}" ${disabled ? "disabled" : ""}></td>
        <td><select class="place-classification" ${disabled ? "disabled" : ""}>
          <option value="business" ${place.classification === "business" ? "selected" : ""}>Služební</option>
          <option value="private" ${place.classification === "private" ? "selected" : ""}>Soukromé</option>
          <option value="mixed" ${place.classification === "mixed" ? "selected" : ""}>Služební i soukromé</option>
          <option value="transient" ${place.classification === "transient" ? "selected" : ""}>Krátká zastávka</option>
        </select></td>
        <td><input class="place-radius" type="number" min="25" max="5000" step="25" value="${this._text(place.radius_m, "")}" ${disabled ? "disabled" : ""}> m</td>
        <td>${this._text(place.anchor_count, "0")}</td>
        <td>${this._text(anchor.address)}<small>${this._number(anchor.latitude, 5)}, ${this._number(anchor.longitude, 5)}</small></td>
        <td><div class="place-actions"><button class="save-place" ${disabled ? "disabled" : ""}>Uložit</button><button class="delete-place danger" ${disabled ? "disabled" : ""}>Odstranit</button></div></td>
      </tr>`;
    }).join("")}</tbody></table></div>`;
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
    const historyRows = this._historyData?.rows || [];
    const selectedDay = (this._historyData?.days || []).find(
      (day) => day.date === this._historyDate,
    ) || {};
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
        .table-wrap { position:relative; display:block; width:100%; max-width:100%; min-width:0; overflow-x:scroll; overflow-y:visible; overscroll-behavior-x:contain; -webkit-overflow-scrolling:touch; touch-action:pan-x pan-y; scrollbar-gutter:stable; padding-bottom:8px; }
        table { width:max-content; min-width:1180px; border-collapse:collapse; margin-top:12px; }
        th, td { text-align:left; vertical-align:top; border-bottom:1px solid var(--divider-color); padding:9px 8px; min-width:75px; }
        th { color:var(--secondary-text-color); font-weight:600; } td:nth-child(2), td:nth-child(3) { min-width:180px; }
        .save-trip { padding:9px 13px; white-space:nowrap; }
        .decision { min-width:220px; max-width:330px; }
        .decision summary { cursor:pointer; color:var(--primary-color); font-weight:600; }
        .decision div { margin-top:6px; }
        .review-label { color:var(--warning-color,#ef6c00); }
        .daily-trips { min-width:0; overflow:hidden; margin-bottom:16px; }
        .tabs { display:flex; gap:6px; margin:-8px 0 22px; padding:5px; width:max-content; max-width:100%; overflow-x:auto; border-radius:12px; background:var(--secondary-background-color); }
        .tab { min-width:130px; padding:10px 16px; color:var(--primary-text-color); background:transparent; }
        .tab.active { color:var(--text-primary-color,#fff); background:var(--primary-color); }
        .map-heading { display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom:12px; flex-wrap:wrap; }
        .map-heading h2 { margin:0; }
        .map-heading button { padding:9px 14px; }
        .map-loading { min-height:20px; margin-bottom:12px; color:var(--secondary-text-color); }
        .history-heading { display:flex; justify-content:space-between; align-items:center; gap:14px; margin-bottom:16px; flex-wrap:wrap; }
        .history-heading h2 { margin:0; text-transform:capitalize; }
        .history-month-nav { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
        .history-month-nav button { padding:9px 13px; }
        .history-month-nav input { margin:0; }
        .calendar-legend { display:flex; gap:14px; flex-wrap:wrap; margin:0 0 12px; color:var(--secondary-text-color); }
        .calendar-legend span { display:flex; align-items:center; gap:6px; }
        .legend-dot { width:10px; height:10px; border-radius:999px; background:#1976d2; }
        .legend-dot.private { background:#8e44ad; }
        .calendar-weekdays, .calendar-grid { display:grid; grid-template-columns:repeat(7,minmax(0,1fr)); gap:6px; }
        .calendar-weekdays { margin-bottom:6px; color:var(--secondary-text-color); text-align:center; font-size:12px; font-weight:700; }
        .calendar-day { min-width:0; min-height:96px; display:flex; flex-direction:column; align-items:stretch; gap:6px; padding:8px; color:var(--primary-text-color); background:var(--secondary-background-color); border:1px solid transparent; border-radius:10px; text-align:left; }
        .calendar-day:hover { border-color:var(--primary-color); }
        .calendar-day.selected { outline:3px solid var(--primary-color); outline-offset:0; }
        .calendar-day.today .day-number { color:var(--primary-color); }
        .calendar-day.has-trips { border-color:var(--divider-color); }
        .day-number { font-size:16px; font-weight:800; }
        .day-values { display:flex; flex-direction:column; gap:4px; margin-top:auto; min-width:0; }
        .calendar-value { display:block; overflow:hidden; padding:3px 5px; border-radius:6px; color:#fff; background:#1976d2; font-size:11px; line-height:1.25; text-overflow:ellipsis; white-space:nowrap; }
        .calendar-value.private { background:#8e44ad; }
        .calendar-value.review { background:#ef6c00; }
        .history-status { min-height:20px; margin:0 0 12px; color:var(--secondary-text-color); }
        .places-heading { display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap; margin-bottom:12px; }
        .places-heading h2 { margin:0; }
        .place-toolbar { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
        .places-table table { min-width:1050px; }
        .place-actions { display:flex; gap:6px; }
        .place-actions button { padding:8px 10px; white-space:nowrap; }
        button.danger { background:var(--error-color,#c62828); }
        .radius-summary { display:flex; gap:10px 16px; flex-wrap:wrap; margin:8px 0 16px; color:var(--secondary-text-color); }
        @media (max-width:600px) {
          main { padding:16px; } dl { grid-template-columns:1fr; gap:3px; } dd { margin-bottom:8px; }
          .calendar-weekdays, .calendar-grid { gap:3px; }
          .calendar-day { min-height:75px; padding:5px; border-radius:7px; }
          .calendar-value { padding:2px 3px; font-size:9px; }
          .table-wrap { margin:0 -4px; width:calc(100% + 8px); }
        }
      </style>
      <main>
        <header><div><h1>Kniha jízd</h1><div class="muted">Průběžný stav integrace a export reportů</div></div>
          <span class="pill ${ready?.state === "on" ? "ready" : "error"}">${ready?.state === "on" ? this._statusLabel(status?.state) : "Vstupy nejsou připravené"}</span>
        </header>
        <nav class="tabs" aria-label="Části panelu">
          <button class="tab ${this._activeTab === "overview" ? "active" : ""}" data-tab="overview">Přehled</button>
          <button class="tab ${this._activeTab === "history" ? "active" : ""}" data-tab="history">Historie</button>
          <button class="tab ${this._activeTab === "map" ? "active" : ""}" data-tab="map">Mapa míst</button>
          <button class="tab ${this._activeTab === "places" ? "active" : ""}" data-tab="places">Správa míst</button>
        </nav>
        <div ${this._activeTab === "overview" ? "" : "hidden"}>
        <section class="grid">
          <article class="card"><div class="muted">Dnes služební</div><div class="metric">${this._number(business?.state)} km</div></article>
          <article class="card"><div class="muted">Dnes soukromé</div><div class="metric">${this._number(privateKm?.state)} km</div></article>
          <article class="card"><div class="muted">Dnešní jízdy</div><div class="metric">${this._text(todayTrips?.state, "0")}</div></article>
          <article class="card"><div class="muted">Čekající jízdy</div><div class="metric">${this._text(pending?.state, "0")}</div></article>
          <article class="card"><div class="muted">Jízdy k revizi</div><div class="metric">${this._text(attrs.review_count, "0")}</div><small>Dnes ${this._text(attrs.today_review_count, "0")}</small></article>
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
            <dt>Automatická revize</dt><dd>${this._text(attrs.pending_review_hours)} h</dd>
            <dt>Ustálení cíle</dt><dd>${this._text(attrs.location_settle_seconds)} s</dd>
            <dt>Denní kontrola km</dt><dd>${odometerCheck.consistent ? "Sedí" : "Čeká / rozdíl"} · odometer ${this._number(odometerCheck.odometer_delta_km)} km · potvrzené segmenty ${this._number(odometerCheck.assigned_segment_km)} km · čekající ${this._number(odometerCheck.pending_segment_km)} km · rozdíl ${this._number(odometerCheck.difference_km)} km</dd>
            <dt>Domov</dt><dd>${this._text(attrs.home_address)} · ${this._text(attrs.home_latitude)}, ${this._text(attrs.home_longitude)}</dd>
            <dt>Firma</dt><dd>${this._text(attrs.company_address)} · ${this._text(attrs.company_latitude)}, ${this._text(attrs.company_longitude)} → ${this._text(attrs.company_label)}</dd>
            <dt>Poloměry</dt><dd>domov ${this._number(attrs.home_radius_m)} m · firma ${this._number(attrs.company_radius_m)} m · klient ${this._number(attrs.client_radius_m)} m · soukromé ${this._number(attrs.private_radius_m)} m · zastávka ${this._number(attrs.transient_radius_m)} m</dd>
            <dt>Poslední volba z telefonu</dt><dd>${attrs.last_notification_action ? `${this._text(attrs.last_notification_action.action)} · ${this._text(attrs.last_notification_action.processed_at)}` : "—"}</dd>
            <dt>Poslední chyba</dt><dd>${this._text(attrs.last_error)}</dd>
          </dl></article>
          <article class="card"><h2>Poslední jízda</h2><dl>
            <dt>Vzdálenost</dt><dd>${this._number(lastTrip?.state)} km</dd>
            <dt>Celá cesta</dt><dd>${this._number(last.journey_distance_km)} km / ${this._text(last.journey_segment_count, "1")} segmentů</dd>
            <dt>Zákazník</dt><dd>${this._text(last.purpose)}</dd>
            <dt>Typ</dt><dd>${last.journey_role === "return" ? "Služební návrat" : last.trip_type === "private" ? "Soukromá" : last.trip_type === "unclassified" ? "Nevyřešená – k revizi" : this._text(last.trip_type)}</dd>
            <dt>Start</dt><dd>${this._text(last.start_address)}</dd>
            <dt>Cíl</dt><dd>${this._text(last.end_address)}</dd>
            <dt>Konec</dt><dd>${this._text(last.ended_at)}</dd>
          </dl></article>
        </section>
        <section class="card daily-trips"><h2>Dnešní jízdy</h2>
          <div class="muted">Uložené i čekající jízdy lze opravit. Zákazník je u služební jízdy volitelný. Segmenty stejné celé cesty se upraví společně.</div>
          ${this._tripTable(todayTripRows, "Dnes zatím není zaznamenána žádná jízda.")}
        </section>
        <section class="card"><h2>Excel report</h2>
          <div class="muted">Oba listy budou obsahovat pouze jízdy z vybraného měsíce.</div>
          <div class="month-control"><label for="month">Měsíc reportu</label><input id="month" type="month" value="${this._text(this._month)}"></div>
          <div class="actions"><button id="export" ${this._exporting ? "disabled" : ""}>${this._exporting ? "Generuji…" : "Vygenerovat a stáhnout Excel"}</button>
          ${downloadUrl ? `<a class="button" href="${downloadUrl}" download="${this._text(downloadFilename)}">Stáhnout poslední export (${this._text(exportEntity?.attributes?.month)})</a>` : ""}</div>
          <div class="message">${this._text(this._message, exportEntity?.attributes?.generated_at ? `Poslední export: ${exportEntity.attributes.generated_at}, měsíc ${exportEntity.attributes.month}` : "Dosud nebyl vytvořen export.")}</div>
        </section>
        </div>
        ${this._activeTab === "history" ? `<div class="history-view">
          <section class="card daily-trips">
            <div class="history-heading">
              <div><h2>${this._text(this._historyMonthLabel(this._historyMonth))}</h2><div class="muted">Kliknutím na den zobrazíte jeho jízdy.</div></div>
              <div class="history-month-nav">
                <button id="history-previous" aria-label="Předchozí měsíc">‹</button>
                <input id="history-month" type="month" value="${this._text(this._historyMonth)}" aria-label="Měsíc historie">
                <button id="history-next" aria-label="Další měsíc">›</button>
                <button id="refresh-history" ${this._historyLoading ? "disabled" : ""}>Aktualizovat</button>
              </div>
            </div>
            <div class="history-status">${this._historyError
              ? `Historii se nepodařilo načíst: ${this._text(this._historyError)}`
              : this._historyLoading
                ? "Načítám historii…"
                : this._historyData
                  ? `Za měsíc: ${this._number(this._historyData.month_business_km)} služebních km, ${this._number(this._historyData.month_private_km)} soukromých km, ${this._text(this._historyData.month_trips, "0")} záznamů, ${this._text(this._historyData.month_review_trips, "0")} k revizi.`
                  : "Historie ještě není načtená."}</div>
            <div class="calendar-legend"><span><i class="legend-dot"></i>Služební km</span><span><i class="legend-dot private"></i>Soukromé km</span></div>
            ${this._historyCalendar()}
          </section>
          <section class="grid">
            <article class="card"><div class="muted">Vybraný den</div><div class="metric">${this._text(this._historyDateLabel(this._historyDate))}</div></article>
            <article class="card"><div class="muted">Služební</div><div class="metric">${this._number(selectedDay.business_km || 0)} km</div><small>${this._text(selectedDay.business_trips, "0")} jízd</small></article>
            <article class="card"><div class="muted">Soukromé</div><div class="metric">${this._number(selectedDay.private_km || 0)} km</div><small>${this._text(selectedDay.private_trips, "0")} jízd</small></article>
            <article class="card"><div class="muted">K revizi</div><div class="metric">${this._text(selectedDay.review_trips, "0")}</div><small>nezapočteno do typu</small></article>
          </section>
          <section class="card daily-trips"><h2>Jízdy vybraného dne</h2>
            <div class="muted">Historické záznamy lze opravit stejně jako dnešní jízdy.</div>
            ${this._tripTable(historyRows)}
          </section>
        </div>` : ""}
        ${this._activeTab === "map" ? `<section class="card">
          <div class="map-heading"><div><h2>Mapa uložených míst a zón</h2><div class="muted">Aktuální auto, naučené parkovací body, rozpoznávací poloměry a dnešní úseky.</div></div><button id="refresh-map" ${this._mapLoading ? "disabled" : ""}>Aktualizovat</button></div>
          <div class="map-loading"></div>
          <kniha-jizd-map></kniha-jizd-map>
        </section>` : ""}
        ${this._activeTab === "places" ? `<section class="card daily-trips">
          <div class="places-heading"><div><h2>Správa naučených míst</h2><div class="muted">Běžné místo má jeden typ; volbu „Služební i soukromé“ používejte jen pro skutečnou výjimku.</div></div>
            <div class="place-toolbar"><span class="selected-place-count">${this._selectedPlaces.size} vybráno</span><button id="merge-places" ${this._selectedPlaces.size < 2 || this._savingPlace ? "disabled" : ""}>Sloučit vybrané</button><button id="refresh-places" ${this._placesLoading || this._savingPlace ? "disabled" : ""}>Aktualizovat</button></div>
          </div>
          <div class="radius-summary">Aktivní výchozí poloměry: domov ${this._number(this._placesData?.radii?.home)} m · firma ${this._number(this._placesData?.radii?.company)} m · klient ${this._number(this._placesData?.radii?.business)} m · soukromé ${this._number(this._placesData?.radii?.private)} m · zastávka ${this._number(this._placesData?.radii?.transient)} m</div>
          <div class="history-status">${this._placesError ? `Správu míst se nepodařilo načíst: ${this._text(this._placesError)}` : this._placesLoading ? "Načítám místa…" : this._text(this._placesMessage, this._placesData ? `${this._placesData.places?.length || 0} uložených míst.` : "Místa ještě nejsou načtená.")}</div>
          ${this._placesTable()}
        </section>` : ""}
      </main>`;
    this.shadowRoot.querySelectorAll(".tab").forEach((button) => {
      button.addEventListener("click", () => this._selectTab(button.dataset.tab));
    });
    this.shadowRoot.getElementById("history-previous")?.addEventListener(
      "click",
      () => this._shiftHistoryMonth(-1),
    );
    this.shadowRoot.getElementById("history-next")?.addEventListener(
      "click",
      () => this._shiftHistoryMonth(1),
    );
    this.shadowRoot.getElementById("history-month")?.addEventListener(
      "change",
      (event) => this._changeHistoryMonth(event.target.value),
    );
    this.shadowRoot.getElementById("refresh-history")?.addEventListener(
      "click",
      () => this._loadHistoryData(),
    );
    this.shadowRoot.querySelectorAll(".calendar-day").forEach((button) => {
      button.addEventListener(
        "click",
        () => this._selectHistoryDate(button.dataset.historyDate),
      );
    });
    this.shadowRoot.getElementById("refresh-map")?.addEventListener("click", () => this._loadMapData());
    this.shadowRoot.getElementById("refresh-places")?.addEventListener("click", () => this._loadPlacesData());
    this.shadowRoot.getElementById("merge-places")?.addEventListener("click", () => this._mergeSelectedPlaces());
    this.shadowRoot.getElementById("month")?.addEventListener("change", (event) => {
      if (event.target.value) this._month = event.target.value;
    });
    this.shadowRoot.getElementById("export")?.addEventListener("click", () => this._exportExcel());
    this.shadowRoot.querySelectorAll(".save-trip").forEach((button) => {
      button.addEventListener("click", () => this._saveTrip(button));
    });
    this.shadowRoot.querySelectorAll(".save-place").forEach((button) => {
      button.addEventListener("click", () => this._savePlace(button));
    });
    this.shadowRoot.querySelectorAll(".delete-place").forEach((button) => {
      button.addEventListener("click", () => this._deletePlace(button));
    });
    this.shadowRoot.querySelectorAll(".place-select").forEach((input) => {
      input.addEventListener("change", () => this._togglePlaceSelection(input));
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
