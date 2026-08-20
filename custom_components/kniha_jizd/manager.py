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

from .const import (
    ACTION_CONFIRM,
    ACTION_NEW,
    ACTION_PREFIX,
    ACTION_PRIVATE,
    CONF_ADDRESS_ENTITY,
    CONF_GPS_ENTITY,
    CONF_INSTITUTION_SEARCH_RADIUS,
    CONF_NOTIFY_SERVICE,
    CONF_ODOMETER_ENTITY,
    CONF_PLACE_RADIUS,
    CONF_TRIGGER_ENTITY,
    CONF_WAIT_TIMEOUT,
    DOMAIN,
    EVENT_NOTIFICATION_ACTION,
    RUNTIME_STORE_VERSION,
    TRIP_TYPE_BUSINESS,
    TRIP_TYPE_PRIVATE,
    UNAVAILABLE_STATES,
)
from .geocoding import NominatimGeocoder
from .nearby_search import NearbyInstitutionSearcher
from .storage import KnihaJizdRepository

_LOGGER = logging.getLogger(__name__)

_ACTION_PATTERN = re.compile(
    rf"^{ACTION_PREFIX}_({ACTION_CONFIRM}|{ACTION_NEW}|{ACTION_PRIVATE})_([0-9a-f]+)$"
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
        self.place_radius = float(config[CONF_PLACE_RADIUS])
        self.institution_search_radius = float(
            config[CONF_INSTITUTION_SEARCH_RADIUS]
        )

        self._active: dict[str, Any] | None = None
        self._closing: dict[str, dict[str, Any]] = {}
        self._pending: dict[str, dict[str, Any]] = {}
        self._statistics: dict[str, Any] = {
            "segments_total": 0,
            "business_km_total": 0.0,
            "private_km_total": 0.0,
            "today_segments": 0,
            "today_business_km": 0.0,
            "today_private_km": 0.0,
            "last_segment": None,
        }
        self._statistics_date: str | None = None
        self._last_error: str | None = None
        self._export: dict[str, Any] = {
            "state": "never",
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
        self._active = active if isinstance(active, dict) else None
        self._closing = closing if isinstance(closing, dict) else {}
        self._pending = pending if isinstance(pending, dict) else {}
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
            self._create_task(
                self._async_send_classification_notification(segment),
                f"{DOMAIN}_restore_notification_{segment.get('id', 'unknown')}",
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
    def set_export_running(self) -> None:
        """Expose a running Excel export to entities and the panel."""
        self._export.update({"state": "generating", "error": None})
        self._notify_listeners()

    @callback
    def set_export_success(self, path: Path) -> None:
        """Expose a finished export and create a temporary download link."""
        self._download_token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        self._download_token_expires_at = now + timedelta(minutes=15)
        self._export.update(
            {
                "state": "ready",
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
                "date": local_started.date().isoformat(),
                "started_at": _iso_utc(started_at),
                "ended_at": None,
                "odometer_updated_at": None,
                "odometer_wait_timed_out": None,
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
                "map_estimate": None,
                "map_address": None,
                "map_attribution": None,
                "map_candidates": [],
                "candidate_search_radius_m": None,
                "selected_map_candidate": None,
                "validation_error": (
                    None if start_odometer is not None else "missing_start_odometer"
                ),
            }
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
        """Wait for a post-disconnect odometer state and classify the segment."""
        segment_id = str(segment["id"])
        disconnected_at = _parse_datetime(segment.get("ended_at")) or datetime.now(UTC)
        end_odometer, odometer_updated_at, timed_out = (
            await self._async_wait_for_odometer(disconnected_at)
        )
        segment["odometer_updated_at"] = _iso_utc(odometer_updated_at)
        segment["odometer_wait_timed_out"] = timed_out
        segment["end_odometer_km"] = end_odometer

        start_odometer = _as_float(segment.get("start_odometer_km"))
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

        await self._async_apply_previous_final_to_new_active(segment)

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
            if _address_is_coordinate_fallback(
                segment.get("end_address")
            ) and learned_place.get("matched_address"):
                segment["end_address"] = learned_place["matched_address"]
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
            self._closing.pop(segment_id, None)
            await self._async_save_runtime()
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

        if not segment.get("map_estimate"):
            segment["map_estimate"] = (
                segment.get("map_address")
                or segment.get("end_address")
                or "Neznámý cíl"
            )

        self._closing.pop(segment_id, None)
        self._pending[segment_id] = segment
        await self._async_save_runtime()
        await self._async_send_classification_notification(segment)

    async def _async_wait_for_odometer(
        self, disconnected_at: datetime
    ) -> tuple[float | None, datetime | None, bool]:
        """Wait until the odometer last_updated is strictly after disconnection."""
        current_state = self.hass.states.get(self.odometer_entity)
        current_updated_at = _odometer_updated_at(current_state)
        if current_updated_at is not None and current_updated_at > disconnected_at:
            return _odometer_value(current_state), current_updated_at, False

        elapsed = max(0.0, (datetime.now(UTC) - disconnected_at).total_seconds())
        remaining = max(0.0, self.wait_timeout - elapsed)
        if remaining <= 0:
            return _odometer_value(current_state), current_updated_at, True

        loop = asyncio.get_running_loop()
        future: asyncio.Future[State] = loop.create_future()

        @callback
        def _odometer_changed(event: Event) -> None:
            new_state: State | None = event.data.get("new_state")
            updated_at = _odometer_updated_at(new_state)
            if (
                new_state is not None
                and updated_at is not None
                and updated_at > disconnected_at
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
            if (
                current_state is not None
                and current_updated_at is not None
                and current_updated_at > disconnected_at
                and not future.done()
            ):
                future.set_result(current_state)

            async with asyncio.timeout(remaining):
                final_state = await future
            return (
                _odometer_value(final_state),
                _odometer_updated_at(final_state),
                False,
            )
        except TimeoutError:
            latest_state = self.hass.states.get(self.odometer_entity)
            return (
                _odometer_value(latest_state),
                _odometer_updated_at(latest_state),
                True,
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
        if candidate_lines:
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
        service = self.notify_service
        if not self.hass.services.has_service("notify", service):
            _LOGGER.error(
                "Notification service notify.%s does not exist; segment %s remains pending",
                service,
                segment_id,
            )
            return

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
            segment = deepcopy(original)
            reply_text = str(event_data.get("reply_text") or "").strip()
            candidates = _map_candidates(segment)
            selected_candidate: dict[str, Any] | None = None

            if action == ACTION_CONFIRM:
                purpose = str(segment.get("map_estimate") or "Neznámý zákazník")
                trip_type = TRIP_TYPE_BUSINESS
                if candidates:
                    selected_candidate = candidates[0]
            elif action == ACTION_NEW:
                if not reply_text:
                    await self._async_send_classification_notification(
                        original,
                        "Název zákazníka nebyl vyplněn. Zkuste to prosím znovu.",
                    )
                    return
                if reply_text.isdigit():
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
            elif action == ACTION_PRIVATE:
                purpose = "Soukromá"
                trip_type = TRIP_TYPE_PRIVATE

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
                learn_place=True,
            )
            self._pending.pop(segment_id, None)
            await self._async_save_runtime()

    async def _async_finalize_segment(
        self,
        segment: dict[str, Any],
        purpose: str,
        trip_type: str,
        source: str,
        learn_place: bool,
    ) -> None:
        """Write a fully classified segment and optionally learn its destination."""
        segment["purpose"] = purpose
        segment["trip_type"] = trip_type
        segment["classification_source"] = source
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
                    "id": uuid4().hex,
                    "latitude": segment.get("end_latitude"),
                    "longitude": segment.get("end_longitude"),
                    "address": segment.get("end_address"),
                    "label": purpose,
                    "trip_type": trip_type,
                    "map_name": segment.get("map_estimate"),
                    "updated_at": _iso_utc(datetime.now(UTC)),
                }
            )
        self._last_error = None
        await self._async_refresh_statistics()
        _LOGGER.info(
            "Trip segment %s saved as %s (%s)", segment.get("id"), purpose, trip_type
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
        """Persist active, closing and pending state across HA restarts."""
        async with self._runtime_lock:
            await self._runtime_store.async_save(
                {
                    "active": deepcopy(self._active),
                    "closing": deepcopy(self._closing),
                    "pending": deepcopy(self._pending),
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
