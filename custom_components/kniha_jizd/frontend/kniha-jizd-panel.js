import "./kniha-jizd-map.js?v=1.14.0";

class KnihaJizdPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._exporting = false;
    this._message = "";
    this._savingTrip = null;
    this._resolvingTrip = null;
    this._questionValues = new Map();
    this._resolvedQuestions = new Set();
    this._placeQuestionValues = new Map();
    this._resolvedPlaceQuestions = new Set();
    this._resolvingPlace = null;
    this._scrollPositions = new Map();
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
    this._historyRequestId = 0;
    this._placesData = null;
    this._placesLoading = false;
    this._placesError = "";
    this._placesMessage = "";
    this._placesRequestId = 0;
    this._savingPlace = null;
    this._selectedPlaces = new Set();
    this._mapRequestId = 0;
    this._mapMessage = "";
    this._tripDrafts = new Map();
    this._placeDrafts = new Map();
    this._openDetails = new Set();
    this._overviewData = null;
    this._overviewSignature = "";
    this._overviewLoading = false;
    this._overviewError = "";
    this._overviewRequestId = 0;
    this._overviewLoadedAt = 0;
    this._overviewRefreshTimer = null;
    this._renderPending = false;
    this._pointerActive = false;
    this._interactionUntil = 0;
    this._idleRenderTimer = null;
    this.shadowRoot.addEventListener?.("focusout", () => {
      if (!this._renderPending) return;
      setTimeout(() => this._requestRender(), 0);
    });
    this.shadowRoot.addEventListener?.("pointerdown", () => {
      this._pointerActive = true;
    });
    ["pointerup", "pointercancel"].forEach((eventName) => {
      this.shadowRoot.addEventListener?.(eventName, () => {
        this._pointerActive = false;
        if (this._renderPending) this._requestRender();
      });
    });
  }

  set hass(value) {
    const firstConnection = !this._hass;
    this._hass = value;
    if (
      this._activeTab === "overview"
      &&
      !this._overviewLoading
      && !this._overviewRefreshTimer
      && Date.now() - this._overviewLoadedAt > 1000
    ) {
      this._overviewRefreshTimer = setTimeout(() => {
        this._overviewRefreshTimer = null;
        this._loadOverviewData();
      }, this._overviewData ? 250 : 0);
    }
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
    // Home Assistant replaces `hass` frequently. The overview API refresh below
    // provides the data used by this panel, so rebuilding the whole DOM here
    // would only interrupt scrolling, open details and in-progress edits.
    if (firstConnection || !this.shadowRoot?.querySelector?.("main")) this._render();
  }

  set narrow(value) {
    this._narrow = value;
  }

  set panel(value) {
    this._panel = value;
  }

  _requestRender() {
    const activeElement = this.shadowRoot?.activeElement;
    const interactionDelay = Math.max(0, this._interactionUntil - Date.now());
    if (
      this._pointerActive
      || activeElement?.matches?.("input, select, textarea")
      || interactionDelay > 0
    ) {
      this._renderPending = true;
      if (!this._pointerActive && !activeElement?.matches?.("input, select, textarea")) {
        if (this._idleRenderTimer) clearTimeout(this._idleRenderTimer);
        this._idleRenderTimer = setTimeout(() => {
          this._idleRenderTimer = null;
          this._requestRender();
        }, interactionDelay + 30);
      }
      return;
    }
    if (this._idleRenderTimer) clearTimeout(this._idleRenderTimer);
    this._idleRenderTimer = null;
    this._renderPending = false;
    this._render();
  }

  connectedCallback() {
    this._render();
    if (this._hass && !this._overviewData) this._loadOverviewData();
  }

  disconnectedCallback() {
    if (this._mapRefreshTimer) clearTimeout(this._mapRefreshTimer);
    this._mapRefreshTimer = null;
    if (this._overviewRefreshTimer) clearTimeout(this._overviewRefreshTimer);
    this._overviewRefreshTimer = null;
    if (this._idleRenderTimer) clearTimeout(this._idleRenderTimer);
    this._idleRenderTimer = null;
  }

  async _loadOverviewData() {
    if (!this._hass || this._overviewLoading) return;
    const requestId = ++this._overviewRequestId;
    const previousError = this._overviewError;
    let shouldRender = !this._overviewData;
    this._overviewLoading = true;
    this._overviewError = "";
    try {
      const data = await this._hass.callApi("GET", "kniha_jizd/overview");
      if (requestId !== this._overviewRequestId) return;
      const comparableData = { ...data };
      delete comparableData.generated_at;
      const nextSignature = JSON.stringify(comparableData);
      shouldRender = shouldRender || nextSignature !== this._overviewSignature || Boolean(previousError);
      this._overviewData = data;
      this._overviewSignature = nextSignature;
      this._overviewLoadedAt = Date.now();
      const questions = new Set(
        (data?.diagnostics?.today_trips || [])
          .filter((trip) => trip?.question)
          .map((trip) => String(trip.id)),
      );
      this._resolvedQuestions = new Set(
        [...this._resolvedQuestions].filter((segmentId) => questions.has(segmentId)),
      );
      const placeQuestions = new Set(
        (data?.diagnostics?.place_questions || data?.diagnostics?.today_trips || [])
          .filter((trip) => trip?.place_question)
          .map((trip) => String(trip.id)),
      );
      this._resolvedPlaceQuestions = new Set(
        [...this._resolvedPlaceQuestions].filter((segmentId) => placeQuestions.has(segmentId)),
      );
    } catch (error) {
      if (requestId !== this._overviewRequestId) return;
      this._overviewError = error.message || String(error);
      shouldRender = true;
    } finally {
      if (requestId === this._overviewRequestId) {
        this._overviewLoading = false;
        this._overviewLoadedAt = Date.now();
        if (this._activeTab !== "overview") return;
        if (shouldRender) this._requestRender();
      }
    }
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
      waiting_place_save: "Čeká na uložení místa",
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

  _tripValues(trip) {
    return {
      start: String(trip?.start_address ?? ""),
      end: String(trip?.end_address ?? ""),
      distance: String(trip?.distance_km ?? ""),
      purpose: String(trip?.purpose ?? ""),
      type: String(trip?.trip_type ?? "business"),
    };
  }

  _placeValues(place) {
    return {
      label: String(place?.label ?? ""),
      classification: String(place?.classification ?? "business"),
      radius: String(place?.radius_m ?? ""),
    };
  }

  _sameValues(left, right) {
    return Object.keys(right).every((key) => String(left?.[key] ?? "") === String(right[key] ?? ""));
  }

  _captureInteractiveState() {
    if (!this.shadowRoot) return;
    this.shadowRoot.querySelectorAll("tr[data-segment-id]").forEach((row) => {
      const segmentId = String(row.dataset.segmentId || "");
      if (!segmentId) return;
      const values = {
        start: row.querySelector(".trip-start")?.value ?? "",
        end: row.querySelector(".trip-end")?.value ?? "",
        distance: row.querySelector(".trip-distance")?.value ?? "",
        purpose: row.querySelector(".trip-purpose")?.value ?? "",
        type: row.querySelector(".trip-type")?.value ?? "business",
      };
      const originals = {
        start: row.querySelector(".trip-start")?.dataset?.originalValue ?? "",
        end: row.querySelector(".trip-end")?.dataset?.originalValue ?? "",
        distance: row.querySelector(".trip-distance")?.dataset?.originalValue ?? "",
        purpose: row.querySelector(".trip-purpose")?.dataset?.originalValue ?? "",
        type: row.querySelector(".trip-type")?.dataset?.originalValue ?? "business",
      };
      const existing = this._tripDrafts.get(segmentId);
      if (!this._sameValues(values, originals)) {
        this._tripDrafts.set(segmentId, {
          ...values,
          confirmedAt: existing && this._sameValues(existing, values)
            ? existing.confirmedAt
            : undefined,
        });
      }
      else this._tripDrafts.delete(segmentId);
    });
    this.shadowRoot.querySelectorAll("tr[data-place-id]").forEach((row) => {
      const placeId = String(row.dataset.placeId || "");
      if (!placeId) return;
      const values = {
        label: row.querySelector(".place-label")?.value ?? "",
        classification: row.querySelector(".place-classification")?.value ?? "business",
        radius: row.querySelector(".place-radius")?.value ?? "",
      };
      const originals = {
        label: row.querySelector(".place-label")?.dataset?.originalValue ?? "",
        classification: row.querySelector(".place-classification")?.dataset?.originalValue ?? "business",
        radius: row.querySelector(".place-radius")?.dataset?.originalValue ?? "",
      };
      if (!this._sameValues(values, originals)) this._placeDrafts.set(placeId, values);
      else this._placeDrafts.delete(placeId);
    });
    this.shadowRoot.querySelectorAll("details[data-details-key]").forEach((details) => {
      const detailsKey = String(details.dataset.detailsKey || "");
      if (!detailsKey) return;
      if (details.open) this._openDetails.add(detailsKey);
      else this._openDetails.delete(detailsKey);
    });
    this.shadowRoot.querySelectorAll(".question-card[data-segment-id]").forEach((card) => {
      const segmentId = String(card.dataset.segmentId || "");
      const value = card.querySelector(".question-value")?.value ?? "";
      if (segmentId && value) this._questionValues.set(segmentId, value);
    });
    this.shadowRoot.querySelectorAll(".place-question-card[data-segment-id]").forEach((card) => {
      const segmentId = String(card.dataset.segmentId || "");
      const value = card.querySelector(".place-question-value")?.value ?? "";
      if (segmentId) this._placeQuestionValues.set(segmentId, value);
    });
  }

  async _saveTrip(button) {
    if (!this._hass || this._savingTrip) return;
    const row = button.closest("tr");
    const segmentId = row?.dataset?.segmentId;
    const purpose = row?.querySelector(".trip-purpose")?.value?.trim() || "";
    const tripType = row?.querySelector(".trip-type")?.value || "business";
    const startInput = row?.querySelector(".trip-start");
    const endInput = row?.querySelector(".trip-end");
    const distanceInput = row?.querySelector(".trip-distance");
    const startAddress = startInput?.value?.trim() || "";
    const endAddress = endInput?.value?.trim() || "";
    const distanceValue = distanceInput?.value ?? "";
    const distanceKm = distanceValue === "" ? undefined : Number(distanceValue);
    const startChanged = startAddress !== (startInput?.dataset?.originalValue ?? "");
    const endChanged = endAddress !== (endInput?.dataset?.originalValue ?? "");
    const distanceChanged = distanceValue !== (distanceInput?.dataset?.originalValue ?? "");
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
        ...(startChanged ? { start_address: startAddress } : {}),
        ...(endChanged ? { end_address: endAddress } : {}),
        ...(distanceChanged && Number.isFinite(distanceKm) ? { distance_km: distanceKm } : {}),
      });
      const confirmedDraft = this._tripDrafts.get(segmentId);
      if (confirmedDraft) {
        confirmedDraft.confirmedAt = Date.now();
        this._tripDrafts.set(segmentId, confirmedDraft);
      }
      this._message = "Jízda byla upravena. Pokud tachometr ještě čeká, dokončí se automaticky.";
      await this._loadOverviewData();
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

  _tripTable(
    rows,
    emptyMessage = "Pro vybraný den není zaznamenána žádná jízda.",
    scrollKey = "overview-trips",
  ) {
    if (!Array.isArray(rows) || rows.length === 0) {
      return `<div class="muted">${this._text(emptyMessage)}</div>`;
    }
    return `<div class="table-wrap trip-table" data-scroll-key="${scrollKey}"><table><thead><tr>
      <th>Čas</th><th>Odkud</th><th>Kam</th><th>km</th><th>Zákazník / účel</th><th>Typ</th><th>Rozhodnutí</th><th>Stav</th><th></th>
    </tr></thead><tbody>${rows.map((trip) => {
      const segmentId = String(trip.id || "");
      const serverValues = this._tripValues(trip);
      let draft = this._tripDrafts.get(segmentId);
      if (draft && this._sameValues(draft, serverValues)) {
        this._tripDrafts.delete(segmentId);
        draft = null;
      }
      const values = draft || serverValues;
      const privateSelected = values.type === "private";
      const reviewSelected = values.type === "unclassified";
      const disabled = !trip.editable || this._savingTrip === trip.id;
      const dirty = Boolean(draft && !draft.confirmedAt);
      return `<tr data-segment-id="${this._text(trip.id)}">
        <td data-label="Čas">${this._time(trip.started_at)}</td>
        <td data-label="Odkud"><input class="trip-start" type="text" value="${this._text(values.start, "")}" data-original-value="${this._text(serverValues.start, "")}" placeholder="Místo odjezdu" ${disabled ? "disabled" : ""}></td>
        <td data-label="Kam"><input class="trip-end" type="text" value="${this._text(values.end, "")}" data-original-value="${this._text(serverValues.end, "")}" placeholder="Místo příjezdu" ${disabled ? "disabled" : ""}></td>
        <td data-label="Kilometry"><input class="trip-distance" type="number" min="0" step="1" value="${this._text(values.distance, "")}" data-original-value="${this._text(serverValues.distance, "")}" ${disabled ? "disabled" : ""}></td>
        <td data-label="Zákazník / účel"><input class="trip-purpose" type="text" value="${this._text(values.purpose, "")}" data-original-value="${this._text(serverValues.purpose, "")}" placeholder="Volitelný zákazník / účel" ${disabled ? "disabled" : ""}></td>
        <td data-label="Typ"><select class="trip-type" data-original-value="${this._text(serverValues.type, "business")}" ${disabled ? "disabled" : ""}>
          ${reviewSelected ? '<option value="unclassified" selected disabled>Nevyřešená – vyberte typ</option>' : ""}
          <option value="business" ${privateSelected || reviewSelected ? "" : "selected"}>Služební</option>
          <option value="private" ${privateSelected ? "selected" : ""}>Soukromá</option>
        </select></td>
        <td data-label="Rozhodnutí" class="wide-field">${this._decisionDetails(trip)}</td>
        <td data-label="Stav" class="wide-field">${trip.needs_review ? '<strong class="review-label">K revizi</strong> · ' : ""}${this._statusLabel(trip.status)}${trip.odometer_ready ? "" : " · čeká km"}<small>${this._text(trip.distance_reconciliation_source)}</small></td>
        <td data-label="Akce" class="table-action"><button class="save-trip" ${disabled || !dirty ? "disabled" : ""}>${this._savingTrip === trip.id ? "Ukládám…" : draft?.confirmedAt ? "Uloženo" : "Uložit"}</button></td>
      </tr>`;
    }).join("")}</tbody></table></div>`;
  }

  _questionCards(rows) {
    const questions = (Array.isArray(rows) ? rows : []).filter(
      (trip) => trip?.question && !this._resolvedQuestions.has(String(trip.id)),
    );
    if (!questions.length) return "";
    return `<section class="questions" aria-labelledby="questions-title">
      <div class="section-heading"><div><h2 id="questions-title">Potřebuje vaši odpověď</h2><div class="muted">Vyřešte jen nejasné jízdy; telefon není potřeba.</div></div><span class="count-badge">${questions.length}</span></div>
      <div class="question-grid">${questions.map((trip) => this._questionCard(trip)).join("")}</div>
    </section>`;
  }

  _questionCard(trip) {
    const question = trip.question || {};
    const segmentId = String(trip.id || "");
    const resolving = this._resolvingTrip === segmentId;
    const phoneLabel = {
      sent: "Dotaz byl poslán i do telefonu",
      panel_only: "Jen v panelu – telefon nebyl rušen",
      waiting: "Telefon počká, zda jízda nebude pokračovat",
      not_scheduled: "Dotaz je dostupný v panelu",
    }[question.phone_state] || "Dotaz je dostupný v panelu";
    const value = this._questionValues.get(segmentId) || "";
    return `<article class="question-card" data-segment-id="${this._text(segmentId)}">
      <div class="question-meta"><span>${this._time(trip.started_at)}</span><span>${this._text(phoneLabel)}</span></div>
      <h3>${this._text(question.title, "Zařaďte jízdu")}</h3>
      <p>${this._text(question.prompt)}</p>
      <div class="question-route"><span>${this._text(trip.start_address)}</span><span aria-hidden="true">→</span><span>${this._text(trip.end_address)}</span></div>
      ${question.purpose_input ? `<label class="question-input"><span>Zákazník nebo účel <small>(volitelné, jen pro služební jízdu)</small></span><input class="question-value" type="text" value="${this._text(value, "")}" placeholder="Např. FN Brno nebo servis" ${resolving ? "disabled" : ""}></label>` : ""}
      ${(question.candidates || []).length ? `<div class="question-suggestions"><small>Mapové návrhy pouze předvyplní účel:</small>${question.candidates.map((candidate) => `<button class="use-purpose-suggestion secondary" data-value="${this._text(candidate.name)}" ${resolving ? "disabled" : ""}>${this._text(candidate.name)}</button>`).join("")}</div>` : ""}
      <div class="question-actions">${(question.actions || []).map((action) => `<button class="resolve-trip ${action.id === "private" ? "secondary" : ""}" data-action="${this._text(action.id)}" ${resolving ? "disabled" : ""}>${this._text(action.label)}</button>`).join("")}</div>
      <details class="question-details decision" data-details-key="question:${this._text(segmentId)}" ${this._openDetails.has(`question:${segmentId}`) ? "open" : ""}><summary>Podrobnosti návrhu</summary>${this._decisionDetails(trip, false)}</details>
    </article>`;
  }

  async _resolveTrip(button) {
    if (!this._hass || this._resolvingTrip) return;
    const card = button.closest(".question-card");
    const segmentId = String(card?.dataset?.segmentId || "");
    const action = String(button.dataset.action || "");
    const value = card?.querySelector(".question-value")?.value?.trim() || "";
    if (!segmentId || !action) return;

    this._resolvingTrip = segmentId;
    this._message = "Ukládám rozhodnutí…";
    this._render();
    try {
      await this._hass.callService("kniha_jizd", "resolve_trip", {
        segment_id: segmentId,
        action,
        ...(value ? { value } : {}),
      });
      this._resolvedQuestions.add(segmentId);
      this._questionValues.delete(segmentId);
      this._message = "Rozhodnutí bylo uloženo.";
      this._overviewLoadedAt = 0;
      await this._loadOverviewData();
      if (this._activeTab === "history") await this._loadHistoryData();
    } catch (error) {
      this._message = `Rozhodnutí se nepodařilo uložit: ${error.message || error}`;
    } finally {
      this._resolvingTrip = null;
      this._render();
    }
  }

  _placeQuestionCards(rows) {
    const questions = (Array.isArray(rows) ? rows : []).filter(
      (trip) => trip?.place_question && !this._resolvedPlaceQuestions.has(String(trip.id)),
    );
    if (!questions.length) return "";
    return `<section class="questions" aria-labelledby="place-questions-title">
      <div class="section-heading"><div><h2 id="place-questions-title">Uložit místa pro příště?</h2><div class="muted">Tyto jízdy už jsou zařazené. Teď jen rozhodujete o budoucím rozpoznání cíle.</div></div><span class="count-badge place-count">${questions.length}</span></div>
      <div class="question-grid">${questions.map((trip) => this._placeQuestionCard(trip)).join("")}</div>
    </section>`;
  }

  _placeQuestionCard(trip) {
    const question = trip.place_question || {};
    const segmentId = String(trip.id || "");
    const resolving = this._resolvingPlace === segmentId;
    const value = this._placeQuestionValues.has(segmentId)
      ? this._placeQuestionValues.get(segmentId)
      : String(question.suggested_label || "");
    return `<article class="question-card place-question-card" data-segment-id="${this._text(segmentId)}">
      <div class="question-meta"><span>${this._time(trip.started_at)}</span><span>Jízda je už zařazená</span></div>
      <h3>${this._text(question.title, "Uložit místo pro příště?")}</h3>
      <p>${this._text(question.prompt)}</p>
      <div class="question-route"><span>${this._text(trip.start_address)}</span><span aria-hidden="true">→</span><span>${this._text(trip.end_address)}</span></div>
      <label class="question-input"><span>Název uloženého místa</span><input class="place-question-value" type="text" value="${this._text(value, "")}" placeholder="Název místa" ${resolving ? "disabled" : ""}></label>
      ${(question.candidates || []).length ? `<div class="question-suggestions">${question.candidates.map((candidate) => `<button class="use-place-suggestion secondary" data-value="${this._text(candidate.name)}" ${resolving ? "disabled" : ""}>${this._text(candidate.name)}</button>`).join("")}</div>` : ""}
      <div class="question-actions"><button class="resolve-place" data-action="save" ${resolving ? "disabled" : ""}>Uložit pro příště</button><button class="resolve-place secondary" data-action="skip" ${resolving ? "disabled" : ""}>Jen tentokrát</button></div>
    </article>`;
  }

  async _resolvePlace(button) {
    if (!this._hass || this._resolvingPlace) return;
    const card = button.closest(".place-question-card");
    const segmentId = String(card?.dataset?.segmentId || "");
    const action = String(button.dataset.action || "");
    const value = card?.querySelector(".place-question-value")?.value?.trim() || "";
    if (!segmentId || !action) return;
    if (action === "save" && !value) {
      this._message = "Nejdřív zadejte název místa.";
      this._render();
      return;
    }
    this._resolvingPlace = segmentId;
    this._message = action === "save" ? "Ukládám místo…" : "Nabídku místa zavírám…";
    this._render();
    try {
      await this._hass.callService("kniha_jizd", "save_trip_place", {
        segment_id: segmentId,
        action,
        ...(action === "save" ? { value } : {}),
      });
      this._resolvedPlaceQuestions.add(segmentId);
      this._placeQuestionValues.delete(segmentId);
      this._message = action === "save" ? "Místo bylo uloženo pro příští rozpoznání." : "Místo se nebude učit.";
      this._overviewLoadedAt = 0;
      await this._loadOverviewData();
    } catch (error) {
      this._message = `Rozhodnutí o místě se nepodařilo uložit: ${error.message || error}`;
    } finally {
      this._resolvingPlace = null;
      this._render();
    }
  }

  _decisionDetails(trip, wrap = true) {
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
    const decisionId = String(trip?.id || "");
    const content = `<div>${this._text(decision.explanation)}</div>
      <small>Zdroj: ${this._text(decision.source)} · jistota: ${this._text(decision.confidence)}</small>
      ${decision.matched_place_id || decision.distance_m !== null && decision.distance_m !== undefined
        ? `<small>Místo: ${this._text(decision.matched_place_label)} · vzdálenost ${distance} / poloměr ${radius} · ${this._text(decision.match_method)}</small>`
        : ""}
      ${decision.return_gap_minutes !== null && decision.return_gap_minutes !== undefined
        ? `<small>Návaznost: ${this._number(decision.return_gap_minutes, 1)} min · ${this._text(decision.return_reason)}</small>`
        : ""}
      ${searchStatus
        ? `<small>Hledání institucí: ${this._text(searchStatus)} · pokusy ${this._text(decision.candidate_search_attempts, "0")}${decision.candidate_search_cache_hit ? " · cache" : ""}${decision.candidate_search_error ? ` · ${this._text(decision.candidate_search_error)}` : ""}</small>`
        : ""}`;
    if (!wrap) return content;
    return `<details class="decision" data-details-key="decision:${this._text(decisionId)}" ${this._openDetails.has(`decision:${decisionId}`) ? "open" : ""}><summary>${this._text(decision.source_label, "Proč takto?")}</summary>
      ${content}</details>`;
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
    return `<div class="calendar-scroll" data-scroll-key="history-calendar"><div class="calendar-content">
      <div class="calendar-weekdays" aria-hidden="true">
        ${["Po", "Út", "St", "Čt", "Pá", "So", "Ne"].map((day) => `<span>${day}</span>`).join("")}
      </div><div class="calendar-grid">${cells.join("")}</div>
    </div></div>`;
  }

  async _loadHistoryData() {
    if (!this._hass) return;
    const requestId = ++this._historyRequestId;
    const requestedMonth = this._historyMonth;
    const requestedDate = this._historyDate;
    this._historyLoading = true;
    this._historyError = "";
    this._render();
    try {
      const query = `month=${encodeURIComponent(requestedMonth)}&date=${encodeURIComponent(requestedDate)}`;
      const data = await this._hass.callApi(
        "GET",
        `kniha_jizd/history?${query}`,
      );
      if (requestId !== this._historyRequestId) return;
      this._historyData = data;
    } catch (error) {
      if (requestId !== this._historyRequestId) return;
      this._historyError = error.message || String(error);
    } finally {
      if (requestId === this._historyRequestId) {
        this._historyLoading = false;
        this._render();
      }
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
      let exportData = null;
      for (let attempt = 0; attempt < 20; attempt += 1) {
        await this._loadOverviewData();
        exportData = this._overviewData?.export;
        if (exportData?.download_url) break;
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
      const url = exportData?.download_url;
      if (!url) throw new Error("Odkaz ke stažení nebyl vytvořen");
      this._message = "Excel je hotový. Stahování začíná…";
      const link = document.createElement("a");
      link.href = url;
      link.download = exportData?.filename || `kniha_jizd_${this._month}.xlsx`;
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
    if (tab === "overview" && Date.now() - this._overviewLoadedAt > 1000) {
      await this._loadOverviewData();
    }
    if (tab === "map" && !this._mapData) await this._loadMapData();
    if (tab === "history" && !this._historyData) await this._loadHistoryData();
    if (tab === "places" && !this._placesData) await this._loadPlacesData();
  }

  _syncMapElement() {
    const map = this.shadowRoot?.querySelector("kniha-jizd-map");
    if (map) {
      if (!map._knihaJizdDeleteBound) {
        map._knihaJizdDeleteBound = true;
        map.addEventListener("kniha-jizd-delete-map-place", (event) => {
          this._deleteMapPlace(event.detail);
        });
      }
      if (this._mapData) map.data = this._mapData;
    }
    const status = this.shadowRoot?.querySelector(".map-loading");
    if (status) {
      status.textContent = this._mapError
        ? `Mapová data se nepodařilo načíst: ${this._mapError}`
        : this._mapLoading
          ? "Načítám aktuální polohu, místa a zóny…"
          : this._mapMessage
            ? this._mapMessage
            : this._mapData
              ? `Aktualizováno ${new Date(this._mapData.generated_at || Date.now()).toLocaleString("cs-CZ")}`
              : "Mapová data ještě nejsou načtená.";
    }
    const refresh = this.shadowRoot?.getElementById("refresh-map");
    if (refresh) refresh.disabled = this._mapLoading;
  }

  async _loadMapData() {
    if (!this._hass || this._savingPlace) return;
    const requestId = ++this._mapRequestId;
    this._mapLoading = true;
    this._mapError = "";
    this._mapMessage = "";
    this._syncMapElement();
    try {
      const data = await this._hass.callApi("GET", "kniha_jizd/map");
      if (requestId !== this._mapRequestId) return;
      this._mapData = data;
    } catch (error) {
      if (requestId !== this._mapRequestId) return;
      this._mapError = error.message || String(error);
    } finally {
      if (requestId === this._mapRequestId) {
        this._mapLoadedAt = Date.now();
        this._mapLoading = false;
        this._syncMapElement();
      }
    }
  }

  async _deleteMapPlace(detail) {
    if (!this._hass || this._savingPlace) return;
    const placeId = String(detail?.placeId || "");
    const markerId = String(detail?.markerId || "");
    const anchorIndex = Number(detail?.anchorIndex);
    const label = String(detail?.label || detail?.address || "tento bod");
    if (!placeId || placeId.startsWith("configured:") || !Number.isInteger(anchorIndex) || anchorIndex < 0) return;
    if (!window.confirm(`Opravdu odstranit označený bod „${label}“? Historické jízdy zůstanou zachované.`)) return;

    const requestId = ++this._mapRequestId;
    this._placesRequestId += 1;
    this._placesLoading = false;
    const workingId = `map:${markerId || placeId}`;
    this._savingPlace = workingId;
    this._mapLoading = true;
    this._mapError = "";
    this._mapMessage = "Odstraňuji označený bod…";
    this._syncMapElement();
    try {
      const result = await this._hass.callApi("POST", "kniha_jizd/places", {
        action: "delete_anchor",
        place_id: placeId,
        anchor_index: anchorIndex,
      });
      if (requestId !== this._mapRequestId) return;
      this._placesData = result.data || this._placesData;
      this._placeDrafts.delete(placeId);
      this._selectedPlaces.delete(placeId);
      this._mapMessage = "Označený bod byl odstraněn.";
      try {
        this._mapData = await this._hass.callApi("GET", "kniha_jizd/map");
        if (requestId !== this._mapRequestId) return;
        this._mapLoadedAt = Date.now();
      } catch (refreshError) {
        if (requestId !== this._mapRequestId) return;
        this._mapData = null;
        this._mapMessage = `Bod byl odstraněn, ale mapu se nepodařilo obnovit: ${refreshError.message || refreshError}`;
      }
    } catch (error) {
      if (requestId !== this._mapRequestId) return;
      this._mapError = error.message || String(error);
    } finally {
      if (this._savingPlace === workingId) this._savingPlace = null;
      if (requestId === this._mapRequestId) {
        this._mapLoading = false;
        this._syncMapElement();
      }
    }
  }

  async _loadPlacesData() {
    if (!this._hass || this._savingPlace) return;
    const requestId = ++this._placesRequestId;
    this._placesLoading = true;
    this._placesError = "";
    this._render();
    try {
      const data = await this._hass.callApi("GET", "kniha_jizd/places");
      if (requestId !== this._placesRequestId) return;
      this._placesData = data;
      const validIds = new Set((this._placesData?.places || []).map((place) => String(place.id)));
      this._selectedPlaces = new Set(
        [...this._selectedPlaces].filter((placeId) => validIds.has(placeId)),
      );
    } catch (error) {
      if (requestId !== this._placesRequestId) return;
      this._placesError = error.message || String(error);
    } finally {
      if (requestId === this._placesRequestId) {
        this._placesLoading = false;
        this._render();
      }
    }
  }

  async _placeAction(payload, workingId) {
    if (!this._hass || this._savingPlace) return;
    this._placesRequestId += 1;
    this._placesLoading = false;
    this._savingPlace = workingId;
    this._placesError = "";
    this._placesMessage = "Ukládám změnu místa…";
    this._render();
    try {
      const result = await this._hass.callApi("POST", "kniha_jizd/places", payload);
      this._placesData = result.data || this._placesData;
      this._placesMessage = "Změna místa byla uložena.";
      if (payload.place_id) this._placeDrafts.delete(String(payload.place_id));
      if (["delete", "delete_anchor", "merge"].includes(payload.action)) {
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
    if (!window.confirm(`Sloučit ${placeIds.length} vybrané GPS duplicity? Sloučení projde pouze tehdy, když jsou všechny body nejvýše 25 m od sebe.`)) return;
    this._placeAction({ action: "merge", place_ids: placeIds }, "merge");
  }

  _placesTable() {
    const places = this._placesData?.places || [];
    const configuredPlaces = this._placesData?.configured_places || [];
    const visiblePointIds = new Set(this._placesData?.visible_learned_point_ids || []);
    if (!places.length && !configuredPlaces.length) {
      return '<div class="muted">Zatím nejsou uložena ani nakonfigurována žádná místa.</div>';
    }
    const disabled = Boolean(this._savingPlace);
    const configuredRows = configuredPlaces.map((place) => {
      const typeLabel = place.place_role === "home" ? "Podle směru jízdy" : "Služební";
      return `<tr class="configured-place-row">
        <td data-label="Výběr"></td>
        <td data-label="Název"><strong>${this._text(place.label)}</strong><small>Konfigurované místo · upravuje se v nastavení integrace</small></td>
        <td data-label="Typ">${this._text(typeLabel)}</td>
        <td data-label="Poloměr">${this._number(place.radius_m)} m</td>
        <td data-label="Fyzický bod"><div class="place-anchor">
          <div><strong>${this._text(place.address, place.label)}</strong><small>${this._number(place.latitude, 5)}, ${this._number(place.longitude, 5)}</small></div>
          <span class="map-point-status visible">Na mapě</span>
        </div></td>
        <td data-label="Akce"></td>
      </tr>`;
    }).join("");
    const learnedRows = places.map((place) => {
      const placeId = String(place.id || "");
      const serverValues = this._placeValues(place);
      let draft = this._placeDrafts.get(placeId);
      if (draft && this._sameValues(draft, serverValues)) {
        this._placeDrafts.delete(placeId);
        draft = null;
      }
      const values = draft || serverValues;
      const anchors = Array.isArray(place.anchors) ? place.anchors : [];
      const anchor = anchors[0] || {};
      const visible = visiblePointIds.has(`${place.id}:0`);
      const hasCoordinates = [anchor.latitude, anchor.longitude].every(
        (value) => value !== null && value !== undefined && value !== "" && Number.isFinite(Number(value)),
      );
      const hiddenLabel = hasCoordinates
          ? "Skryto – v zóně domova/firmy"
          : "Skryto – bez souřadnic";
      const point = anchors.length ? `<div class="place-anchor">
          <div><strong>${this._text(anchor.address, "bez adresy")}</strong><small>${this._number(anchor.latitude, 5)}, ${this._number(anchor.longitude, 5)}</small></div>
          <span class="map-point-status ${visible ? "visible" : "hidden"}">${visible ? "Na mapě" : hiddenLabel}</span>
        </div>` : '<div class="muted">Bod nemá použitelné souřadnice ani adresu.</div>';
      return `<tr data-place-id="${this._text(place.id)}">
        <td data-label="Výběr"><input class="place-select" type="checkbox" value="${this._text(place.id)}" ${this._selectedPlaces.has(String(place.id)) ? "checked" : ""} ${disabled ? "disabled" : ""}></td>
        <td data-label="Název"><input class="place-label" type="text" value="${this._text(values.label, "")}" data-original-value="${this._text(serverValues.label, "")}" ${disabled ? "disabled" : ""}></td>
        <td data-label="Typ"><select class="place-classification" data-original-value="${this._text(serverValues.classification, "business")}" ${disabled ? "disabled" : ""}>
          <option value="business" ${values.classification === "business" ? "selected" : ""}>Služební</option>
          <option value="private" ${values.classification === "private" ? "selected" : ""}>Soukromé</option>
          <option value="mixed" ${values.classification === "mixed" ? "selected" : ""}>Služební i soukromé</option>
        </select></td>
        <td data-label="Poloměr"><input class="place-radius" type="number" min="25" max="5000" step="25" value="${this._text(values.radius, "")}" data-original-value="${this._text(serverValues.radius, "")}" ${disabled ? "disabled" : ""}> m</td>
        <td data-label="Fyzický bod">${point}</td>
        <td data-label="Akce" class="table-action"><div class="place-actions"><button class="save-place" ${disabled ? "disabled" : ""}>Uložit</button><button class="delete-place danger" ${disabled ? "disabled" : ""}>Odstranit bod</button></div></td>
      </tr>`;
    }).join("");
    return `<div class="table-wrap places-table" data-scroll-key="places"><table><thead><tr>
      <th></th><th>Název</th><th>Typ</th><th>Poloměr</th><th>Fyzický bod</th><th></th>
    </tr></thead><tbody>${configuredRows}${learnedRows}</tbody></table></div>`;
  }

  _render() {
    if (!this.shadowRoot) return;
    this._captureInteractiveState();
    this.shadowRoot.querySelectorAll?.("[data-scroll-key]").forEach((surface) => {
      this._scrollPositions.set(surface.dataset.scrollKey, surface.scrollLeft);
    });
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
    const overview = this._overviewData || {};
    const statistics = overview.statistics || {};
    const attrs = overview.diagnostics || status?.attributes || {};
    const statusValue = overview.status || status?.state;
    const readyValue = overview.ready ?? ready?.state === "on";
    const businessValue = statistics.today_business_km ?? business?.state;
    const privateValue = statistics.today_private_km ?? privateKm?.state;
    const todayTripsValue = statistics.today_segments ?? todayTrips?.state;
    const pendingValue = (
      Number(attrs.closing_count || 0)
      + Number(attrs.pending_count || 0)
      + Number(attrs.transient_count || 0)
      + Number(attrs.place_prompt_count || 0)
    );
    const totalTripsValue = statistics.segments_total ?? totalTrips?.state;
    const totalBusinessValue = statistics.business_km_total ?? totalBusiness?.state;
    const totalPrivateValue = statistics.private_km_total ?? totalPrivate?.state;
    const odometerCheck = attrs.odometer_day_check || {};
    const todayTripRows = attrs.today_trips || [];
    const historyRows = this._historyData?.rows || [];
    const selectedDay = (this._historyData?.days || []).find(
      (day) => day.date === this._historyDate,
    ) || {};
    const last = overview.last_trip || lastTrip?.attributes || {};
    const exportData = overview.export || exportEntity?.attributes || {};
    const rawDownloadUrl = exportData.download_url;
    const expiresAt = Date.parse(exportData.expires_at || "");
    const downloadUrl = rawDownloadUrl && expiresAt > Date.now() ? rawDownloadUrl : null;
    const downloadFilename = exportData.filename || "kniha_jizd.xlsx";
    const gpsDetail = attrs.gps_ok
      ? `${attrs.gps_entity}: ${attrs.latitude}, ${attrs.longitude} · ${attrs.gps_coordinate_source}`
      : `${attrs.gps_entity}: stav ${attrs.gps_state ?? "—"} · souřadnice nenalezeny ani v ${attrs.address_entity}`;
    const odometerDetail = attrs.odometer_ok
      ? `${attrs.odometer_entity}: ${attrs.odometer_km} km · ${attrs.odometer_value_source}`
      : `${attrs.odometer_entity}: stav ${attrs.odometer_state ?? "—"} · číselná hodnota nenalezena`;

    this.shadowRoot.innerHTML = `
      <style>
        :host { display:block; min-height:100%; color:var(--primary-text-color); background:linear-gradient(180deg,color-mix(in srgb,var(--primary-color) 5%,var(--primary-background-color)) 0,var(--primary-background-color) 260px); }
        * { box-sizing:border-box; }
        main { width:100%; max-width:1560px; min-width:0; margin:0 auto; padding:clamp(16px,2.5vw,36px); font-family:var(--paper-font-body1_-_font-family, sans-serif); }
        header { display:flex; justify-content:space-between; gap:16px; align-items:center; margin-bottom:22px; flex-wrap:wrap; }
        .title-subtitle { margin-top:5px; font-size:14px; }
        h1 { margin:0; font-size:clamp(26px,3vw,36px); line-height:1.08; letter-spacing:-.025em; } h2 { margin:0 0 16px; font-size:19px; }
        .pill { display:inline-flex; align-items:center; gap:8px; border:1px solid var(--divider-color); border-radius:999px; padding:8px 14px; font-weight:700; background:color-mix(in srgb,var(--card-background-color) 88%,transparent); box-shadow:0 2px 8px rgba(0,0,0,.05); }
        .pill::before { width:8px; height:8px; border-radius:50%; background:currentColor; content:""; }
        .pill.ready { color:var(--success-color, #2e7d32); } .pill.error { color:var(--error-color, #c62828); }
        .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:16px; margin-bottom:16px; }
        .card { min-width:0; max-width:100%; border:1px solid color-mix(in srgb,var(--divider-color) 85%,transparent); border-radius:16px; padding:20px; background:var(--card-background-color); box-shadow:0 6px 24px rgba(0,0,0,.07); }
        .metric-card { position:relative; overflow:hidden; padding-top:17px; }
        .metric-card::before { position:absolute; inset:0 auto auto 0; width:100%; height:3px; background:color-mix(in srgb,var(--primary-color) 65%,transparent); content:""; }
        .card.attention .metric { color:var(--warning-color,#ef6c00); }
        .metric { font-size:30px; font-weight:750; margin-top:8px; letter-spacing:-.025em; } .muted, small { color:var(--secondary-text-color); }
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
        input[type="text"], input[type="number"], select { width:100%; min-width:130px; color:var(--primary-text-color); background:var(--card-background-color); border:1px solid var(--divider-color); border-radius:9px; padding:9px 10px; font:inherit; transition:border-color .15s,box-shadow .15s; }
        input:hover, select:hover { border-color:color-mix(in srgb,var(--primary-color) 55%,var(--divider-color)); }
        .trip-distance { min-width:85px !important; width:95px !important; }
        button, a.button { border:0; border-radius:10px; padding:12px 18px; font:inherit; font-weight:650; cursor:pointer; text-decoration:none; transition:transform .12s,box-shadow .12s,background-color .12s; }
        button { color:var(--text-primary-color,#fff); background:var(--primary-color); }
        button:not(:disabled):hover, a.button:hover { transform:translateY(-1px); box-shadow:0 5px 14px rgba(0,0,0,.12); }
        button.secondary { color:var(--primary-text-color); background:var(--secondary-background-color); border:1px solid var(--divider-color); }
        button:disabled { opacity:.6; cursor:wait; } a.button { color:var(--primary-color); background:var(--secondary-background-color); }
        .message { margin-top:12px; color:var(--secondary-text-color); }
        .global-message { margin:-10px 0 18px; padding:11px 14px; border-radius:10px; color:var(--primary-text-color); background:color-mix(in srgb,var(--primary-color) 9%,var(--card-background-color)); }
        button:focus-visible, input:focus-visible, select:focus-visible, summary:focus-visible, a:focus-visible { outline:3px solid var(--primary-color); outline-offset:2px; }
        .advanced { align-self:start; padding:0; overflow:hidden; }
        .advanced > summary { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:18px 20px; cursor:pointer; font-size:16px; font-weight:700; list-style:none; }
        .advanced > summary::-webkit-details-marker { display:none; }
        .advanced > summary::after { flex:none; width:9px; height:9px; border-right:2px solid currentColor; border-bottom:2px solid currentColor; content:""; transform:rotate(45deg); transition:transform .18s; }
        .advanced[open] > summary::after { transform:rotate(225deg); }
        .advanced[open] > summary { border-bottom:1px solid var(--divider-color); }
        .advanced-body { margin-top:14px; }
        .advanced > .advanced-body { margin:0; padding:18px 20px 20px; }
        .last-trip-heading { display:flex; align-items:flex-start; justify-content:space-between; gap:12px; }
        .last-trip-heading h2 { margin:3px 0 0; }
        .last-trip-heading > strong { font-size:24px; white-space:nowrap; }
        .last-trip-route { display:flex; align-items:center; gap:8px; margin:11px 0 8px; padding:9px 10px; border-radius:9px; background:var(--secondary-background-color); font-size:13px; }
        .last-trip-route span:not([aria-hidden]) { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        .inline-error { margin:0 0 16px; padding:12px 14px; border-radius:10px; color:var(--error-color,#c62828); background:color-mix(in srgb,var(--error-color,#c62828) 10%,transparent); }
        .questions { margin-bottom:18px; }
        .section-heading { display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom:10px; }
        .section-heading h2 { margin:0 0 3px; }
        .count-badge { display:grid; place-items:center; min-width:30px; height:30px; padding:0 9px; border-radius:999px; color:var(--text-primary-color,#fff); background:var(--warning-color,#ef6c00); font-weight:800; }
        .question-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,330px),1fr)); gap:12px; }
        .question-card { min-width:0; padding:17px; border:1px solid color-mix(in srgb,var(--warning-color,#ef6c00) 42%,var(--divider-color)); border-radius:14px; background:var(--card-background-color); box-shadow:var(--ha-card-box-shadow,0 2px 8px rgba(0,0,0,.1)); }
        .question-card h3 { margin:7px 0 7px; font-size:17px; }
        .question-card p { margin:0 0 10px; }
        .question-meta, .question-route { display:flex; align-items:center; justify-content:space-between; gap:8px; color:var(--secondary-text-color); font-size:12px; }
        .question-route { justify-content:flex-start; margin:10px 0; padding:9px 10px; border-radius:9px; background:var(--secondary-background-color); font-size:13px; }
        .question-route span { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        .question-input { display:grid; gap:5px; margin:11px 0; color:var(--secondary-text-color); font-size:12px; }
        .question-actions { display:flex; gap:7px; flex-wrap:wrap; margin-top:12px; }
        .question-actions button { flex:1 1 135px; min-height:42px; padding:9px 11px; }
        .question-suggestions { display:flex; gap:6px; flex-wrap:wrap; align-items:center; margin:8px 0; }
        .question-suggestions small { flex-basis:100%; }
        .question-suggestions button { padding:6px 9px; font-size:12px; }
        .count-badge.place-count { background:var(--primary-color); }
        .question-details { margin-top:11px; color:var(--secondary-text-color); font-size:12px; }
        .question-details summary { cursor:pointer; color:var(--primary-color); font-weight:600; }
        .table-wrap, .calendar-scroll { position:relative; display:block; width:100%; max-width:100%; min-width:0; overflow-x:auto; overflow-y:hidden; overscroll-behavior-x:contain; -webkit-overflow-scrolling:touch; touch-action:pan-x pan-y; scrollbar-gutter:stable; padding-bottom:8px; }
        table { width:100%; min-width:1120px; border-collapse:separate; border-spacing:0; margin-top:12px; }
        th, td { text-align:left; vertical-align:top; border-bottom:1px solid var(--divider-color); padding:11px 9px; min-width:75px; }
        th { position:sticky; top:0; z-index:1; color:var(--secondary-text-color); background:var(--card-background-color); font-size:12px; font-weight:700; letter-spacing:.025em; text-transform:uppercase; }
        tbody tr { transition:background-color .12s; }
        tbody tr:hover { background:color-mix(in srgb,var(--primary-color) 4%,transparent); }
        td:nth-child(2), td:nth-child(3) { min-width:180px; }
        .save-trip { padding:9px 13px; white-space:nowrap; }
        .decision { min-width:220px; max-width:330px; }
        .decision summary { cursor:pointer; color:var(--primary-color); font-weight:600; }
        .decision div { margin-top:6px; }
        .review-label { color:var(--warning-color,#ef6c00); }
        .daily-trips, .history-view { min-width:0; }
        .daily-trips { overflow:visible; margin-bottom:18px; }
        .primary-section { border-color:color-mix(in srgb,var(--primary-color) 24%,var(--divider-color)); box-shadow:0 10px 34px rgba(0,0,0,.09); }
        .section-titlebar { display:flex; align-items:flex-start; justify-content:space-between; gap:14px; margin-bottom:2px; flex-wrap:wrap; }
        .section-titlebar h2 { margin:0 0 4px; font-size:22px; }
        .section-kicker { color:var(--primary-color); font-size:12px; font-weight:800; letter-spacing:.06em; text-transform:uppercase; }
        .overview-secondary { display:grid; grid-template-columns:minmax(0,1.25fr) minmax(300px,.75fr); gap:16px; margin-bottom:18px; }
        .overview-secondary > .card { margin:0; }
        .diagnostics-section { margin-top:18px; }
        .diagnostics-heading { margin:0 0 11px; }
        .diagnostics-heading h2 { margin:0 0 4px; }
        .diagnostics-grid { display:grid; grid-template-columns:minmax(280px,.8fr) minmax(420px,1.2fr); gap:16px; }
        .tabs { display:flex; gap:5px; margin:-6px 0 22px; padding:5px; width:100%; max-width:760px; overflow-x:auto; border:1px solid color-mix(in srgb,var(--divider-color) 75%,transparent); border-radius:14px; background:color-mix(in srgb,var(--secondary-background-color) 82%,transparent); scrollbar-width:thin; }
        .tab { flex:1 0 125px; min-width:0; padding:10px 15px; color:var(--primary-text-color); background:transparent; box-shadow:none; }
        .tab.active { color:var(--text-primary-color,#fff); background:var(--primary-color); box-shadow:0 4px 12px color-mix(in srgb,var(--primary-color) 28%,transparent); }
        .map-heading { display:flex; justify-content:space-between; align-items:center; gap:12px; margin-bottom:12px; flex-wrap:wrap; }
        .map-heading h2 { margin:0; }
        .map-heading button { padding:9px 14px; }
        .map-loading { min-height:20px; margin-bottom:12px; color:var(--secondary-text-color); }
        .map-card, kniha-jizd-map { display:block; width:100%; min-width:0; }
        .history-heading { display:flex; justify-content:space-between; align-items:center; gap:14px; margin-bottom:16px; flex-wrap:wrap; }
        .history-heading h2 { margin:0; text-transform:capitalize; }
        .history-month-nav { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
        .history-month-nav button { padding:9px 13px; }
        .history-month-nav input { margin:0; }
        .calendar-legend { display:flex; gap:14px; flex-wrap:wrap; margin:0 0 12px; color:var(--secondary-text-color); }
        .calendar-legend span { display:flex; align-items:center; gap:6px; }
        .legend-dot { width:10px; height:10px; border-radius:999px; background:#1976d2; }
        .legend-dot.private { background:#8e44ad; }
        .calendar-content { min-width:630px; }
        .calendar-weekdays, .calendar-grid { display:grid; grid-template-columns:repeat(7,minmax(84px,1fr)); gap:6px; }
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
        .places-table table { min-width:1200px; }
        .places-table td:nth-child(5) { min-width:430px; }
        .place-actions { display:flex; gap:6px; }
        .place-actions button { padding:8px 10px; white-space:nowrap; }
        .place-anchor { display:flex; align-items:center; gap:8px; min-width:0; padding:7px; border:1px solid var(--divider-color); border-radius:8px; }
        .place-anchor > div { flex:1; min-width:150px; }
        .place-anchor strong, .place-anchor small { display:block; }
        .place-anchor button { padding:7px 9px; white-space:nowrap; }
        .map-point-status { display:inline-block; flex:none; padding:3px 7px; border-radius:999px; font-size:11px; font-weight:700; white-space:nowrap; }
        .map-point-status.visible { color:var(--success-color,#2e7d32); background:color-mix(in srgb,var(--success-color,#2e7d32) 14%,transparent); }
        .map-point-status.hidden { color:var(--secondary-text-color); background:var(--secondary-background-color); }
        .configured-place-row { background:color-mix(in srgb,var(--primary-color) 5%,transparent); }
        button.danger { background:var(--error-color,#c62828); }
        .radius-summary { display:flex; gap:10px 16px; flex-wrap:wrap; margin:8px 0 16px; color:var(--secondary-text-color); }
        @media (max-width:1050px) {
          .overview-secondary, .diagnostics-grid { grid-template-columns:1fr; }
          main { padding:14px; }
          .card { padding:16px; }
          .trip-table, .places-table { overflow:visible; padding:0; }
          .trip-table table, .places-table table { display:block; width:100%; min-width:0; margin-top:14px; }
          .trip-table thead, .places-table thead { display:none; }
          .trip-table tbody, .places-table tbody { display:grid; gap:12px; }
          .trip-table tr, .places-table tr { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:11px 12px; padding:14px; border:1px solid var(--divider-color); border-radius:13px; background:color-mix(in srgb,var(--secondary-background-color) 58%,var(--card-background-color)); }
          .trip-table td, .places-table td, .places-table td:nth-child(5) { display:grid; gap:5px; width:auto !important; min-width:0 !important; padding:0; border:0; }
          .trip-table td::before, .places-table td::before { color:var(--secondary-text-color); content:attr(data-label); font-size:11px; font-weight:750; letter-spacing:.045em; text-transform:uppercase; }
          .trip-table .wide-field, .trip-table .table-action, .places-table td:nth-child(5), .places-table .table-action { grid-column:1 / -1; }
          .trip-table .decision { min-width:0; max-width:none; }
          .trip-table input, .trip-table select, .places-table input[type="text"], .places-table input[type="number"], .places-table select { min-width:0; }
          .trip-distance { width:100% !important; }
          .place-anchor > div { min-width:0; }
          .place-actions { flex-wrap:wrap; }
          .table-action button { min-height:42px; }
        }
        @media (max-width:600px) {
          dl { grid-template-columns:1fr; gap:3px; } dd { margin-bottom:8px; }
          header { margin-bottom:18px; }
          .grid { grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }
          .card { padding:14px; } .metric { font-size:23px; }
          .question-grid { grid-template-columns:1fr; }
          .last-trip-card { grid-column:1 / -1; }
          .question-actions button { flex-basis:100%; }
          .calendar-weekdays, .calendar-grid { gap:3px; }
          .calendar-day { min-height:75px; padding:5px; border-radius:7px; }
          .calendar-value { padding:2px 3px; font-size:9px; }
          .calendar-scroll { margin:0 -4px; width:calc(100% + 8px); }
          .advanced > summary { padding:15px 16px; }
          .advanced > .advanced-body { padding:15px 16px 17px; }
          .tabs { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); max-width:none; margin-bottom:16px; overflow:visible; }
          .tab { width:100%; min-width:0; }
        }
        @media (max-width:440px) {
          .grid { grid-template-columns:1fr; }
          .trip-table tr, .places-table tr { grid-template-columns:1fr; padding:12px; }
          .trip-table .wide-field, .trip-table .table-action, .places-table td:nth-child(5), .places-table .table-action { grid-column:auto; }
          .section-titlebar h2 { font-size:20px; }
          .tab { flex-basis:112px; padding-inline:11px; }
        }
      </style>
      <main>
        <header><div><h1>Kniha jízd</h1><div class="muted title-subtitle">Jízdy, historie a reporty na jednom místě</div></div>
          <span class="pill ${readyValue ? "ready" : "error"}">${readyValue ? this._statusLabel(statusValue) : "Vstupy nejsou připravené"}</span>
        </header>
        <nav class="tabs" aria-label="Části panelu">
          <button class="tab ${this._activeTab === "overview" ? "active" : ""}" data-tab="overview">Přehled</button>
          <button class="tab ${this._activeTab === "history" ? "active" : ""}" data-tab="history">Historie</button>
          <button class="tab ${this._activeTab === "map" ? "active" : ""}" data-tab="map">Mapa míst</button>
          <button class="tab ${this._activeTab === "places" ? "active" : ""}" data-tab="places">Správa míst</button>
        </nav>
        ${this._message ? `<div class="global-message" role="status" aria-live="polite">${this._text(this._message)}</div>` : ""}
        <div ${this._activeTab === "overview" ? "" : "hidden"}>
        ${this._overviewError ? `<div class="inline-error" role="alert">Živý přehled se nepodařilo načíst: ${this._text(this._overviewError)}</div>` : ""}
        <section class="card daily-trips primary-section">
          <div class="section-titlebar"><div><div class="section-kicker">Hlavní přehled</div><h2>Dnešní jízdy</h2><div class="muted">Jízdy můžete přímo opravit a uložit. Zákazník je u služební jízdy volitelný.</div></div><span class="pill">${this._text(todayTripsValue, "0")} jízd</span></div>
          ${this._tripTable(todayTripRows, "Dnes zatím není zaznamenána žádná jízda.", "overview-trips")}
        </section>
        ${this._questionCards(todayTripRows)}
        ${this._placeQuestionCards(attrs.place_questions || todayTripRows)}
        <section class="grid metrics-grid">
          <article class="card metric-card"><div class="muted">Dnes služební</div><div class="metric">${this._number(businessValue)} km</div></article>
          <article class="card metric-card"><div class="muted">Dnes soukromé</div><div class="metric">${this._number(privateValue)} km</div></article>
          <article class="card metric-card"><div class="muted">Dnešní jízdy</div><div class="metric">${this._text(todayTripsValue, "0")}</div></article>
          <article class="card metric-card attention"><div class="muted">Vyžaduje pozornost</div><div class="metric">${this._text(pendingValue + Number(attrs.today_review_count || 0), "0")}</div><small>${pendingValue} čeká · ${this._text(attrs.today_review_count, "0")} k revizi</small></article>
        </section>
        <section class="overview-secondary">
          <article class="card last-trip-card"><div class="last-trip-heading"><div><div class="muted">Poslední jízda</div><h2>${this._text(last.purpose, last.trip_type === "private" ? "Soukromá" : "Bez klienta")}</h2></div><strong>${this._number(last.distance_km ?? lastTrip?.state)} km</strong></div>
            <div class="last-trip-route"><span>${this._text(last.start_address)}</span><span aria-hidden="true">→</span><span>${this._text(last.end_address)}</span></div>
            <small>${last.journey_role === "return" ? "Služební návrat" : last.trip_type === "private" ? "Soukromá" : last.trip_type === "unclassified" ? "Nevyřešená – k revizi" : "Služební"} · celá cesta ${this._number(last.journey_distance_km ?? last.distance_km)} km / ${this._text(last.journey_segment_count, "1")} segmentů · ${this._time(last.ended_at)}</small>
          </article>
          <section class="card"><h2>Excel report</h2>
          <div class="muted">Oba listy budou obsahovat pouze jízdy z vybraného měsíce.</div>
          <div class="month-control"><label for="month">Měsíc reportu</label><input id="month" type="month" value="${this._text(this._month)}"></div>
          <div class="actions"><button id="export" ${this._exporting ? "disabled" : ""}>${this._exporting ? "Generuji…" : "Vygenerovat a stáhnout Excel"}</button>
          ${downloadUrl ? `<a class="button" href="${downloadUrl}" download="${this._text(downloadFilename)}">Stáhnout poslední export (${this._text(exportData.month)})</a>` : ""}</div>
          <div class="message">${this._text(exportData.generated_at ? `Poslední export: ${exportData.generated_at}, měsíc ${exportData.month}` : "Dosud nebyl vytvořen export.")}</div>
          </section>
        </section>
        <section class="diagnostics-section" aria-labelledby="diagnostics-title">
          <div class="diagnostics-heading"><h2 id="diagnostics-title">Technický stav</h2><div class="muted">Podrobnosti integrace jsou sbalené, aby nepřekážely běžné práci s jízdami.</div></div>
          <div class="diagnostics-grid">
            <details class="card advanced" data-details-key="input-checks" ${this._openDetails.has("input-checks") ? "open" : ""}><summary>Kontrola vstupů</summary><div class="advanced-body">
              ${this._check("Android Auto", attrs.trigger_ok, `${attrs.trigger_entity}: ${attrs.trigger_state}`)}
              ${this._check("GPS telefonu", attrs.gps_ok, gpsDetail)}
              ${this._check("Geokódovaná adresa", attrs.address_ok, attrs.address_entity)}
              ${this._check("Tachometr", attrs.odometer_ok, odometerDetail)}
              ${this._check("Notifikace", attrs.notify_ok, attrs.notify_service)}
            </div></details>
            <details class="card advanced" data-details-key="automation-diagnostics" ${this._openDetails.has("automation-diagnostics") ? "open" : ""}><summary>Automatika a diagnostika</summary><div class="advanced-body"><dl>
              <dt>Stav</dt><dd>${this._statusLabel(statusValue)}</dd>
              <dt>Aktivní segment</dt><dd>${this._text(attrs.active_segment_id)}</dd>
              <dt>Čeká tachometr</dt><dd>${this._text(attrs.closing_count, "0")}</dd>
              <dt>Čeká zařazení</dt><dd>${this._text(attrs.pending_count, "0")}</dd>
              <dt>Čeká na cíl celé jízdy</dt><dd>${this._text(attrs.transient_count, "0")}</dd>
              <dt>Čeká uložení místa</dt><dd>${this._text(attrs.place_prompt_count, "0")}</dd>
              <dt>Návaznost návratu</dt><dd>${this._text(attrs.return_context_hours)} h</dd>
              <dt>Okno návaznosti návštěvy</dt><dd>${this._text(attrs.transient_stop_minutes)} min</dd>
              <dt>Automatická revize</dt><dd>${this._text(attrs.pending_review_hours)} h</dd>
              <dt>Ustálení cíle</dt><dd>${this._text(attrs.location_settle_seconds)} s</dd>
              <dt>Denní kontrola km</dt><dd>${odometerCheck.consistent ? "Sedí" : "Čeká / rozdíl"} · odometer ${this._number(odometerCheck.odometer_delta_km)} km · potvrzené segmenty ${this._number(odometerCheck.assigned_segment_km)} km · čekající ${this._number(odometerCheck.pending_segment_km)} km · rozdíl ${this._number(odometerCheck.difference_km)} km</dd>
              <dt>Domov</dt><dd>${this._text(attrs.home_address)} · ${this._text(attrs.home_latitude)}, ${this._text(attrs.home_longitude)}</dd>
              <dt>Firma</dt><dd>${this._text(attrs.company_address)} · ${this._text(attrs.company_latitude)}, ${this._text(attrs.company_longitude)} → ${this._text(attrs.company_label)}</dd>
              <dt>Poloměry</dt><dd>domov ${this._number(attrs.home_radius_m)} m · firma ${this._number(attrs.company_radius_m)} m · klient ${this._number(attrs.client_radius_m)} m · soukromé ${this._number(attrs.private_radius_m)} m · návaznost ${this._number(attrs.transient_radius_m)} m</dd>
              <dt>Celkem</dt><dd>${this._text(totalTripsValue, "0")} záznamů · ${this._number(totalBusinessValue)} služebních km · ${this._number(totalPrivateValue)} soukromých km</dd>
              <dt>Poslední rozhodnutí</dt><dd>${attrs.last_notification_action ? `${this._text(attrs.last_notification_action.action)} · ${attrs.last_notification_action.channel === "panel" ? "panel" : "telefon"} · ${this._text(attrs.last_notification_action.processed_at)}` : "—"}</dd>
              <dt>Poslední chyba</dt><dd>${this._text(attrs.last_error)}</dd>
            </dl></div></details>
          </div>
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
            ${this._tripTable(historyRows, "Pro vybraný den není zaznamenána žádná jízda.", "history-trips")}
          </section>
        </div>` : ""}
        ${this._activeTab === "map" ? `<section class="card map-card">
          <div class="map-heading"><div><h2>Mapa uložených míst a zón</h2><div class="muted">Mapa zobrazuje jen výslovně uložená místa a celé dnešní trasy; potvrzené mezibody se do ní nepřidávají.</div></div><button id="refresh-map" ${this._mapLoading ? "disabled" : ""}>Aktualizovat</button></div>
          <div class="map-loading"></div>
          <kniha-jizd-map></kniha-jizd-map>
        </section>` : ""}
        ${this._activeTab === "places" ? `<section class="card daily-trips">
          <div class="places-heading"><div><h2>Správa míst</h2><div class="muted">Jsou zde jen místa, která jste výslovně uložili. Každý řádek je samostatný fyzický bod; volbu „Služební i soukromé“ používejte jen pro skutečnou výjimku.</div></div>
            <div class="place-toolbar"><span class="selected-place-count">${this._selectedPlaces.size} vybráno</span><button id="merge-places" ${this._selectedPlaces.size < 2 || this._savingPlace ? "disabled" : ""}>Sloučit GPS duplicity</button><button id="refresh-places" ${this._placesLoading || this._savingPlace ? "disabled" : ""}>Aktualizovat</button></div>
          </div>
          <div class="radius-summary">Aktivní výchozí poloměry: domov ${this._number(this._placesData?.radii?.home)} m · firma ${this._number(this._placesData?.radii?.company)} m · klient ${this._number(this._placesData?.radii?.business)} m · soukromé ${this._number(this._placesData?.radii?.private)} m</div>
          <div class="history-status">${this._placesError ? `Správu míst se nepodařilo načíst: ${this._text(this._placesError)}` : this._placesLoading ? "Načítám místa…" : this._text(this._placesMessage, this._placesData ? `${this._placesData.places?.length || 0} naučených míst obsahuje ${this._placesData.stored_point_count || 0} fyzických bodů. Na mapě je ${this._placesData.map_point_count || 0} bodů včetně konfigurovaného domova a firmy.` : "Místa ještě nejsou načtená.")}</div>
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
    this.shadowRoot.querySelectorAll(".trip-start, .trip-end, .trip-distance, .trip-purpose, .trip-type").forEach((input) => {
      const updateDirtyState = () => {
        this._captureInteractiveState();
        const row = input.closest("tr[data-segment-id]");
        const segmentId = String(row?.dataset?.segmentId || "");
        const draft = this._tripDrafts.get(segmentId);
        const save = row?.querySelector(".save-trip");
        if (save) {
          save.disabled = !draft || Boolean(draft.confirmedAt) || Boolean(this._savingTrip);
          save.textContent = draft?.confirmedAt ? "Uloženo" : "Uložit";
        }
      };
      input.addEventListener("input", updateDirtyState);
      input.addEventListener("change", updateDirtyState);
    });
    this.shadowRoot.querySelectorAll(".resolve-trip").forEach((button) => {
      button.addEventListener("click", () => this._resolveTrip(button));
    });
    this.shadowRoot.querySelectorAll(".use-purpose-suggestion").forEach((button) => {
      button.addEventListener("click", () => {
        const input = button.closest(".question-card")?.querySelector(".question-value");
        if (input) {
          input.value = button.dataset.value || "";
          input.dispatchEvent(new Event("input", { bubbles: true }));
        }
      });
    });
    this.shadowRoot.querySelectorAll(".question-value").forEach((input) => {
      input.addEventListener("input", () => {
        const segmentId = String(input.closest(".question-card")?.dataset?.segmentId || "");
        if (segmentId) this._questionValues.set(segmentId, input.value);
      });
    });
    this.shadowRoot.querySelectorAll(".resolve-place").forEach((button) => {
      button.addEventListener("click", () => this._resolvePlace(button));
    });
    this.shadowRoot.querySelectorAll(".place-question-value").forEach((input) => {
      input.addEventListener("input", () => {
        const segmentId = String(input.closest(".place-question-card")?.dataset?.segmentId || "");
        if (segmentId) this._placeQuestionValues.set(segmentId, input.value);
      });
    });
    this.shadowRoot.querySelectorAll(".use-place-suggestion").forEach((button) => {
      button.addEventListener("click", () => {
        const input = button.closest(".place-question-card")?.querySelector(".place-question-value");
        if (input) {
          input.value = button.dataset.value || "";
          input.dispatchEvent(new Event("input", { bubbles: true }));
        }
      });
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
    this.shadowRoot.querySelectorAll("details[data-details-key]").forEach((details) => {
      details.addEventListener("toggle", () => {
        const detailsKey = String(details.dataset.detailsKey || "");
        if (!detailsKey) return;
        if (details.open) this._openDetails.add(detailsKey);
        else this._openDetails.delete(detailsKey);
      });
    });
    this.shadowRoot.querySelectorAll("[data-scroll-key]").forEach((surface) => {
      const scrollKey = surface.dataset.scrollKey;
      surface.scrollLeft = Math.min(
        this._scrollPositions.get(scrollKey) || 0,
        Math.max(0, surface.scrollWidth - surface.clientWidth),
      );
      surface.addEventListener("scroll", () => {
        this._scrollPositions.set(scrollKey, surface.scrollLeft);
        this._interactionUntil = Date.now() + 450;
        if (this._renderPending) this._requestRender();
      }, { passive: true });
    });
    this._syncMapElement();
  }
}

if (!customElements.get("kniha-jizd-panel")) {
  customElements.define("kniha-jizd-panel", KnihaJizdPanel);
}
