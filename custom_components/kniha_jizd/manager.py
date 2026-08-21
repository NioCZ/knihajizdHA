"""Trip lifecycle manager for Kniha jízd."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hmac
import logging
from pathlib import Path
import re
import secrets
from typing import Any
from uuid import uuid4

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_DOMAIN,
    ATTR_SERVICE,
    EVENT_SERVICE_REGISTERED,
    EVENT_SERVICE_REMOVED,
)
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .address_rules import configured_place_match
from .const import (
    ACTION_CONFIRM,
    ACTION_NEW,
    ACTION_PREFIX,
    ACTION_PRIVATE,
    ACTION_RETURN,
    CONF_ADDRESS_ENTITY,
    CONF_COMPANY_ADDRESS,
    CONF_COMPANY_LATITUDE,
    CONF_COMPANY_LABEL,
    CONF_COMPANY_LONGITUDE,
    CONF_GPS_ENTITY,
    CONF_HOME_ADDRESS,
    CONF_HOME_LATITUDE,
    CONF_HOME_LONGITUDE,
    CONF_INSTITUTION_SEARCH_RADIUS,
    CONF_NOTIFY_SERVICE,
    CONF_ODOMETER_ENTITY,
    CONF_PLACE_RADIUS,
    CONF_RETURN_CONTEXT_HOURS,
    CONF_TRANSIENT_STOP_MINUTES,
    CONF_TRIGGER_ENTITY,
    CONF_WAIT_TIMEOUT,
    DEFAULT_TRANSIENT_STOP_RADIUS,
    DOMAIN,
    EVENT_NOTIFICATION_ACTION,
    LEARNED_TRANSIENT_RADIUS,
    PLACE_ROLE_CLIENT,
    PLACE_ROLE_RETURN,
    PLACE_ROLE_TRANSIENT,
    RUNTIME_STORE_VERSION,
    TRIP_TYPE_BUSINESS,
    TRIP_TYPE_CONTEXTUAL,
    TRIP_TYPE_PRIVATE,
    UNAVAILABLE_STATES,
)
from .geocoding import NominatimGeocoder
from .journey_chain import (
    apply_journey_classification,
    continuation_details,
    detect_transient_stop,
)
from .nearby_search import NearbyInstitutionSearcher
from .odometer_logic import odometer_update_signal
from .storage import KnihaJizdRepository
from .trip_context import infer_return_context

_LOGGER = logging.getLogger(__name__)

_ACTION_PATTERN = re.compile(
    rf"^{ACTION_PREFIX}_({ACTION_CONFIRM}|{ACTION_NEW}|{ACTION_PRIVATE}|"
    rf"{ACTION_RETURN})_([0-9a-f]+)$"
)


class KnihaJizdManager:
    """Track Android Auto trip segments and classify completed trips."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        config: dict[str, Any],
        repository: KnihaJizdRepository,
        geocoder: NominatimGeocoder,
        institution_searcher: NearbyInstitutionSearcher,
    ) -> None:
        """Initialize the manager."""
        self.hass = hass
        self.entry = entry
        self.repository = repository
        self.geocoder = geocoder
        self.institution_searcher = institution_searcher

        self.trigger_entity = str(config[CONF_TRIGGER_ENTITY])
        self.gps_entity = str(config[CONF_GPS_ENTITY])
        self.address_entity = str(config[CONF_ADDRESS_ENTITY])
        self.odometer_entity = str(config[CONF_ODOMETER_ENTITY])
        self.notify_service = _normalize_notify_service(
            str(config[CONF_NOTIFY_SERVICE])
        )
        self.wait_timeout = float(config[CONF_WAIT_TIMEOUT])
        self.return_context_hours = float(config[CONF_RETURN_CONTEXT_HOURS])
        self.transient_stop_minutes = float(config[CONF_TRANSIENT_STOP_MINUTES])
        self.home_address = str(config.get(CONF_HOME_ADDRESS, "")).strip()
        self.home_latitude = _as_float(config.get(CONF_HOME_LATITUDE))
        self.home_longitude = _as_float(config.get(CONF_HOME_LONGITUDE))
        self.company_address = str(config.get(CONF_COMPANY_ADDRESS, "")).strip()
        self.company_latitude = _as_float(config.get(CONF_COMPANY_LATITUDE))
        self.company_longitude = _as_float(config.get(CONF_COMPANY_LONGITUDE))
        self.company_label = str(config.get(CONF_COMPANY_LABEL, "")).strip()
        self.place_radius = float(config[CONF_PLACE_RADIUS])
        self.institution_search_radius = float(
            config[CONF_INSTITUTION_SEARCH_RADIUS]
        )

        self._active: dict[str, Any] | None = None
        self._closing: dict[str, dict[str, Any]] = {}
        self._pending: dict[str, dict[str, Any]] = {}
        self._transient: dict[str, dict[str, Any]] = {}
        self._statistics: dict[str, Any] = {
            "segments_total": 0,
            "business_km_total": 0.0,
            "private_km_total": 0.0,
            "today_segments": 0,
            "today_business_km": 0.0,
            "today_private_km": 0.0,
            "today_rows": [],
            "last_segment": None,
        }
        self._statistics_date: str | None = None
        self._last_error: str | None = None
        self._export: dict[str, Any] = {
            "state": "never",
            "month": None,
            "filename": None,
            "path": None,
            "download_url": None,
            "generated_at": None,
            "expires_at": None,
            "error": None,
        }
        self._download_token: str | None = None
        self._download_token_expires_at: datetime | None = None
        self._listeners: set[Callable[[], None]] = set()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._unsubscribers: list[Callable[[], None]] = []
        self._transition_lock = asyncio.Lock()
        self._resolution_lock = asyncio.Lock()
        self._journey_lock = asyncio.Lock()
        self._finalization_lock = asyncio.Lock()
        self._runtime_lock = asyncio.Lock()
        self._stopping = False
        self._runtime_store: Store[dict[str, Any]] = Store(
            hass,
            RUNTIME_STORE_VERSION,
            f"{DOMAIN}.{entry.entry_id}.runtime",
        )

    async def async_start(self) -> None:
        """Restore runtime state and subscribe to HA events."""
        runtime = await self._runtime_store.async_load() or {}
        active = runtime.get("active")
        closing = runtime.get("closing")
        pending = runtime.get("pending")
        transient = runtime.get("transient")
        self._active = active if isinstance(active, dict) else None
        self._closing = closing if isinstance(closing, dict) else {}
        self._pending = pending if isinstance(pending, dict) else {}
        self._transient = transient if isinstance(transient, dict) else {}
        for segment in [
            *self._closing.values(),
            *self._pending.values(),
            *self._transient.values(),
        ]:
            self._restore_processing_flags(segment)
        await self._async_refresh_statistics()

        self._unsubscribers.append(
            async_track_state_change_event(
                self.hass, [self.trigger_entity], self._handle_trigger_event
            )
        )
        self._unsubscribers.append(
            self.hass.bus.async_listen(
                EVENT_NOTIFICATION_ACTION, self._handle_notification_action
            )
        )
        self._unsubscribers.extend(
            (
                self.hass.bus.async_listen(
                    EVENT_SERVICE_REGISTERED, self._handle_service_registry_event
                ),
                self.hass.bus.async_listen(
                    EVENT_SERVICE_REMOVED, self._handle_service_registry_event
                ),
            )
        )
        self._unsubscribers.append(
            async_track_state_change_event(
                self.hass,
                [self.gps_entity, self.address_entity, self.odometer_entity],
                self._handle_input_update,
            )
        )

        for segment in list(self._closing.values()):
            self._create_task(
                self._async_finish_segment_safe(segment),
                f"{DOMAIN}_resume_closing_{segment.get('id', 'unknown')}",
            )

        for segment in list(self._pending.values()):
            if not segment.get("odometer_ready"):
                self._create_task(
                    self._async_complete_odometer(segment),
                    f"{DOMAIN}_restore_pending_odometer_{segment.get('id', 'unknown')}",
                )
            if not segment.get("classification_ready"):
                self._create_task(
                    self._async_send_classification_notification(segment),
                    f"{DOMAIN}_restore_notification_{segment.get('id', 'unknown')}",
                )

        for segment in list(self._transient.values()):
            if not segment.get("odometer_ready"):
                self._create_task(
                    self._async_complete_odometer(segment),
                    f"{DOMAIN}_restore_transient_odometer_{segment.get('id', 'unknown')}",
                )
            if not segment.get("continued_by_segment_id"):
                self._create_task(
                    self._async_expire_transient_segment(str(segment.get("id"))),
                    f"{DOMAIN}_restore_transient_{segment.get('id', 'unknown')}",
                )

        trigger_state = self.hass.states.get(self.trigger_entity)
        if trigger_state is not None and trigger_state.state == "on":
            if self._active is None:
                await self._async_start_segment(trigger_state.last_changed)
        elif (
            trigger_state is not None
            and trigger_state.state == "off"
            and self._active is not None
        ):
            disconnected_at = (
                trigger_state.last_changed
                if trigger_state is not None
                else datetime.now(UTC)
            )
            await self._async_begin_end_segment(disconnected_at)
        self._notify_listeners()

    @staticmethod
    def _restore_processing_flags(segment: dict[str, Any]) -> None:
        """Infer new runtime flags for segments saved by an older release."""
        if "odometer_ready" not in segment:
            segment["odometer_ready"] = (
                segment.get("end_odometer_km") is not None
                and segment.get("odometer_wait_timed_out") is not None
            )
        segment.setdefault("classification_prepared", False)
        segment.setdefault("classification_ready", bool(segment.get("trip_type")))
        segment.setdefault("persisted", False)

    async def async_shutdown(self) -> None:
        """Unsubscribe and cancel in-flight work, keeping state for reload."""
        self._stopping = True
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()

        await self._async_save_runtime()
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._listeners.clear()

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Subscribe an entity to live manager updates."""
        self._listeners.add(listener)

        @callback
        def _unsubscribe() -> None:
            self._listeners.discard(listener)

        return _unsubscribe

    @callback
    def _notify_listeners(self) -> None:
        """Notify all diagnostic entities about a state change."""
        for listener in list(self._listeners):
            listener()

    @property
    def status(self) -> str:
        """Return the most important current workflow state."""
        if self._last_error:
            return "error"
        if self._active is not None:
            return "driving"
        if self._closing:
            return "waiting_odometer"
        if self._pending:
            return "waiting_classification"
        if self._transient:
            return "waiting_journey"
        return "idle"

    @property
    def statistics(self) -> dict[str, Any]:
        """Return a safe snapshot of raw-log statistics."""
        return deepcopy(self._statistics)

    @property
    def export_status(self) -> dict[str, Any]:
        """Return the latest Excel export state."""
        return self._export.copy()

    @property
    def diagnostics(self) -> dict[str, Any]:
        """Return current inputs and workflow counters for HA entities."""
        trigger_state = self.hass.states.get(self.trigger_entity)
        gps_state = self.hass.states.get(self.gps_entity)
        address_state = self.hass.states.get(self.address_entity)
        odometer_state = self.hass.states.get(self.odometer_entity)
        latitude = _coordinate(gps_state, "latitude")
        longitude = _coordinate(gps_state, "longitude")
        odometer = _odometer_value(odometer_state)
        trigger_ok = (
            trigger_state is not None and trigger_state.state in {"on", "off"}
        )
        gps_ok = latitude is not None and longitude is not None
        odometer_ok = odometer is not None
        notify_ok = self.hass.services.has_service("notify", self.notify_service)
        address = (
            address_state.state
            if address_state is not None
            and address_state.state.casefold() not in UNAVAILABLE_STATES
            else None
        )
        return {
            "kniha_jizd_kind": "status",
            "ready": trigger_ok and gps_ok and odometer_ok and notify_ok,
            "status": self.status,
            "active_segment_id": (
                self._active.get("id") if self._active is not None else None
            ),
            "active_started_at": (
                self._active.get("started_at") if self._active is not None else None
            ),
            "closing_count": len(self._closing),
            "pending_count": len(self._pending),
            "transient_count": len(self._transient),
            "return_context_hours": self.return_context_hours,
            "transient_stop_minutes": self.transient_stop_minutes,
            "home_address": self.home_address or None,
            "home_latitude": self.home_latitude,
            "home_longitude": self.home_longitude,
            "company_address": self.company_address or None,
            "company_latitude": self.company_latitude,
            "company_longitude": self.company_longitude,
            "company_label": self.company_label or None,
            "today_trips": self._today_trip_rows(),
            "trigger_entity": self.trigger_entity,
            "trigger_state": trigger_state.state if trigger_state else None,
            "trigger_ok": trigger_ok,
            "gps_entity": self.gps_entity,
            "gps_ok": gps_ok,
            "latitude": latitude,
            "longitude": longitude,
            "address_entity": self.address_entity,
            "address": address,
            "address_ok": address is not None,
            "odometer_entity": self.odometer_entity,
            "odometer_km": odometer,
            "odometer_ok": odometer_ok,
            "odometer_updated_at": _iso_utc(_odometer_updated_at(odometer_state)),
            "notify_service": f"notify.{self.notify_service}",
            "notify_ok": notify_ok,
            "last_error": self._last_error,
        }

    @callback
    def set_export_running(self, month: str) -> None:
        """Expose a running Excel export to entities and the panel."""
        self._download_token = None
        self._download_token_expires_at = None
        self._export.update(
            {
                "state": "generating",
                "month": month,
                "filename": f"kniha_jizd_{month}.xlsx",
                "download_url": None,
                "expires_at": None,
                "error": None,
            }
        )
        self._notify_listeners()

    @callback
    def set_export_success(self, path: Path, month: str) -> None:
        """Expose a finished export and create a temporary download link."""
        self._download_token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        self._download_token_expires_at = now + timedelta(minutes=15)
        self._export.update(
            {
                "state": "ready",
                "month": month,
                "filename": f"kniha_jizd_{month}.xlsx",
                "path": str(path),
                "download_url": (
                    f"/api/{DOMAIN}/download/{self._download_token}"
                ),
                "generated_at": now.isoformat(),
                "expires_at": self._download_token_expires_at.isoformat(),
                "error": None,
            }
        )
        self._notify_listeners()

    @callback
    def set_export_error(self, error: str) -> None:
        """Expose an Excel export failure."""
        self._export.update(
            {
                "state": "error",
                "download_url": None,
                "expires_at": None,
                "error": error,
            }
        )
        self._notify_listeners()

    def validate_download_token(self, token: str) -> Path | None:
        """Return the current export path for a valid temporary token."""
        expires_at = self._download_token_expires_at
        path = self._export.get("path")
        if (
            not self._download_token
            or expires_at is None
            or expires_at <= datetime.now(UTC)
            or not isinstance(path, str)
            or not hmac.compare_digest(token, self._download_token)
        ):
            return None
        return Path(path)

    async def _async_refresh_statistics(self) -> None:
        """Refresh persisted totals without blocking the event loop."""
        local_date = dt_util.now().date().isoformat()
        self._statistics = await self.repository.async_get_statistics(local_date)
        self._statistics_date = local_date
        self._notify_listeners()

    def _today_trip_rows(self) -> list[dict[str, Any]]:
        """Return compact persisted and unfinished rows for the sidebar panel."""
        rows: dict[str, dict[str, Any]] = {}
        persisted = self._statistics.get("today_rows")
        if isinstance(persisted, list):
            for segment in persisted:
                if isinstance(segment, dict) and segment.get("id"):
                    rows[str(segment["id"])] = _panel_trip_row(segment, "saved")

        runtime_groups: tuple[tuple[str, list[dict[str, Any]]], ...] = (
            ("driving", [self._active] if self._active is not None else []),
            ("waiting_odometer", list(self._closing.values())),
            ("waiting_classification", list(self._pending.values())),
            ("waiting_journey", list(self._transient.values())),
        )
        local_date = dt_util.now().date().isoformat()
        for status, segments in runtime_groups:
            for segment in segments:
                if str(segment.get("date")) != local_date or not segment.get("id"):
                    continue
                effective_status = status
                if status == "waiting_odometer" and not segment.get(
                    "classification_prepared"
                ):
                    effective_status = "processing_destination"
                rows[str(segment["id"])] = _panel_trip_row(
                    segment, effective_status
                )
        return sorted(rows.values(), key=lambda row: str(row.get("started_at") or ""))

    async def async_update_trip(
        self, segment_id: str, purpose: str, trip_type: str
    ) -> dict[str, Any]:
        """Correct a persisted or unfinished trip from the sidebar panel."""
        if trip_type not in {TRIP_TYPE_BUSINESS, TRIP_TYPE_PRIVATE}:
            raise ValueError("trip_type must be business or private")
        selected_purpose = purpose.strip()
        if trip_type == TRIP_TYPE_PRIVATE:
            selected_purpose = "Soukromá"

        runtime = self._find_runtime_segment(segment_id)
        if runtime is not None:
            if runtime is self._active or not runtime.get("ended_at"):
                raise ValueError("an active trip cannot be edited before it ends")
            if runtime.get("journey_role") == "transient_stop" and runtime.get(
                "continued_by_segment_id"
            ):
                destination = self._find_journey_destination(runtime.get("journey_id"))
                if destination is not None:
                    runtime = destination
            runtime_id = str(runtime["id"])
            if runtime.get("journey_role") == "transient_stop":
                self._transient.pop(runtime_id, None)
                runtime["journey_role"] = "destination"
                stop = runtime.get("transient_stop")
                if isinstance(stop, dict):
                    stop["manually_resolved"] = True
            await self._async_finalize_segment(
                runtime,
                purpose=selected_purpose,
                trip_type=trip_type,
                source="manual_panel",
                learn_place=bool(selected_purpose),
                learned_label=selected_purpose or None,
                place_role=PLACE_ROLE_CLIENT if selected_purpose else None,
            )
            return {
                "updated": 1,
                "state": (
                    "saved" if runtime.get("persisted") else "waiting_odometer"
                ),
            }

        changed = await self.repository.async_update_trip(
            segment_id, selected_purpose, trip_type
        )
        if not changed:
            raise ValueError("trip segment was not found")
        await self._async_refresh_statistics()
        return {"updated": changed, "state": "saved"}

    def _find_runtime_segment(self, segment_id: str) -> dict[str, Any] | None:
        """Find one unfinished segment without copying its live state."""
        if self._active is not None and self._active.get("id") == segment_id:
            return self._active
        for collection in (self._closing, self._pending, self._transient):
            if segment_id in collection:
                return collection[segment_id]
        return None

    def _find_journey_destination(
        self, journey_id: Any
    ) -> dict[str, Any] | None:
        """Find the editable destination following a transient segment."""
        if not journey_id:
            return None
        for collection in (self._pending, self._closing):
            for segment in collection.values():
                if (
                    segment.get("journey_id") == journey_id
                    and segment.get("journey_role") != "transient_stop"
                ):
                    return segment
        return None

    @callback
    def _handle_trigger_event(self, event: Event) -> None:
        """Schedule processing for an Android Auto state transition."""
        old_state: State | None = event.data.get("old_state")
        new_state: State | None = event.data.get("new_state")
        old_value = old_state.state if old_state is not None else None
        new_value = new_state.state if new_state is not None else None
        if old_value != "on" and new_value == "on":
            self._create_task(
                self._async_start_segment(event.time_fired),
                f"{DOMAIN}_start_segment",
            )
        elif new_value == "off" and (
            old_value == "on" or self._active is not None
        ):
            self._create_task(
                self._async_begin_end_segment(event.time_fired),
                f"{DOMAIN}_end_segment",
            )
        self._notify_listeners()

    @callback
    def _handle_input_update(self, event: Event) -> None:
        """Refresh diagnostic entities when a configured input changes."""
        if self._statistics_date != dt_util.now().date().isoformat():
            self._create_task(
                self._async_refresh_statistics(),
                f"{DOMAIN}_refresh_daily_statistics",
            )
        self._notify_listeners()

    @callback
    def _handle_notification_action(self, event: Event) -> None:
        """Schedule processing for a matching actionable notification event."""
        action = event.data.get("action")
        if not isinstance(action, str) or not _ACTION_PATTERN.match(action):
            return
        self._create_task(
            self._async_process_notification_action(event.data),
            f"{DOMAIN}_notification_action",
        )

    @callback
    def _handle_service_registry_event(self, event: Event) -> None:
        """Refresh readiness when the configured phone service changes."""
        if (
            event.data.get(ATTR_DOMAIN) == "notify"
            and event.data.get(ATTR_SERVICE) == self.notify_service
        ):
            self._notify_listeners()

    def _create_task(self, coro: Coroutine[Any, Any, Any], name: str) -> None:
        """Create a tracked Home Assistant task."""
        if self._stopping:
            coro.close()
            return
        task = self.hass.async_create_task(coro, name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _async_start_segment(self, started_at: datetime) -> None:
        """Capture trip start values on Android Auto off -> on."""
        async with self._transition_lock:
            if self._active is not None:
                _LOGGER.warning(
                    "Ignoring trip start because segment %s is already active",
                    self._active.get("id"),
                )
                return

            odometer_state = self.hass.states.get(self.odometer_entity)
            start_odometer = _odometer_value(odometer_state)
            odometer_updated_at = _odometer_updated_at(odometer_state)
            location = self._capture_location()
            local_started = dt_util.as_local(started_at)
            self._active = {
                "id": uuid4().hex,
                "journey_id": uuid4().hex,
                "journey_segment_count": None,
                "journey_distance_km": None,
                "journey_distance_complete": None,
                "date": local_started.date().isoformat(),
                "started_at": _iso_utc(started_at),
                "ended_at": None,
                "odometer_updated_at": None,
                "odometer_wait_timed_out": None,
                "odometer_ready": False,
                "odometer_completion_source": None,
                "start_odometer_km": start_odometer,
                "end_odometer_km": None,
                "distance_km": None,
                "start_odometer_updated_at": _iso_utc(odometer_updated_at),
                "start_odometer_source": "sensor_at_android_auto_connect",
                "start_address": location["address"],
                "end_address": None,
                "start_latitude": location["latitude"],
                "start_longitude": location["longitude"],
                "end_latitude": None,
                "end_longitude": None,
                "purpose": None,
                "trip_type": None,
                "classification_source": None,
                "classification_prepared": False,
                "classification_ready": False,
                "persisted": False,
                "journey_role": None,
                "journey_inherited_from_segment_id": None,
                "transient_stop": None,
                "transient_continuation": None,
                "return_of_segment_id": None,
                "return_context": None,
                "matched_place_id": None,
                "return_destination_label": None,
                "map_estimate": None,
                "map_address": None,
                "map_attribution": None,
                "map_candidates": [],
                "candidate_search_radius_m": None,
                "selected_map_candidate": None,
                "configured_place": None,
                "configured_place_match": None,
                "validation_error": (
                    None if start_odometer is not None else "missing_start_odometer"
                ),
            }
            self._link_transient_continuation(self._active)
            await self._async_save_runtime()
            _LOGGER.info("Trip segment %s started", self._active["id"])

    async def _async_begin_end_segment(self, disconnected_at: datetime) -> None:
        """Capture destination immediately and begin asynchronous odometer wait."""
        async with self._transition_lock:
            if self._active is None:
                _LOGGER.warning("Ignoring trip end because no segment is active")
                return

            segment = self._active
            self._active = None
            location = self._capture_location()
            segment["ended_at"] = _iso_utc(disconnected_at)
            segment["end_address"] = location["address"]
            segment["end_latitude"] = location["latitude"]
            segment["end_longitude"] = location["longitude"]
            self._closing[segment["id"]] = segment
            await self._async_save_runtime()

        self._create_task(
            self._async_finish_segment_safe(segment),
            f"{DOMAIN}_finish_segment_{segment['id']}",
        )

    async def _async_finish_segment_safe(self, segment: dict[str, Any]) -> None:
        """Finish one closing segment but leave it recoverable after a failure."""
        try:
            await self._async_finish_segment(segment)
        except asyncio.CancelledError:
            raise
        except Exception as err:  # noqa: BLE001 - preserve for restart recovery
            self._last_error = f"{type(err).__name__}: {err}"
            self._notify_listeners()
            _LOGGER.exception("Failed to finish trip segment %s", segment.get("id"))

    async def _async_finish_segment(self, segment: dict[str, Any]) -> None:
        """Run odometer completion and destination classification concurrently."""
        tasks: list[Coroutine[Any, Any, Any]] = []
        if not segment.get("odometer_ready"):
            tasks.append(self._async_complete_odometer(segment))
        if not segment.get("classification_prepared"):
            tasks.append(self._async_prepare_classification(segment))
        if tasks:
            await asyncio.gather(*tasks)
        await self._async_try_finalize_segment(segment)

    async def _async_complete_odometer(self, segment: dict[str, Any]) -> None:
        """Wait for the primary cloud signal, with timeout only as fallback."""
        disconnected_at = _parse_datetime(segment.get("ended_at")) or datetime.now(UTC)
        start_odometer = _as_float(segment.get("start_odometer_km"))
        end_odometer, odometer_updated_at, timed_out, completion_source = (
            await self._async_wait_for_odometer(disconnected_at, start_odometer)
        )
        segment["odometer_updated_at"] = _iso_utc(odometer_updated_at)
        segment["odometer_wait_timed_out"] = timed_out
        segment["odometer_completion_source"] = completion_source
        segment["end_odometer_km"] = end_odometer

        if start_odometer is None or end_odometer is None:
            segment["distance_km"] = None
            segment["validation_error"] = (
                "missing_start_odometer"
                if start_odometer is None
                else "missing_end_odometer"
            )
        elif end_odometer + 0.001 < start_odometer:
            segment["distance_km"] = None
            segment["validation_error"] = "odometer_decreased"
        else:
            segment["distance_km"] = round(max(0.0, end_odometer - start_odometer), 3)
            segment["validation_error"] = None

        segment["odometer_ready"] = True
        await self._async_apply_previous_final_to_new_active(segment)
        await self._async_save_runtime()
        await self._async_try_finalize_segment(segment)
        await self._async_try_finalize_journey_destination(segment.get("journey_id"))

    async def _async_prepare_classification(
        self, segment: dict[str, Any]
    ) -> None:
        """Resolve the destination immediately, independently of the odometer."""
        if _address_is_coordinate_fallback(segment.get("start_address")):
            learned_start = await self.repository.async_find_place(
                _as_float(segment.get("start_latitude")),
                _as_float(segment.get("start_longitude")),
                _as_text(segment.get("start_address")),
                self.place_radius,
            )
            if learned_start is not None and learned_start.get("matched_address"):
                segment["start_address"] = learned_start["matched_address"]
            else:
                start_map_result = await self.geocoder.async_reverse(
                    _as_float(segment.get("start_latitude")),
                    _as_float(segment.get("start_longitude")),
                )
                if start_map_result is not None and start_map_result.get("display_name"):
                    segment["start_address"] = start_map_result["display_name"]

        segment["return_context"] = (
            infer_return_context(
                segment,
                self._statistics.get("last_segment"),
                self.return_context_hours,
                self.place_radius,
            )
            or segment.get("return_context")
            or self._journey_return_context(segment)
        )
        if await self._async_handle_configured_destination(segment):
            return
        learned_place = await self.repository.async_find_place(
            _as_float(segment.get("end_latitude")),
            _as_float(segment.get("end_longitude")),
            _as_text(segment.get("end_address")),
            self.place_radius,
        )
        if learned_place is not None:
            segment["map_estimate"] = learned_place.get("map_name") or learned_place.get(
                "label"
            )
            segment["matched_place_id"] = learned_place.get("id")
            segment["return_destination_label"] = learned_place.get("label")
            if _address_is_coordinate_fallback(
                segment.get("end_address")
            ) and learned_place.get("matched_address"):
                segment["end_address"] = learned_place["matched_address"]
            place_role = learned_place.get("place_role")
            if place_role == PLACE_ROLE_TRANSIENT:
                segment["transient_stop"] = {
                    "detected": True,
                    "kind": "learned",
                    "name": str(learned_place.get("label") or "Mezizastávka"),
                    "detection_source": "learned_place",
                }
                await self._async_hold_transient(segment)
                return
            if place_role == PLACE_ROLE_RETURN and segment.get("return_context"):
                await self._async_finalize_return(
                    segment,
                    source="learned_return_context",
                    learn_place=False,
                )
                return
            if (
                place_role == PLACE_ROLE_RETURN
                or (
                    segment.get("return_context")
                    and place_role != PLACE_ROLE_CLIENT
                )
            ):
                if not segment.get("return_context"):
                    segment["return_context"] = {
                        "suggested": True,
                        "reason": "learned_return_destination",
                        "previous_segment_id": None,
                        "previous_purpose": None,
                    }
                await self._async_queue_pending(segment)
                return
            learned_trip_type = (
                TRIP_TYPE_PRIVATE
                if learned_place.get("trip_type") == TRIP_TYPE_PRIVATE
                else TRIP_TYPE_BUSINESS
            )
            await self._async_finalize_segment(
                segment,
                purpose=str(learned_place.get("label") or "Neznámý zákazník"),
                trip_type=learned_trip_type,
                source="learned_place",
                learn_place=False,
            )
            return

        end_latitude = _as_float(segment.get("end_latitude"))
        end_longitude = _as_float(segment.get("end_longitude"))
        candidates, map_result = await asyncio.gather(
            self.institution_searcher.async_search(
                end_latitude,
                end_longitude,
                self.institution_search_radius,
            ),
            self.geocoder.async_reverse(end_latitude, end_longitude),
        )
        segment["map_candidates"] = candidates
        segment["candidate_search_radius_m"] = self.institution_search_radius
        if candidates:
            segment["map_estimate"] = candidates[0]["name"]
            segment["map_attribution"] = "© OpenStreetMap contributors, ODbL"
        if map_result is not None:
            if not segment.get("map_estimate"):
                segment["map_estimate"] = map_result.get("name")
            segment["map_address"] = map_result.get("display_name")
            segment["map_attribution"] = (
                segment.get("map_attribution") or map_result.get("attribution")
            )
            if _address_is_coordinate_fallback(segment.get("end_address")) and map_result.get(
                "display_name"
            ):
                segment["end_address"] = map_result["display_name"]

        if await self._async_handle_configured_destination(segment):
            return

        transient_stop = detect_transient_stop(map_result, candidates)
        if transient_stop is not None:
            segment["transient_stop"] = transient_stop
            segment["map_estimate"] = transient_stop["name"]
            await self._async_hold_transient(segment)
            return

        if not segment.get("map_estimate"):
            segment["map_estimate"] = (
                segment.get("map_address")
                or segment.get("end_address")
                or "Neznámý cíl"
            )

        await self._async_queue_pending(segment)

    async def _async_handle_configured_destination(
        self, segment: dict[str, Any]
    ) -> bool:
        """Apply the configured home/company rules to a completed destination."""
        addresses = (segment.get("end_address"), segment.get("map_address"))
        home_match = configured_place_match(
            segment.get("end_latitude"),
            segment.get("end_longitude"),
            addresses,
            self.home_address,
            self.home_latitude,
            self.home_longitude,
            self.place_radius,
        )
        if home_match is not None:
            segment["configured_place"] = "home"
            segment["configured_place_match"] = home_match
            segment["map_estimate"] = "Domov"
            segment["return_destination_label"] = "Domov"
            if segment.get("return_context"):
                await self._async_finalize_return(
                    segment,
                    source="configured_home_return",
                    learn_place=True,
                )
                return True
            segment["return_context"] = {
                "suggested": True,
                "reason": "configured_home_destination",
                "previous_segment_id": None,
                "previous_purpose": None,
            }
            await self._async_queue_pending(segment)
            return True

        company_match = configured_place_match(
            segment.get("end_latitude"),
            segment.get("end_longitude"),
            addresses,
            self.company_address,
            self.company_latitude,
            self.company_longitude,
            self.place_radius,
        )
        if company_match is not None:
            label = self.company_label or "Firma"
            segment["configured_place"] = "company"
            segment["configured_place_match"] = company_match
            segment["map_estimate"] = label
            await self._async_finalize_segment(
                segment,
                purpose=label,
                trip_type=TRIP_TYPE_BUSINESS,
                source="configured_company",
                learn_place=True,
                learned_label=label,
                place_role=PLACE_ROLE_CLIENT,
            )
            return True
        return False

    async def _async_queue_pending(self, segment: dict[str, Any]) -> None:
        """Move a completed segment to the notification decision queue."""
        segment_id = str(segment["id"])
        segment["classification_prepared"] = True
        self._closing.pop(segment_id, None)
        self._transient.pop(segment_id, None)
        self._pending[segment_id] = segment
        await self._async_save_runtime()
        await self._async_send_classification_notification(segment)

    async def _async_hold_transient(self, segment: dict[str, Any]) -> None:
        """Hold a likely intermediate stop until the whole journey is known."""
        segment_id = str(segment["id"])
        segment["journey_id"] = segment.get("journey_id") or uuid4().hex
        segment["journey_role"] = "transient_stop"
        segment["classification_prepared"] = True
        self._closing.pop(segment_id, None)
        self._pending.pop(segment_id, None)
        self._transient[segment_id] = segment

        continuation = self._find_runtime_continuation(segment)
        if continuation is not None:
            self._link_transient_continuation(continuation, segment)
        await self._async_save_runtime()

        if not segment.get("continued_by_segment_id"):
            self._create_task(
                self._async_expire_transient_segment(segment_id),
                f"{DOMAIN}_expire_transient_{segment_id}",
            )
        _LOGGER.info(
            "Trip segment %s held as a possible intermediate stop", segment_id
        )

    async def _async_expire_transient_segment(self, segment_id: str) -> None:
        """Ask for classification if no continuing leg starts in time."""
        while True:
            segment = self._transient.get(segment_id)
            if segment is None or segment.get("continued_by_segment_id"):
                return
            ended_at = _parse_datetime(segment.get("ended_at"))
            if ended_at is None:
                remaining = 0.0
            else:
                deadline = ended_at + timedelta(minutes=self.transient_stop_minutes)
                remaining = (deadline - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                break
            await asyncio.sleep(min(remaining, 60.0))

        segment = self._transient.get(segment_id)
        if segment is None or segment.get("continued_by_segment_id"):
            return
        stop = segment.get("transient_stop")
        if isinstance(stop, dict):
            stop["expired"] = True
            stop["max_duration_minutes"] = self.transient_stop_minutes
        segment["journey_role"] = "destination"
        await self._async_queue_pending(segment)

    def _find_runtime_continuation(
        self, transient: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Find a segment that started while transient analysis was running."""
        candidates: list[dict[str, Any]] = []
        if self._active is not None:
            candidates.append(self._active)
        candidates.extend(self._closing.values())
        candidates.extend(self._pending.values())
        candidates.sort(key=lambda item: str(item.get("started_at") or ""))
        for current in candidates:
            if current.get("id") == transient.get("id"):
                continue
            if self._continuation_details(transient, current) is not None:
                return current
        return None

    def _link_transient_continuation(
        self,
        current: dict[str, Any],
        preferred: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Attach a new segment to the latest compatible intermediate stop."""
        candidates = [preferred] if preferred is not None else list(
            self._transient.values()
        )
        candidates = [
            item
            for item in candidates
            if isinstance(item, dict) and not item.get("continued_by_segment_id")
        ]
        candidates.sort(
            key=lambda item: str(item.get("ended_at") or ""), reverse=True
        )
        for transient in candidates:
            details = self._continuation_details(transient, current)
            if details is None:
                continue
            current["journey_id"] = transient.get("journey_id") or uuid4().hex
            current["transient_continuation"] = details
            current["return_context"] = (
                current.get("return_context") or transient.get("return_context")
            )
            transient["continued_by_segment_id"] = current.get("id")
            transient["continuation"] = details
            return transient
        return None

    def _continuation_details(
        self, transient: dict[str, Any], current: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Apply configured limits to a possible journey continuation."""
        return continuation_details(
            transient,
            current,
            self.transient_stop_minutes,
            min(self.place_radius, DEFAULT_TRANSIENT_STOP_RADIUS),
        )

    def _journey_return_context(
        self, segment: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Carry return evidence across fuel, rest or shopping stops."""
        journey_id = segment.get("journey_id")
        if not journey_id:
            return None
        matching = sorted(
            (
                item
                for item in self._transient.values()
                if item.get("journey_id") == journey_id
                and isinstance(item.get("return_context"), dict)
            ),
            key=lambda item: str(item.get("started_at") or ""),
        )
        if not matching:
            return None
        return deepcopy(matching[0]["return_context"])

    async def _async_wait_for_odometer(
        self, disconnected_at: datetime, start_odometer: float | None
    ) -> tuple[float | None, datetime | None, bool, str]:
        """Prefer a new timestamp plus counter increase; timeout is fallback."""
        current_state = self.hass.states.get(self.odometer_entity)
        current_updated_at = _odometer_updated_at(current_state)
        current_value = _odometer_value(current_state)
        current_signal = odometer_update_signal(
            disconnected_at,
            start_odometer,
            current_updated_at,
            current_value,
        )
        if current_signal is not None:
            return current_value, current_updated_at, False, current_signal

        elapsed = max(0.0, (datetime.now(UTC) - disconnected_at).total_seconds())
        remaining = max(0.0, self.wait_timeout - elapsed)
        if remaining <= 0:
            return (
                current_value,
                current_updated_at,
                True,
                "timeout_latest_value",
            )

        loop = asyncio.get_running_loop()
        future: asyncio.Future[State] = loop.create_future()

        @callback
        def _odometer_changed(event: Event) -> None:
            new_state: State | None = event.data.get("new_state")
            updated_at = _odometer_updated_at(new_state)
            value = _odometer_value(new_state)
            signal = odometer_update_signal(
                disconnected_at,
                start_odometer,
                updated_at,
                value,
            )
            if (
                new_state is not None
                and signal is not None
                and not future.done()
            ):
                future.set_result(new_state)

        unsubscribe = async_track_state_change_event(
            self.hass, [self.odometer_entity], _odometer_changed
        )
        try:
            # Close the race between the first check and listener registration.
            current_state = self.hass.states.get(self.odometer_entity)
            current_updated_at = _odometer_updated_at(current_state)
            current_value = _odometer_value(current_state)
            current_signal = odometer_update_signal(
                disconnected_at,
                start_odometer,
                current_updated_at,
                current_value,
            )
            if (
                current_state is not None
                and current_signal is not None
                and not future.done()
            ):
                future.set_result(current_state)

            async with asyncio.timeout(remaining):
                final_state = await future
            return (
                _odometer_value(final_state),
                _odometer_updated_at(final_state),
                False,
                str(
                    odometer_update_signal(
                        disconnected_at,
                        start_odometer,
                        _odometer_updated_at(final_state),
                        _odometer_value(final_state),
                    )
                    or "post_disconnect_update"
                ),
            )
        except TimeoutError:
            latest_state = self.hass.states.get(self.odometer_entity)
            return (
                _odometer_value(latest_state),
                _odometer_updated_at(latest_state),
                True,
                "timeout_latest_value",
            )
        finally:
            unsubscribe()

    async def _async_apply_previous_final_to_new_active(
        self, finished_segment: dict[str, Any]
    ) -> None:
        """Correct a new trip start if it captured the previous stale cloud value."""
        async with self._transition_lock:
            active = self._active
            if active is None:
                return
            active_started = _parse_datetime(active.get("started_at"))
            finished_at = _parse_datetime(finished_segment.get("ended_at"))
            active_odo_at = _parse_datetime(active.get("start_odometer_updated_at"))
            final_odo_at = _parse_datetime(finished_segment.get("odometer_updated_at"))
            final_odometer = _as_float(finished_segment.get("end_odometer_km"))
            if (
                active_started is not None
                and finished_at is not None
                and active_started >= finished_at
                and final_odometer is not None
                and (
                    active_odo_at is None
                    or final_odo_at is None
                    or active_odo_at <= final_odo_at
                )
            ):
                active["start_odometer_km"] = final_odometer
                active["start_odometer_updated_at"] = _iso_utc(final_odo_at)
                active["start_odometer_source"] = "previous_segment_final"
                active["validation_error"] = None
                await self._async_save_runtime()

    async def _async_send_classification_notification(
        self, segment: dict[str, Any], validation_message: str | None = None
    ) -> None:
        """Ask the phone to classify an unknown destination."""
        segment_id = str(segment["id"])
        estimate = str(segment.get("map_estimate") or "Neznámý cíl")
        candidates = _map_candidates(segment)
        candidate_lines = [
            f"{index}. {candidate['name']} ({_format_distance(candidate.get('distance_m'))})"
            for index, candidate in enumerate(candidates[:3], start=1)
        ]
        return_context = segment.get("return_context")
        transient_stop = segment.get("transient_stop")
        if isinstance(return_context, dict) and return_context.get("suggested"):
            previous_purpose = return_context.get("previous_purpose")
            if previous_purpose:
                base_message = (
                    f"Možný návrat po návštěvě {previous_purpose}. "
                    f"Odhadovaný cíl: {estimate}. Byla to služební zpáteční "
                    "jízda, další klient, nebo osobní cesta?"
                )
            else:
                base_message = (
                    f"Cíl {estimate} je uložený jako návratové místo, ale "
                    "nenavazuje na známou služební jízdu. Jak cestu zařadit?"
                )
        elif isinstance(transient_stop, dict) and transient_stop.get("expired"):
            base_message = (
                f"Zastavení {estimate} vypadalo jako mezizastávka, ale další "
                f"jízda nezačala do {self.transient_stop_minutes:g} minut. "
                "Jak tento samostatný segment zařadit?"
            )
        elif candidate_lines:
            base_message = (
                f"Jízda ukončena. Nejpravděpodobnější cíl: {estimate}. "
                f"Návrhy: {'; '.join(candidate_lines)}. Jak jízdu zařadit? "
                "U volby Navrhnout nového lze zadat vlastní název nebo číslo návrhu."
            )
        else:
            base_message = (
                f"Jízda ukončena. Odhadovaný cíl: {estimate}. Jak jízdu zařadit?"
            )
        message = (
            f"{validation_message} {base_message}"
            if validation_message
            else base_message
        )
        if not segment.get("odometer_ready"):
            message += " Tachometr se doplní automaticky na pozadí."
        service = self.notify_service
        if not self.hass.services.has_service("notify", service):
            _LOGGER.error(
                "Notification service notify.%s does not exist; segment %s remains pending",
                service,
                segment_id,
            )
            return

        if isinstance(return_context, dict) and return_context.get("suggested"):
            actions = [
                {
                    "action": _action_id(ACTION_RETURN, segment_id),
                    "title": "Služební návrat",
                },
                {
                    "action": _action_id(ACTION_NEW, segment_id),
                    "title": "Jiný klient",
                    "behavior": "textInput",
                    "textInputButtonTitle": "Uložit",
                    "textInputPlaceholder": "Název nebo číslo návrhu 1–3",
                },
                {
                    "action": _action_id(ACTION_PRIVATE, segment_id),
                    "title": "Osobní KM",
                },
            ]
        else:
            actions = [
                {
                    "action": _action_id(ACTION_CONFIRM, segment_id),
                    "title": "Potvrdit klienta",
                },
                {
                    "action": _action_id(ACTION_NEW, segment_id),
                    "title": "Navrhnout nového",
                    "behavior": "textInput",
                    "textInputButtonTitle": "Uložit",
                    "textInputPlaceholder": "Název nebo číslo návrhu 1–3",
                },
                {
                    "action": _action_id(ACTION_PRIVATE, segment_id),
                    "title": "Osobní KM",
                },
            ]
        await self.hass.services.async_call(
            "notify",
            service,
            {
                "title": "Kniha jízd",
                "message": message,
                "data": {
                    "tag": f"kniha_jizd_{segment_id}",
                    "actions": actions,
                },
            },
            blocking=True,
        )

    async def _async_process_notification_action(
        self, event_data: dict[str, Any]
    ) -> None:
        """Classify and persist a pending segment from a mobile app action."""
        match = _ACTION_PATTERN.match(str(event_data.get("action", "")))
        if match is None:
            return
        action, segment_id = match.groups()

        async with self._resolution_lock:
            original = self._pending.get(segment_id)
            if original is None:
                return
            segment = original
            reply_text = str(event_data.get("reply_text") or "").strip()
            candidates = _map_candidates(segment)
            selected_candidate: dict[str, Any] | None = None
            learned_label: str | None = None
            place_role: str | None = None
            learn_place = True
            return_context = segment.get("return_context")
            transient_stop = segment.get("transient_stop")
            expired_transient = (
                isinstance(transient_stop, dict)
                and bool(transient_stop.get("expired"))
            )

            if action == ACTION_RETURN:
                await self._async_finalize_return(
                    segment,
                    source="notification_return",
                    learn_place=not expired_transient,
                )
                return
            if action == ACTION_CONFIRM:
                purpose = str(segment.get("map_estimate") or "Neznámý zákazník")
                trip_type = TRIP_TYPE_BUSINESS
                place_role = PLACE_ROLE_CLIENT
                learned_label = purpose
                if candidates:
                    selected_candidate = candidates[0]
            elif action == ACTION_NEW:
                if not reply_text:
                    if isinstance(return_context, dict):
                        purpose = str(
                            segment.get("map_estimate") or "Neznámý zákazník"
                        )
                        if candidates:
                            selected_candidate = candidates[0]
                    else:
                        await self._async_send_classification_notification(
                            original,
                            "Název zákazníka nebyl vyplněn. Zkuste to prosím znovu.",
                        )
                        return
                elif reply_text.isdigit():
                    candidate_number = int(reply_text)
                    if not 1 <= candidate_number <= min(3, len(candidates)):
                        await self._async_send_classification_notification(
                            original,
                            "Toto číslo návrhu není dostupné. Zkuste to prosím znovu.",
                        )
                        return
                    selected_candidate = candidates[candidate_number - 1]
                    purpose = str(selected_candidate["name"])
                else:
                    purpose = reply_text
                trip_type = TRIP_TYPE_BUSINESS
                place_role = PLACE_ROLE_CLIENT
                learned_label = purpose
                if isinstance(return_context, dict):
                    segment["journey_role"] = "destination"
            elif action == ACTION_PRIVATE:
                purpose = "Soukromá"
                trip_type = TRIP_TYPE_PRIVATE
                learn_place = not bool(segment.get("matched_place_id"))

            if expired_transient:
                # Fuel stations, shops and rest areas are inherently contextual.
                # One long stop must not make every future visit permanently
                # business or private.
                learn_place = False
                place_role = None
                learned_label = None

            if selected_candidate is not None:
                segment["map_estimate"] = purpose
                segment["selected_map_candidate"] = selected_candidate

            await self._async_finalize_segment(
                segment,
                purpose=purpose,
                trip_type=trip_type,
                source=(
                    "notification_map_candidate"
                    if selected_candidate is not None
                    else "notification"
                ),
                learn_place=learn_place,
                learned_label=learned_label,
                place_role=place_role,
            )

    async def _async_finalize_return(
        self,
        segment: dict[str, Any],
        source: str,
        learn_place: bool,
    ) -> None:
        """Persist a business return under the preceding customer's purpose."""
        context = segment.get("return_context")
        previous_purpose = (
            context.get("previous_purpose") if isinstance(context, dict) else None
        )
        purpose = str(previous_purpose or "")
        segment["journey_role"] = "return"
        segment["return_of_segment_id"] = (
            context.get("previous_segment_id") if isinstance(context, dict) else None
        )
        learned_destination = str(segment.get("return_destination_label") or "")
        if learned_destination.casefold() in {"soukromá", "soukroma", "private"}:
            learned_destination = ""
        destination_label = str(
            learned_destination
            or segment.get("map_estimate")
            or segment.get("end_address")
            or "Návratové místo"
        )
        await self._async_finalize_segment(
            segment,
            purpose=purpose,
            trip_type=TRIP_TYPE_BUSINESS,
            source=source,
            learn_place=learn_place,
            learned_label=destination_label,
            place_role=PLACE_ROLE_RETURN,
        )

    async def _async_finalize_segment(
        self,
        segment: dict[str, Any],
        purpose: str,
        trip_type: str,
        source: str,
        learn_place: bool,
        learned_label: str | None = None,
        place_role: str | None = None,
    ) -> None:
        """Store a classification immediately and persist once km are ready."""
        segment_id = str(segment["id"])
        segment["purpose"] = purpose
        segment["trip_type"] = trip_type
        segment["classification_source"] = source
        segment["classification_prepared"] = True
        segment["classification_ready"] = True
        segment["classification_options"] = {
            "learn_place": learn_place,
            "learned_label": learned_label,
            "place_role": place_role,
        }
        self._pending.pop(segment_id, None)
        self._closing[segment_id] = segment
        await self._async_save_runtime()
        await self._async_try_finalize_segment(segment)

    async def _async_try_finalize_segment(
        self, segment: dict[str, Any]
    ) -> bool:
        """Persist a classified segment when its whole journey has final km."""
        if (
            segment.get("persisted")
            or not segment.get("classification_ready")
            or not segment.get("odometer_ready")
        ):
            return False
        if any(
            not transient.get("odometer_ready")
            for transient in self._journey_transient_segments(segment)
        ):
            return False

        async with self._finalization_lock:
            if segment.get("persisted"):
                return True
            options = segment.get("classification_options")
            options = options if isinstance(options, dict) else {}
            segment["persisted"] = True
            try:
                await self._async_persist_segment(
                    segment,
                    purpose=str(segment.get("purpose") or ""),
                    trip_type=str(segment.get("trip_type") or TRIP_TYPE_PRIVATE),
                    source=str(segment.get("classification_source") or "unknown"),
                    learn_place=bool(options.get("learn_place")),
                    learned_label=_as_text(options.get("learned_label")),
                    place_role=_as_text(options.get("place_role")),
                )
            except Exception:
                segment["persisted"] = False
                raise
            segment_id = str(segment["id"])
            self._closing.pop(segment_id, None)
            self._pending.pop(segment_id, None)
            self._transient.pop(segment_id, None)
            await self._async_save_runtime()
            return True

    async def _async_try_finalize_journey_destination(
        self, journey_id: Any
    ) -> None:
        """Retry a destination when one preceding stop receives final km."""
        if not journey_id:
            return
        candidates = [*self._closing.values(), *self._pending.values()]
        for candidate in candidates:
            if (
                candidate.get("journey_id") == journey_id
                and candidate.get("classification_ready")
            ):
                await self._async_try_finalize_segment(candidate)

    async def _async_persist_segment(
        self,
        segment: dict[str, Any],
        purpose: str,
        trip_type: str,
        source: str,
        learn_place: bool,
        learned_label: str | None = None,
        place_role: str | None = None,
    ) -> None:
        """Write a ready segment and optionally learn its destination."""
        async with self._journey_lock:
            segment["journey_id"] = segment.get("journey_id") or uuid4().hex
            inherited = self._journey_transient_segments(segment)
            apply_journey_classification(
                inherited, segment, purpose, trip_type, source
            )
            for transient in inherited:
                transient["classification_prepared"] = True
                transient["classification_ready"] = True
                transient["persisted"] = True
                await self._async_learn_transient_place(transient)
                await self.repository.async_append_segment(transient)
                self._transient.pop(str(transient.get("id")), None)

            await self.repository.async_append_segment(segment)

            if learn_place and (
                segment.get("end_address")
                or (
                    segment.get("end_latitude") is not None
                    and segment.get("end_longitude") is not None
                )
            ):
                await self.repository.async_learn_place(
                    {
                        "id": (
                            segment.get("matched_place_id")
                            if place_role is not None
                            and segment.get("matched_place_id")
                            else uuid4().hex
                        ),
                        "latitude": segment.get("end_latitude"),
                        "longitude": segment.get("end_longitude"),
                        "address": segment.get("end_address"),
                        "label": learned_label or purpose,
                        "trip_type": (
                            TRIP_TYPE_CONTEXTUAL
                            if place_role == PLACE_ROLE_RETURN
                            else trip_type
                        ),
                        "place_role": place_role,
                        "map_name": segment.get("map_estimate"),
                        "updated_at": _iso_utc(datetime.now(UTC)),
                    }
                )
        self._last_error = None
        await self._async_refresh_statistics()
        _LOGGER.info(
            "Trip segment %s and %s intermediate stops saved as %s (%s)",
            segment.get("id"),
            len(inherited),
            purpose,
            trip_type,
        )

    def _journey_transient_segments(
        self, destination: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Return unresolved stops belonging to the destination's journey."""
        journey_id = destination.get("journey_id")
        if not journey_id:
            return []
        return sorted(
            (
                item
                for item in self._transient.values()
                if item.get("journey_id") == journey_id
                and item.get("id") != destination.get("id")
            ),
            key=lambda item: str(item.get("started_at") or ""),
        )

    async def _async_learn_transient_place(
        self, segment: dict[str, Any]
    ) -> None:
        """Learn a confirmed intermediate POI using a deliberately small radius."""
        stop = segment.get("transient_stop")
        if not isinstance(stop, dict):
            return
        if not (
            segment.get("end_address")
            or (
                segment.get("end_latitude") is not None
                and segment.get("end_longitude") is not None
            )
        ):
            return
        place_id = str(segment.get("matched_place_id") or uuid4().hex)
        segment["matched_place_id"] = place_id
        await self.repository.async_learn_place(
            {
                "id": place_id,
                "latitude": segment.get("end_latitude"),
                "longitude": segment.get("end_longitude"),
                "address": segment.get("end_address"),
                "label": (
                    stop.get("name")
                    or segment.get("map_estimate")
                    or "Mezizastávka"
                ),
                "trip_type": TRIP_TYPE_CONTEXTUAL,
                "place_role": PLACE_ROLE_TRANSIENT,
                "radius_m": LEARNED_TRANSIENT_RADIUS,
                "map_name": segment.get("map_estimate"),
                "updated_at": _iso_utc(datetime.now(UTC)),
            }
        )

    def _capture_location(self) -> dict[str, float | str | None]:
        """Capture GPS attributes and the full geocoded address."""
        gps_state = self.hass.states.get(self.gps_entity)
        latitude = _coordinate(gps_state, "latitude")
        longitude = _coordinate(gps_state, "longitude")

        address_state = self.hass.states.get(self.address_entity)
        address: str | None = None
        if address_state is not None and address_state.state.casefold() not in UNAVAILABLE_STATES:
            address = address_state.state.strip()
        if not address and latitude is not None and longitude is not None:
            address = f"{latitude:.6f}, {longitude:.6f}"
        return {"latitude": latitude, "longitude": longitude, "address": address}

    async def _async_save_runtime(self) -> None:
        """Persist all unfinished journey state across HA restarts."""
        async with self._runtime_lock:
            await self._runtime_store.async_save(
                {
                    "active": deepcopy(self._active),
                    "closing": deepcopy(self._closing),
                    "pending": deepcopy(self._pending),
                    "transient": deepcopy(self._transient),
                }
            )
        self._notify_listeners()


def _normalize_notify_service(value: str) -> str:
    """Accept both mobile_app_phone and notify.mobile_app_phone."""
    normalized = value.strip()
    if normalized.startswith("notify."):
        return normalized.split(".", maxsplit=1)[1]
    return normalized


def _action_id(action: str, segment_id: str) -> str:
    """Create a collision-free action identifier."""
    return f"{ACTION_PREFIX}_{action}_{segment_id}"


def _coordinate(state: State | None, key: str) -> float | None:
    """Read one coordinate attribute."""
    if state is None:
        return None
    return _as_float(state.attributes.get(key))


def _odometer_value(state: State | None) -> float | None:
    """Parse the odometer state as kilometres."""
    if state is None or state.state.casefold() in UNAVAILABLE_STATES:
        return None
    return _as_float(state.state)


def _odometer_updated_at(state: State | None) -> datetime | None:
    """Prefer the sensor's explicit last_updated attribute, then HA State metadata."""
    if state is None:
        return None
    attribute_value = state.attributes.get("last_updated")
    parsed = _parse_datetime(attribute_value)
    if parsed is not None:
        return parsed
    return _ensure_utc(state.last_updated)


def _parse_datetime(value: Any) -> datetime | None:
    """Parse common timestamp representations and normalize them to UTC."""
    if isinstance(value, datetime):
        return _ensure_utc(value)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str) and value.strip():
        parsed = dt_util.parse_datetime(value.strip())
        if parsed is not None:
            return _ensure_utc(parsed)
    return None


def _ensure_utc(value: datetime) -> datetime:
    """Make a datetime aware and convert it to UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso_utc(value: datetime | None) -> str | None:
    """Serialize an optional datetime in UTC."""
    return _ensure_utc(value).isoformat() if value is not None else None


def _as_float(value: Any) -> float | None:
    """Parse numbers with either a decimal dot or comma."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).strip().replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return None


def _as_text(value: Any) -> str | None:
    """Return nonempty string data."""
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _address_is_coordinate_fallback(value: Any) -> bool:
    """Detect the coordinate text generated when Companion has no address."""
    if not isinstance(value, str):
        return True
    return bool(re.fullmatch(r"-?\d+\.\d{6},\s*-?\d+\.\d{6}", value.strip()))


def _map_candidates(segment: dict[str, Any]) -> list[dict[str, Any]]:
    """Return valid serialized map candidates from a segment."""
    candidates = segment.get("map_candidates")
    if not isinstance(candidates, list):
        return []
    return [
        candidate
        for candidate in candidates
        if isinstance(candidate, dict) and candidate.get("name")
    ]


def _format_distance(value: Any) -> str:
    """Format candidate distance compactly for a phone notification."""
    distance = _as_float(value)
    if distance is None:
        return "neznámá vzdálenost"
    if distance < 1000:
        return f"{distance:.0f} m"
    return f"{distance / 1000:.1f} km"


def _panel_trip_row(segment: dict[str, Any], status: str) -> dict[str, Any]:
    """Serialize only the fields needed by the editable daily table."""
    return {
        "id": segment.get("id"),
        "journey_id": segment.get("journey_id"),
        "started_at": segment.get("started_at"),
        "ended_at": segment.get("ended_at"),
        "start_address": segment.get("start_address"),
        "end_address": segment.get("end_address"),
        "distance_km": segment.get("distance_km"),
        "purpose": segment.get("purpose"),
        "trip_type": segment.get("trip_type"),
        "journey_role": segment.get("journey_role"),
        "odometer_ready": bool(segment.get("odometer_ready") or status == "saved"),
        "odometer_completion_source": segment.get("odometer_completion_source"),
        "status": status,
        "editable": segment.get("ended_at") is not None,
    }
