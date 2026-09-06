"""Trip lifecycle manager for Kniha jízd."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hmac
import logging
from math import floor
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

from .address_rules import configured_place_match, coordinate_distance_m, shorten_address
from .const import (
    ACTION_CONFIRM,
    ACTION_BUSINESS,
    ACTION_NEW,
    ACTION_PREFIX,
    ACTION_PRIVATE,
    ACTION_RETURN,
    ACTION_SAVE_NAMED_PLACE,
    ACTION_SAVE_PLACE,
    ACTION_SKIP_PLACE,
    CONF_ADDRESS_ENTITY,
    CONF_COMPANY_ADDRESS,
    CONF_COMPANY_LATITUDE,
    CONF_COMPANY_LABEL,
    CONF_COMPANY_LONGITUDE,
    CONF_COMPANY_RADIUS,
    CONF_CLIENT_RADIUS,
    CONF_GPS_ENTITY,
    CONF_HOME_ADDRESS,
    CONF_HOME_LATITUDE,
    CONF_HOME_LONGITUDE,
    CONF_HOME_RADIUS,
    CONF_INSTITUTION_SEARCH_RADIUS,
    CONF_LOCATION_SETTLE_SECONDS,
    CONF_NOTIFY_SERVICE,
    CONF_ODOMETER_ENTITY,
    CONF_PENDING_REVIEW_HOURS,
    CONF_PRIVATE_RADIUS,
    CONF_RETURN_CONTEXT_HOURS,
    CONF_TRANSIENT_STOP_MINUTES,
    CONF_TRANSIENT_RADIUS,
    CONF_TRIGGER_ENTITY,
    DOMAIN,
    EVENT_NOTIFICATION_ACTION,
    PLACE_ROLE_CLIENT,
    PLACE_ROLE_PRIVATE,
    PLACE_ROLE_TRANSIENT,
    RUNTIME_STORE_VERSION,
    TRIP_TYPE_BUSINESS,
    TRIP_TYPE_CONTEXTUAL,
    TRIP_TYPE_PRIVATE,
    TRIP_TYPE_UNCLASSIFIED,
    UNAVAILABLE_STATES,
)
from .geocoding import NominatimGeocoder
from .input_parsing import coordinates_from_state, odometer_from_state, parse_decimal
from .journey_chain import (
    apply_journey_classification,
    continuation_details,
    detect_transient_stop,
    map_routes_without_transient_stops,
    normalize_trip_purpose,
    parking_boundary_details,
)
from .location_logic import (
    location_is_fresh,
    merge_location_snapshot,
    select_coordinate_candidate,
)
from .nearby_search import NearbyInstitutionSearcher
from .odometer_logic import odometer_update_signal, propagated_start_odometer
from .storage import (
    KnihaJizdRepository,
    learned_place_behavior,
    place_trip_types,
    suppress_configured_place_duplicates,
)
from .trip_context import infer_trip_context
from .workflow_logic import (
    PHONE_NOTIFICATION_GRACE_MINUTES,
    gps_accuracy_suitable,
    mobile_notification_policy,
    panel_question,
    place_label_suggestion,
    place_name_input_allowed,
    should_offer_place_save,
)

_LOGGER = logging.getLogger(__name__)
_IMPLICIT_TRANSIENT_STOP_MINUTES = 3.0

_ACTION_PATTERN = re.compile(
    rf"^{ACTION_PREFIX}_({ACTION_CONFIRM}|{ACTION_NEW}|{ACTION_BUSINESS}|{ACTION_PRIVATE}|"
    rf"{ACTION_RETURN})_([0-9a-f]+)$"
)
_PLACE_ACTION_PATTERN = re.compile(
    rf"^{ACTION_PREFIX}_({ACTION_SAVE_PLACE}|{ACTION_SAVE_NAMED_PLACE}|"
    rf"{ACTION_SKIP_PLACE})_([0-9a-f]+)$"
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
        self.location_settle_seconds = float(config[CONF_LOCATION_SETTLE_SECONDS])
        self.return_context_hours = float(config[CONF_RETURN_CONTEXT_HOURS])
        self.transient_stop_minutes = float(config[CONF_TRANSIENT_STOP_MINUTES])
        self.pending_review_hours = float(config[CONF_PENDING_REVIEW_HOURS])
        self.home_address = str(config.get(CONF_HOME_ADDRESS, "")).strip()
        self.home_latitude = _as_float(config.get(CONF_HOME_LATITUDE))
        self.home_longitude = _as_float(config.get(CONF_HOME_LONGITUDE))
        self.company_address = str(config.get(CONF_COMPANY_ADDRESS, "")).strip()
        self.company_latitude = _as_float(config.get(CONF_COMPANY_LATITUDE))
        self.company_longitude = _as_float(config.get(CONF_COMPANY_LONGITUDE))
        self.company_label = str(config.get(CONF_COMPANY_LABEL, "")).strip()
        self.home_radius = float(config[CONF_HOME_RADIUS])
        self.company_radius = float(config[CONF_COMPANY_RADIUS])
        self.client_radius = float(config[CONF_CLIENT_RADIUS])
        self.private_radius = float(config[CONF_PRIVATE_RADIUS])
        self.transient_radius = float(config[CONF_TRANSIENT_RADIUS])
        self.institution_search_radius = float(
            config[CONF_INSTITUTION_SEARCH_RADIUS]
        )

        self._active: dict[str, Any] | None = None
        self._closing: dict[str, dict[str, Any]] = {}
        self._pending: dict[str, dict[str, Any]] = {}
        self._transient: dict[str, dict[str, Any]] = {}
        self._place_prompts: dict[str, dict[str, Any]] = {}
        self._statistics: dict[str, Any] = {
            "segments_total": 0,
            "business_km_total": 0.0,
            "private_km_total": 0.0,
            "today_segments": 0,
            "today_business_km": 0.0,
            "today_private_km": 0.0,
            "today_rows": [],
            "today_odometer_check": {},
            "last_segment": None,
        }
        self._statistics_date: str | None = None
        self._last_error: str | None = None
        self._last_notification_action: dict[str, Any] | None = None
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
        self._odometer_completion_condition = asyncio.Condition()
        self._odometer_rollover_events: dict[str, asyncio.Event] = {}
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
        place_prompts = runtime.get("place_prompts")
        self._active = active if isinstance(active, dict) else None
        self._closing = closing if isinstance(closing, dict) else {}
        self._pending = pending if isinstance(pending, dict) else {}
        self._transient = transient if isinstance(transient, dict) else {}
        self._place_prompts = (
            place_prompts if isinstance(place_prompts, dict) else {}
        )
        for segment in self._closing.values():
            if segment.get("classification_prepared") and not segment.get(
                "classification_ready"
            ):
                # Preparation may have been cancelled after setting the first
                # flag but before moving the segment to its durable next queue.
                segment["classification_prepared"] = False
        for segment in [
            *self._closing.values(),
            *self._pending.values(),
            *self._transient.values(),
        ]:
            self._restore_processing_flags(segment)
        runtime_segments = [
            *([self._active] if self._active is not None else []),
            *self._closing.values(),
            *self._pending.values(),
            *self._transient.values(),
        ]
        for segment in sorted(
            runtime_segments,
            key=lambda item: str(item.get("started_at") or ""),
        ):
            started_at = _parse_datetime(segment.get("started_at"))
            if started_at is not None:
                self._mark_previous_odometer_rollovers(started_at)
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
                retry_missing_search = (
                    not _map_candidates(segment)
                    and not segment.get("candidate_search_completed_at")
                    and _as_float(segment.get("end_latitude")) is not None
                    and _as_float(segment.get("end_longitude")) is not None
                )
                self._create_task(
                    (
                        self._async_retry_pending_suggestions(segment)
                        if retry_missing_search
                        else self._async_schedule_classification_notification(
                            segment
                        )
                    ),
                    f"{DOMAIN}_restore_pending_search_{segment.get('id', 'unknown')}",
                )
            self._schedule_pending_review(segment)

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

        for prompt in list(self._place_prompts.values()):
            if not prompt.get("notification_sent_at"):
                self._create_task(
                    self._async_send_place_notification(prompt),
                    f"{DOMAIN}_restore_place_prompt_{prompt.get('segment_id', 'unknown')}",
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
        # A segment still present in a runtime queue needs its idempotent final
        # cleanup even if a crash happened just after the raw-file append.
        segment["persisted"] = False
        segment.setdefault("end_location_ready", True)
        segment.setdefault("start_address_raw", segment.get("start_address"))
        segment.setdefault("end_address_raw", segment.get("end_address"))
        # Task flags are process-local and must not survive a reload.
        segment.pop("notification_task_scheduled", None)

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
        # A task can make one final in-memory change while the first Store save
        # yields. Persist the settled post-cancellation state as well.
        await self._async_save_runtime()
        self._odometer_rollover_events.clear()
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
        if self._place_prompts:
            return "waiting_place_save"
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
        latitude, longitude, gps_source = _location_coordinates(
            gps_state, address_state
        )
        location_details = _location_coordinate_details(gps_state, address_state)
        location_updated_at = (
            location_details.get("updated_at")
            if location_details is not None
            else None
        )
        odometer, odometer_source = _odometer_details(odometer_state)
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
            # Notifications are an optional convenience. The panel remains the
            # authoritative workflow when the mobile service is unavailable.
            "ready": trigger_ok and gps_ok and odometer_ok,
            "status": self.status,
            "active_segment_id": (
                self._active.get("id") if self._active is not None else None
            ),
            "active_started_at": (
                self._active.get("started_at") if self._active is not None else None
            ),
            "closing_count": len(self._closing),
            "pending_count": len(self._pending),
            "review_count": int(self._statistics.get("review_count_total") or 0)
            + sum(1 for item in self._pending.values() if item.get("needs_review")),
            "today_review_count": int(
                self._statistics.get("today_review_count") or 0
            ),
            "transient_count": len(self._transient),
            "place_prompt_count": len(self._place_prompts),
            "place_questions": [
                {
                    "id": prompt.get("segment_id"),
                    "started_at": prompt.get("started_at"),
                    "start_address": prompt.get("start_address"),
                    "end_address": prompt.get("end_address"),
                    "place_question": deepcopy(prompt),
                }
                for prompt in sorted(
                    self._place_prompts.values(),
                    key=lambda item: str(item.get("started_at") or ""),
                )
            ],
            "return_context_hours": self.return_context_hours,
            "transient_stop_minutes": self.transient_stop_minutes,
            "pending_review_hours": self.pending_review_hours,
            "location_settle_seconds": self.location_settle_seconds,
            "home_address": self.home_address or None,
            "home_latitude": self.home_latitude,
            "home_longitude": self.home_longitude,
            "company_address": self.company_address or None,
            "company_latitude": self.company_latitude,
            "company_longitude": self.company_longitude,
            "company_label": self.company_label or None,
            "home_radius_m": self.home_radius,
            "company_radius_m": self.company_radius,
            "client_radius_m": self.client_radius,
            "private_radius_m": self.private_radius,
            "transient_radius_m": self.transient_radius,
            "odometer_day_check": deepcopy(
                self._statistics.get("today_odometer_check") or {}
            ),
            "today_trips": self._today_trip_rows(),
            "trigger_entity": self.trigger_entity,
            "trigger_state": trigger_state.state if trigger_state else None,
            "trigger_ok": trigger_ok,
            "gps_entity": self.gps_entity,
            "gps_ok": gps_ok,
            "latitude": latitude,
            "longitude": longitude,
            "gps_coordinate_source": gps_source,
            "gps_updated_at": _iso_utc(location_updated_at),
            "gps_age_seconds": (
                max(
                    0.0,
                    round(
                        (datetime.now(UTC) - location_updated_at).total_seconds(),
                        1,
                    ),
                )
                if location_updated_at is not None
                else None
            ),
            "gps_accuracy_m": (
                location_details.get("accuracy_m")
                if location_details is not None
                else None
            ),
            "gps_state": gps_state.state if gps_state else None,
            "address_entity": self.address_entity,
            "address": address,
            "address_ok": address is not None,
            "odometer_entity": self.odometer_entity,
            "odometer_km": odometer,
            "odometer_ok": odometer_ok,
            "odometer_value_source": odometer_source,
            "odometer_state": odometer_state.state if odometer_state else None,
            "odometer_updated_at": _iso_utc(_odometer_updated_at(odometer_state)),
            "notify_service": f"notify.{self.notify_service}",
            "notify_ok": notify_ok,
            "last_notification_action": deepcopy(self._last_notification_action),
            "last_error": self._last_error,
        }

    @property
    def public_diagnostics(self) -> dict[str, Any]:
        """Expose health without addresses, coordinates or trip records."""
        diagnostics = self.diagnostics
        allowed = (
            "kniha_jizd_kind",
            "ready",
            "status",
            "closing_count",
            "pending_count",
            "review_count",
            "today_review_count",
            "transient_count",
            "place_prompt_count",
            "trigger_ok",
            "gps_ok",
            "address_ok",
            "odometer_ok",
            "notify_ok",
            "last_error",
        )
        return {key: diagnostics.get(key) for key in allowed}

    @property
    def panel_overview(self) -> dict[str, Any]:
        """Return one coherent admin snapshot for the sidebar panel."""
        diagnostics = self.diagnostics
        export_status = {
            key: deepcopy(value)
            for key, value in self.export_status.items()
            if key != "path"
        }
        return {
            "generated_at": _iso_utc(datetime.now(UTC)),
            "status": self.status,
            "ready": bool(diagnostics["ready"]),
            "diagnostics": diagnostics,
            "statistics": {
                key: deepcopy(self._statistics.get(key))
                for key in (
                    "segments_total",
                    "business_km_total",
                    "private_km_total",
                    "today_segments",
                    "today_business_km",
                    "today_private_km",
                )
            },
            "last_trip": deepcopy(self._statistics.get("last_segment")),
            "export": export_status,
        }

    async def _async_get_visible_place_markers(
        self,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Return the exact configured and learned points shown on the map."""
        learned_places = await self.repository.async_get_places_for_map(
            self.client_radius,
            self.private_radius,
            self.transient_radius,
        )
        learned_places = [
            place
            for place in learned_places
            if place.get("place_role") != PLACE_ROLE_TRANSIENT
        ]
        configured_places: list[dict[str, Any]] = []
        for marker in (
            {
                "id": "configured:home",
                "place_id": "configured:home",
                "label": "Domov",
                "place_role": "home",
                "trip_type": TRIP_TYPE_CONTEXTUAL,
                "latitude": self.home_latitude,
                "longitude": self.home_longitude,
                "address": self.home_address or None,
                "radius_m": self.home_radius,
            },
            {
                "id": "configured:company",
                "place_id": "configured:company",
                "label": self.company_label or "Firma",
                "place_role": "company",
                "trip_type": TRIP_TYPE_CONTEXTUAL,
                "latitude": self.company_latitude,
                "longitude": self.company_longitude,
                "address": self.company_address or None,
                "radius_m": self.company_radius,
            },
        ):
            if marker["latitude"] is not None and marker["longitude"] is not None:
                configured_places.append(marker)
        learned_places = suppress_configured_place_duplicates(
            learned_places, configured_places
        )
        return configured_places, learned_places

    async def async_get_map_data(self) -> dict[str, Any]:
        """Build current, learned and configured place data for the panel map."""
        configured_places, learned_places = (
            await self._async_get_visible_place_markers()
        )

        location = self._capture_location()
        car_latitude = _as_float(location.get("latitude"))
        car_longitude = _as_float(location.get("longitude"))
        car_accuracy = _as_float(location.get("accuracy_m"))
        if car_accuracy is not None and car_accuracy < 0:
            car_accuracy = None
        current_zone: dict[str, Any] | None = None
        zone_accuracy_limited = False
        for marker in [*configured_places, *learned_places]:
            distance = coordinate_distance_m(
                car_latitude,
                car_longitude,
                marker.get("latitude"),
                marker.get("longitude"),
            )
            radius = _as_float(marker.get("radius_m"))
            if distance is None or radius is None:
                continue
            if car_accuracy is not None and car_accuracy > radius:
                if distance <= radius + car_accuracy:
                    zone_accuracy_limited = True
                continue
            if distance > radius:
                continue
            if current_zone is None or distance < float(current_zone["distance_m"]):
                current_zone = {
                    "id": marker.get("id"),
                    "place_id": marker.get("place_id"),
                    "label": marker.get("label"),
                    "place_role": marker.get("place_role"),
                    "distance_m": round(distance, 1),
                    "radius_m": radius,
                }

        gps_state = self.hass.states.get(self.gps_entity)
        _, _, gps_source = _location_coordinates(
            gps_state, self.hass.states.get(self.address_entity)
        )
        car = {
            "latitude": car_latitude,
            "longitude": car_longitude,
            "address": location.get("address_raw") or location.get("address"),
            "coordinate_source": gps_source,
            "accuracy_m": car_accuracy,
            "updated_at": location.get("coordinate_updated_at"),
            "current_zone": current_zone,
            "zone_status": (
                "matched"
                if current_zone is not None
                else "accuracy_limited"
                if zone_accuracy_limited
                else "outside"
            ),
            "driving": self._active is not None,
        }

        routes: dict[str, dict[str, Any]] = {}
        persisted = self._statistics.get("today_rows")
        if isinstance(persisted, list):
            for segment in persisted:
                if isinstance(segment, dict) and segment.get("id"):
                    routes[str(segment["id"])] = _map_trip_row(segment, "saved")
        runtime_groups = (
            ("driving", [self._active] if self._active is not None else []),
            ("waiting_odometer", list(self._closing.values())),
            ("waiting_classification", list(self._pending.values())),
            ("waiting_journey", list(self._transient.values())),
        )
        today = dt_util.now().date().isoformat()
        for status, segments in runtime_groups:
            for segment in segments:
                if str(segment.get("date")) == today and segment.get("id"):
                    routes[str(segment["id"])] = _map_trip_row(segment, status)

        short_stops = [
            route.copy() for route in routes.values() if route.get("short_stop")
        ]

        return {
            "generated_at": _iso_utc(datetime.now(UTC)),
            "attribution": "© OpenStreetMap contributors",
            "client_radius_m": self.client_radius,
            "transient_radius_m": self.transient_radius,
            "private_radius_m": self.private_radius,
            "home_radius_m": self.home_radius,
            "company_radius_m": self.company_radius,
            "car": car,
            "configured_places": configured_places,
            "learned_places": learned_places,
            "short_stops": short_stops,
            "today_routes": map_routes_without_transient_stops(
                [
                    route
                    for route in routes.values()
                    if not route.get("needs_review")
                ]
            ),
        }

    async def async_get_history_data(
        self, month: str, selected_date: str
    ) -> dict[str, Any]:
        """Build calendar totals and editable rows for the history panel."""
        history = await self.repository.async_get_history(month, selected_date)
        local_date = dt_util.now().date().isoformat()
        if selected_date == local_date:
            rows = self._today_trip_rows()
        else:
            stored_rows = history.get("rows")
            rows = (
                [
                    _panel_trip_row(segment, "saved")
                    for segment in stored_rows
                    if isinstance(segment, dict)
                ]
                if isinstance(stored_rows, list)
                else []
            )
        return {
            **history,
            "generated_at": _iso_utc(datetime.now(UTC)),
            "rows": rows,
        }

    async def async_get_places_data(self) -> dict[str, Any]:
        """Return every stored point plus the exact points visible on the map."""
        places = await self.repository.async_get_managed_places(
            self.client_radius, self.private_radius, self.transient_radius
        )
        configured_places, learned_map_places = (
            await self._async_get_visible_place_markers()
        )
        return {
            "generated_at": _iso_utc(datetime.now(UTC)),
            "places": places,
            "configured_places": configured_places,
            "visible_learned_point_ids": [
                marker["id"] for marker in learned_map_places
            ],
            "stored_point_count": sum(
                int(place.get("anchor_count") or 0) for place in places
            ),
            "map_point_count": len(configured_places) + len(learned_map_places),
            "radii": {
                "home": self.home_radius,
                "company": self.company_radius,
                "business": self.client_radius,
                "private": self.private_radius,
                "transient": self.transient_radius,
            },
        }

    async def async_manage_place(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate and apply one learned-place management command."""
        action = str(payload.get("action") or "").strip().casefold()
        if action == "update":
            place_id = str(payload.get("place_id") or "").strip()
            if not place_id:
                raise ValueError("place_id is required")
            result = await self.repository.async_update_place(
                place_id,
                str(payload.get("label") or ""),
                str(payload.get("classification") or ""),
                payload.get("radius_m"),
            )
        elif action == "delete":
            place_id = str(payload.get("place_id") or "").strip()
            if not place_id:
                raise ValueError("place_id is required")
            result = await self.repository.async_delete_place(place_id)
        elif action == "delete_anchor":
            place_id = str(payload.get("place_id") or "").strip()
            anchor_index = payload.get("anchor_index")
            if not place_id:
                raise ValueError("place_id is required")
            if isinstance(anchor_index, bool) or not isinstance(anchor_index, int):
                raise ValueError("anchor_index must be an integer")
            result = await self.repository.async_delete_place_anchor(
                place_id, anchor_index
            )
        elif action == "merge":
            raw_ids = payload.get("place_ids")
            if not isinstance(raw_ids, list):
                raise ValueError("place_ids must be a list")
            result = await self.repository.async_merge_places(
                [str(item) for item in raw_ids],
                str(payload.get("label") or "").strip() or None,
                str(payload.get("classification") or "").strip() or None,
                _as_float(payload.get("radius_m")),
            )
        else:
            raise ValueError("action must be update, delete, delete_anchor or merge")
        self._notify_listeners()
        return {**result, "data": await self.async_get_places_data()}

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
                    segment_id = str(segment["id"])
                    rows[segment_id] = _panel_trip_row(segment, "saved")
                    if segment_id in self._place_prompts:
                        rows[segment_id]["place_question"] = deepcopy(
                            self._place_prompts[segment_id]
                        )

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
        self,
        segment_id: str,
        purpose: str,
        trip_type: str,
        start_address: Any = None,
        end_address: Any = None,
        distance_km: Any = None,
    ) -> dict[str, Any]:
        """Correct a persisted or unfinished trip from the sidebar panel."""
        async with self._resolution_lock:
            return await self._async_update_trip_locked(
                segment_id,
                purpose,
                trip_type,
                start_address,
                end_address,
                distance_km,
            )

    async def _async_update_trip_locked(
        self,
        segment_id: str,
        purpose: str,
        trip_type: str,
        start_address: Any = None,
        end_address: Any = None,
        distance_km: Any = None,
    ) -> dict[str, Any]:
        """Apply a panel correction while other classification choices are locked."""
        if trip_type not in {TRIP_TYPE_BUSINESS, TRIP_TYPE_PRIVATE}:
            raise ValueError("trip_type must be business or private")
        selected_purpose = normalize_trip_purpose(purpose, trip_type)
        validated_distance = _as_float(distance_km)
        if distance_km is not None and (
            validated_distance is None or validated_distance < 0
        ):
            raise ValueError("distance_km must be a finite non-negative number")

        runtime = self._find_runtime_segment(segment_id)
        if runtime is not None:
            if runtime.get("journey_role") == "transient_stop" and runtime.get(
                "continued_by_segment_id"
            ):
                destination = self._find_journey_destination(runtime.get("journey_id"))
                if destination is None:
                    raise ValueError(
                        "the continuing trip must end before this journey can be edited"
                    )
                runtime = destination
            if runtime is self._active or not runtime.get("ended_at"):
                raise ValueError("an active trip cannot be edited before it ends")
            runtime_id = str(runtime["id"])
            # This synchronous marker makes every still-running automatic
            # branch yield to the user's explicit choice after its next await.
            runtime["manual_resolution_requested_at"] = _iso_utc(
                datetime.now(UTC)
            )
            runtime["classification_prepared"] = True
            if (
                start_address is not None
                and str(start_address).strip()
                != str(runtime.get("start_address") or "").strip()
            ):
                runtime["start_address"] = str(start_address).strip()
                runtime["start_address_manual"] = True
            if (
                end_address is not None
                and str(end_address).strip()
                != str(runtime.get("end_address") or "").strip()
            ):
                runtime["end_address"] = str(end_address).strip()
                runtime["end_address_manual"] = True
            manual_distance = validated_distance
            current_distance = _as_float(runtime.get("distance_km"))
            if (
                manual_distance is not None
                and (
                    runtime.get("manual_distance_override")
                    or current_distance is None
                    or _whole_km(manual_distance) != _whole_km(current_distance)
                )
            ):
                if "distance_km_raw" not in runtime:
                    runtime["distance_km_raw"] = runtime.get("distance_km")
                runtime["distance_km"] = _whole_km(manual_distance)
                runtime["manual_distance_override"] = True
                runtime["distance_reconciliation_source"] = "manual_panel"
                runtime["odometer_ready"] = True
                runtime["odometer_wait_timed_out"] = False
                runtime["odometer_completion_source"] = "manual_panel"
                runtime["validation_error"] = None
                rollover_event = self._odometer_rollover_events.get(runtime_id)
                if rollover_event is not None:
                    rollover_event.set()
            if runtime.get("journey_role") == "transient_stop":
                self._transient.pop(runtime_id, None)
                runtime["journey_role"] = "destination"
                runtime["visit_role"] = "destination"
                stop = runtime.get("transient_stop")
                if isinstance(stop, dict):
                    stop["manually_resolved"] = True
            await self._async_finalize_segment(
                runtime,
                purpose=selected_purpose,
                trip_type=trip_type,
                source="manual_panel",
                learn_place=False,
            )
            await self._async_clear_classification_notification(segment_id)
            if runtime_id != segment_id:
                await self._async_clear_classification_notification(runtime_id)
            return {
                "updated": 1,
                "state": (
                    "saved" if runtime.get("persisted") else "waiting_odometer"
                ),
            }

        changed = await self.repository.async_update_trip(
            segment_id,
            selected_purpose,
            trip_type,
            start_address,
            end_address,
            distance_km,
        )
        if not changed:
            raise ValueError("trip segment was not found")
        prompt = self._place_prompts.get(segment_id)
        if prompt is not None:
            prompt["purpose"] = selected_purpose
            prompt["trip_type"] = trip_type
            prompt["trip_type_label"] = (
                "služební" if trip_type == TRIP_TYPE_BUSINESS else "soukromá"
            )
            if end_address is not None and str(end_address).strip():
                prompt["end_address"] = str(end_address).strip()
            prompt["suggested_label"] = place_label_suggestion(
                prompt, selected_purpose, trip_type
            )
            await self._async_save_runtime()
        await self._async_refresh_statistics()
        await self._async_clear_classification_notification(segment_id)
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
            if not isinstance(action, str) or not _PLACE_ACTION_PATTERN.match(action):
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
            if event.event_type == EVENT_SERVICE_REGISTERED and (
                self._pending or self._place_prompts
            ):
                self._create_task(
                    self._async_resend_unsent_notifications(),
                    f"{DOMAIN}_resend_pending_notifications",
                )

    async def _async_resend_unsent_notifications(self) -> None:
        """Deliver questions queued before the configured phone service existed."""
        for segment in list(self._pending.values()):
            if not segment.get("classification_ready") and not segment.get(
                "notification_sent_at"
            ):
                await self._async_schedule_classification_notification(segment)
        for prompt in list(self._place_prompts.values()):
            if not prompt.get("notification_sent_at"):
                await self._async_send_place_notification(prompt)

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
            self._settle_previous_destination_from_new_start(location, started_at)
            location = self._align_start_with_previous_destination(
                location, started_at
            )
            self._mark_previous_odometer_rollovers(started_at)
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
                "odometer_rollover_at": None,
                "start_odometer_km": start_odometer,
                "end_odometer_km": None,
                "distance_km": None,
                "start_odometer_updated_at": _iso_utc(odometer_updated_at),
                "start_odometer_source": "sensor_at_android_auto_connect",
                "start_address": location["address"],
                "end_address": None,
                "start_address_raw": location["address_raw"],
                "end_address_raw": None,
                "start_latitude": location["latitude"],
                "start_longitude": location["longitude"],
                "start_accuracy_m": location.get("accuracy_m"),
                "observed_start_address": location["address"],
                "observed_start_address_raw": location["address_raw"],
                "observed_start_latitude": location["latitude"],
                "observed_start_longitude": location["longitude"],
                "observed_start_accuracy_m": location.get("accuracy_m"),
                "start_location_source": location.get("coordinate_source"),
                "start_location_coordinate_updated_at": location.get(
                    "coordinate_updated_at"
                ),
                "end_latitude": None,
                "end_longitude": None,
                "end_accuracy_m": None,
                "purpose": None,
                "trip_type": None,
                "classification_source": None,
                "classification_prepared": False,
                "classification_ready": False,
                "persisted": False,
                "journey_role": None,
                "visit_role": None,
                "journey_inherited_from_segment_id": None,
                "transient_stop": None,
                "transient_continuation": None,
                "return_of_segment_id": None,
                "return_context": None,
                "private_return_context": None,
                "trip_context": None,
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
                "end_location_ready": False,
                "end_location_source": None,
                "end_location_captured_at": None,
                "end_location_initial_address": None,
                "end_location_initial_address_raw": None,
                "end_location_initial_latitude": None,
                "end_location_initial_longitude": None,
                "validation_error": (
                    None if start_odometer is not None else "missing_start_odometer"
                ),
            }
            self._link_transient_continuation(self._active)
            await self._async_save_runtime()
            await self._async_reconcile_previous_day_at_start(
                start_odometer, odometer_updated_at
            )
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
            segment["end_address_raw"] = location["address_raw"]
            segment["end_latitude"] = location["latitude"]
            segment["end_longitude"] = location["longitude"]
            segment["end_accuracy_m"] = location.get("accuracy_m")
            segment["end_location_initial_address"] = location["address"]
            segment["end_location_initial_address_raw"] = location["address_raw"]
            segment["end_location_initial_latitude"] = location["latitude"]
            segment["end_location_initial_longitude"] = location["longitude"]
            segment["end_location_initial_accuracy_m"] = location.get("accuracy_m")
            segment["end_location_coordinate_updated_at"] = location.get(
                "coordinate_updated_at"
            )
            segment["end_location_captured_at"] = _iso_utc(datetime.now(UTC))
            segment["end_location_source"] = "android_auto_disconnect"
            segment["end_location_ready"] = self.location_settle_seconds <= 0
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
        """Wait for a trustworthy boundary without crossing into the next trip."""
        async with self._odometer_completion_condition:
            await self._odometer_completion_condition.wait_for(
                lambda: not self._has_earlier_odometer_wait(segment)
            )
            if segment.get("odometer_ready"):
                self._odometer_completion_condition.notify_all()
                return

            manual_distance = (
                _as_float(segment.get("distance_km"))
                if segment.get("manual_distance_override")
                else None
            )
            disconnected_at = (
                _parse_datetime(segment.get("ended_at")) or datetime.now(UTC)
            )
            start_odometer = _as_float(segment.get("start_odometer_km"))
            end_odometer, odometer_updated_at, _, completion_source = (
                await self._async_wait_for_odometer(
                    segment, disconnected_at, start_odometer
                )
            )
            if segment.get("odometer_ready") and segment.get(
                "manual_distance_override"
            ):
                self._odometer_completion_condition.notify_all()
                return
            segment["odometer_updated_at"] = _iso_utc(odometer_updated_at)
            segment["odometer_wait_timed_out"] = False
            segment["odometer_completion_source"] = completion_source
            segment["end_odometer_km"] = end_odometer
            # Kept for raw-data compatibility. A delayed cloud update is now a
            # real boundary and is propagated to the next segment instead.
            segment["odometer_shared_update"] = False

            raw_distance: float | None = None
            gps_fallback = completion_source == "gps_fallback_next_trip_started"
            if gps_fallback:
                raw_distance = _segment_gps_distance_km(segment)
                segment["distance_hint_km"] = raw_distance
                segment["distance_km"] = (
                    _whole_km(raw_distance) if raw_distance is not None else None
                )
                segment["validation_error"] = (
                    None if raw_distance is not None else "missing_gps_distance"
                )
            elif start_odometer is None or end_odometer is None:
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
                raw_distance = round(max(0.0, end_odometer - start_odometer), 3)
                segment["distance_km"] = _whole_km(raw_distance)
                segment["validation_error"] = None
            segment["distance_km_raw"] = raw_distance
            if manual_distance is not None:
                segment["distance_km"] = _whole_km(manual_distance)
                segment["distance_reconciliation_source"] = "manual_panel"
            else:
                segment["distance_reconciliation_source"] = (
                    "gps_fallback_next_trip_started"
                    if gps_fallback
                    else "direct_odometer_difference"
                )

            segment["odometer_ready"] = True
            await self._async_apply_previous_final_to_next_segment(segment)
            await self._async_save_runtime()
            self._odometer_completion_condition.notify_all()
        await self._async_try_finalize_segment(segment)
        await self._async_try_finalize_journey_destination(segment.get("journey_id"))

    def _has_earlier_odometer_wait(self, segment: dict[str, Any]) -> bool:
        """Keep overlapping cloud updates assigned in chronological trip order."""
        current_key = (
            str(segment.get("started_at") or ""),
            str(segment.get("id") or ""),
        )
        for candidate in [
            *self._closing.values(),
            *self._pending.values(),
            *self._transient.values(),
        ]:
            if candidate is segment or candidate.get("odometer_ready"):
                continue
            candidate_key = (
                str(candidate.get("started_at") or ""),
                str(candidate.get("id") or ""),
            )
            if candidate_key < current_key:
                return True
        return False

    def _mark_previous_odometer_rollovers(self, started_at: datetime) -> None:
        """Stop older waiters from consuming a counter update from this trip."""
        for candidate in [
            *self._closing.values(),
            *self._pending.values(),
            *self._transient.values(),
        ]:
            if candidate.get("odometer_ready"):
                continue
            candidate_started_at = _parse_datetime(candidate.get("started_at"))
            candidate_ended_at = _parse_datetime(candidate.get("ended_at"))
            if (
                candidate_started_at is None
                or candidate_ended_at is None
                or candidate_started_at >= started_at
                or candidate_ended_at > started_at
            ):
                continue
            rollover_at = _parse_datetime(candidate.get("odometer_rollover_at"))
            if rollover_at is None or started_at < rollover_at:
                candidate["odometer_rollover_at"] = _iso_utc(started_at)
            event = self._odometer_rollover_events.get(
                str(candidate.get("id") or "")
            )
            if event is not None:
                event.set()

    async def _async_reconcile_previous_day_at_start(
        self,
        start_odometer: float | None,
        odometer_updated_at: datetime | None,
    ) -> None:
        """Use a fresh next-start odometer as an exact boundary for earlier legs."""
        previous = self._statistics.get("last_segment")
        if not isinstance(previous, dict) or start_odometer is None:
            return
        previous_start = _as_float(previous.get("start_odometer_km"))
        previous_end_time = _parse_datetime(previous.get("ended_at"))
        if (
            previous_start is None
            or start_odometer <= previous_start + 0.001
            or odometer_updated_at is None
            or previous_end_time is None
            or odometer_updated_at <= previous_end_time
        ):
            return
        previous_date = str(previous.get("date") or "")
        current = self._active
        if (
            not previous_date
            or not isinstance(current, dict)
            or parking_boundary_details(
                previous,
                current,
                self.return_context_hours * 60,
                self.transient_radius,
            )
            is None
        ):
            return
        await self.repository.async_reconcile_day(previous_date, start_odometer)
        await self._async_refresh_statistics()

    async def _async_settle_end_location(self, segment: dict[str, Any]) -> None:
        """Wait for the phone position and then capture the authoritative endpoint."""
        if segment.get("end_location_ready"):
            return
        ended_at = _parse_datetime(segment.get("ended_at")) or datetime.now(UTC)
        deadline = ended_at + timedelta(seconds=self.location_settle_seconds)
        if (
            not segment.get("location_update_requested_at")
            and datetime.now(UTC) <= deadline
        ):
            await self._async_request_phone_location_update(segment)
        if (datetime.now(UTC) - deadline).total_seconds() > 15:
            initial_location = {
                "address": segment.get("end_location_initial_address")
                or segment.get("end_address"),
                "address_raw": segment.get("end_location_initial_address_raw")
                or segment.get("end_address_raw")
                or segment.get("end_address"),
                "latitude": segment.get("end_location_initial_latitude")
                if segment.get("end_location_initial_latitude") is not None
                else segment.get("end_latitude"),
                "longitude": segment.get("end_location_initial_longitude")
                if segment.get("end_location_initial_longitude") is not None
                else segment.get("end_longitude"),
                "accuracy_m": segment.get("end_location_initial_accuracy_m")
                if segment.get("end_location_initial_accuracy_m") is not None
                else segment.get("end_accuracy_m"),
                "coordinate_updated_at": segment.get(
                    "end_location_coordinate_updated_at"
                ),
            }
            self._set_segment_end_location(
                segment, initial_location, "restart_initial_location_fallback"
            )
            await self._async_apply_previous_destination_to_new_active(segment)
            await self._async_save_runtime()
            return
        while not segment.get("end_location_ready"):
            remaining = (deadline - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                break
            await asyncio.sleep(min(remaining, 1.0))
        if segment.get("end_location_ready"):
            return
        location = self._capture_location()
        coordinate_updated_at = _parse_datetime(
            location.get("coordinate_updated_at")
        )
        if location_is_fresh(coordinate_updated_at, ended_at):
            self._set_segment_end_location(
                segment, location, "settled_phone_location"
            )
        else:
            initial_location = {
                "address": segment.get("end_location_initial_address")
                or segment.get("end_address"),
                "address_raw": segment.get("end_location_initial_address_raw")
                or segment.get("end_address_raw")
                or segment.get("end_address"),
                "latitude": segment.get("end_location_initial_latitude"),
                "longitude": segment.get("end_location_initial_longitude"),
                "accuracy_m": segment.get("end_location_initial_accuracy_m"),
                "coordinate_updated_at": segment.get(
                    "end_location_coordinate_updated_at"
                ),
            }
            self._set_segment_end_location(
                segment, initial_location, "stale_settled_location_rejected"
            )
        await self._async_apply_previous_destination_to_new_active(segment)
        await self._async_save_runtime()

    async def _async_request_phone_location_update(
        self, segment: dict[str, Any]
    ) -> None:
        """Ask the Companion app for a fresh location before the settling delay."""
        if not self.hass.services.has_service("notify", self.notify_service):
            return
        try:
            await self.hass.services.async_call(
                "notify",
                self.notify_service,
                {"message": "request_location_update"},
                blocking=True,
            )
            segment["location_update_requested_at"] = _iso_utc(datetime.now(UTC))
        except Exception:  # noqa: BLE001 - timed capture remains available
            _LOGGER.exception(
                "Could not request a phone location update for segment %s",
                segment.get("id"),
            )

    @staticmethod
    def _set_segment_end_location(
        segment: dict[str, Any],
        location: dict[str, Any],
        source: str,
    ) -> None:
        """Apply one final endpoint consistently to a segment."""
        selected = merge_location_snapshot(
            location,
            {
                "address": segment.get("end_address"),
                "address_raw": segment.get("end_address_raw"),
                "latitude": segment.get("end_latitude"),
                "longitude": segment.get("end_longitude"),
                "accuracy_m": segment.get("end_accuracy_m"),
                "coordinate_updated_at": segment.get(
                    "end_location_coordinate_updated_at"
                ),
            },
        )
        if not segment.get("end_address_manual"):
            segment["end_address"] = selected.get("address")
            segment["end_address_raw"] = selected.get("address_raw")
        segment["end_latitude"] = selected.get("latitude")
        segment["end_longitude"] = selected.get("longitude")
        segment["end_accuracy_m"] = selected.get("accuracy_m")
        segment["end_location_coordinate_updated_at"] = selected.get(
            "coordinate_updated_at"
        )
        segment["end_location_ready"] = True
        segment["end_location_source"] = source
        segment["end_location_captured_at"] = _iso_utc(datetime.now(UTC))

    def _settle_previous_destination_from_new_start(
        self, location: dict[str, Any], started_at: datetime
    ) -> None:
        """Use a reconnect location as the endpoint of a just-finished segment."""
        maximum_gap = self.location_settle_seconds + 30.0
        coordinate_fresh = location_is_fresh(
            _parse_datetime(location.get("coordinate_updated_at")),
            started_at,
            maximum_gap,
        )
        address_fresh = location_is_fresh(
            _parse_datetime(location.get("address_updated_at")),
            started_at,
            maximum_gap,
        )
        if not coordinate_fresh and not address_fresh:
            return
        settled_location = location.copy()
        if not coordinate_fresh:
            for key in ("latitude", "longitude", "accuracy_m", "coordinate_updated_at"):
                settled_location[key] = None
        if not address_fresh:
            settled_location["address"] = None
            settled_location["address_raw"] = None
        for segment in self._closing.values():
            if segment.get("end_location_ready"):
                continue
            ended_at = _parse_datetime(segment.get("ended_at"))
            if ended_at is None:
                continue
            gap = (_ensure_utc(started_at) - ended_at).total_seconds()
            if 0 <= gap <= maximum_gap:
                self._set_segment_end_location(
                    segment, settled_location, "next_segment_start"
                )

    def _align_start_with_previous_destination(
        self, location: dict[str, Any], started_at: datetime
    ) -> dict[str, Any]:
        """Make a continuing segment start exactly at the previous endpoint."""
        candidates = [
            *self._closing.values(),
            *self._pending.values(),
            *self._transient.values(),
        ]
        last_persisted = self._statistics.get("last_segment")
        if isinstance(last_persisted, dict):
            candidates.append(last_persisted)
        candidates.sort(key=lambda item: str(item.get("ended_at") or ""), reverse=True)
        for previous in candidates:
            ended_at = _parse_datetime(previous.get("ended_at"))
            if ended_at is None:
                continue
            gap = (_ensure_utc(started_at) - ended_at).total_seconds()
            if gap < 0 or gap > self.return_context_hours * 3600:
                continue
            distance = coordinate_distance_m(
                location.get("latitude"),
                location.get("longitude"),
                previous.get("end_latitude"),
                previous.get("end_longitude"),
            )
            current_accuracy = _as_float(location.get("accuracy_m"))
            previous_accuracy = _as_float(previous.get("end_accuracy_m"))
            coordinate_reliable = bool(
                distance is not None
                and all(
                    value is None or value < 0 or value <= self.transient_radius
                    for value in (current_accuracy, previous_accuracy)
                )
            )
            addresses_match = bool(
                str(location.get("address") or "").strip()
                and str(location.get("address") or "").strip().casefold()
                == str(previous.get("end_address") or "").strip().casefold()
            )
            if coordinate_reliable and distance is not None:
                if distance > self.transient_radius:
                    continue
            elif not addresses_match:
                continue
            previous_snapshot = {
                "address": previous.get("end_address") or location.get("address"),
                "address_raw": previous.get("end_address_raw")
                or previous.get("end_address")
                or location.get("address_raw"),
                "latitude": previous.get("end_latitude"),
                "longitude": previous.get("end_longitude"),
                "accuracy_m": previous.get("end_accuracy_m"),
                "coordinate_updated_at": previous.get(
                    "end_location_coordinate_updated_at"
                ),
            }
            return {
                **merge_location_snapshot(previous_snapshot, location),
                "alignment_source": previous.get("id"),
            }
        return location

    async def _async_apply_previous_destination_to_new_active(
        self, finished_segment: dict[str, Any]
    ) -> None:
        """Correct an already-started next segment after endpoint settling."""
        async with self._transition_lock:
            active = self._active
            if active is None:
                return
            active_started = _parse_datetime(active.get("started_at"))
            finished_at = _parse_datetime(finished_segment.get("ended_at"))
            if (
                active_started is None
                or finished_at is None
                or active_started < finished_at
            ):
                return
            gap = (active_started - finished_at).total_seconds()
            if gap > self.location_settle_seconds + 30.0:
                return
            distance = coordinate_distance_m(
                active.get("start_latitude"),
                active.get("start_longitude"),
                finished_segment.get("end_latitude"),
                finished_segment.get("end_longitude"),
            )
            active_accuracy = _as_float(active.get("start_accuracy_m"))
            finished_accuracy = _as_float(finished_segment.get("end_accuracy_m"))
            coordinate_reliable = bool(
                distance is not None
                and all(
                    value is None or value < 0 or value <= self.transient_radius
                    for value in (active_accuracy, finished_accuracy)
                )
            )
            addresses_match = bool(
                str(active.get("start_address") or "").strip()
                and str(active.get("start_address") or "").strip().casefold()
                == str(finished_segment.get("end_address") or "").strip().casefold()
            )
            if coordinate_reliable and distance is not None:
                if distance > self.transient_radius:
                    return
            elif not addresses_match:
                return
            selected = merge_location_snapshot(
                {
                    "address": finished_segment.get("end_address"),
                    "address_raw": finished_segment.get("end_address_raw")
                    or finished_segment.get("end_address"),
                    "latitude": finished_segment.get("end_latitude"),
                    "longitude": finished_segment.get("end_longitude"),
                    "accuracy_m": finished_segment.get("end_accuracy_m"),
                    "coordinate_updated_at": finished_segment.get(
                        "end_location_coordinate_updated_at"
                    ),
                },
                {
                    "address": active.get("start_address"),
                    "address_raw": active.get("start_address_raw"),
                    "latitude": active.get("start_latitude"),
                    "longitude": active.get("start_longitude"),
                    "accuracy_m": active.get("start_accuracy_m"),
                    "coordinate_updated_at": active.get(
                        "start_location_coordinate_updated_at"
                    ),
                },
            )
            active["start_address"] = selected.get("address")
            active["start_address_raw"] = selected.get("address_raw")
            active["start_latitude"] = selected.get("latitude")
            active["start_longitude"] = selected.get("longitude")
            active["start_accuracy_m"] = selected.get("accuracy_m")
            active["start_location_coordinate_updated_at"] = selected.get(
                "coordinate_updated_at"
            )
            active["start_location_source"] = "previous_segment_final"

    async def _async_prepare_classification(
        self, segment: dict[str, Any]
    ) -> None:
        """Settle and resolve the destination independently of the odometer."""
        if segment.get("manual_resolution_requested_at"):
            return
        await self._async_settle_end_location(segment)
        if segment.get("manual_resolution_requested_at"):
            return
        if _address_is_coordinate_fallback(segment.get("start_address")):
            learned_start = await self.repository.async_find_place(
                _as_float(segment.get("start_latitude")),
                _as_float(segment.get("start_longitude")),
                _as_text(segment.get("start_address")),
                self.client_radius,
                self.private_radius,
                self.transient_radius,
                accuracy_m=_as_float(segment.get("start_accuracy_m")),
            )
            if learned_start is not None and learned_start.get("matched_address"):
                segment["start_address_raw"] = learned_start["matched_address"]
                segment["start_address"] = (
                    shorten_address(learned_start["matched_address"])
                    or learned_start["matched_address"]
                )
            else:
                start_map_result = await self.geocoder.async_reverse(
                    _as_float(segment.get("start_latitude")),
                    _as_float(segment.get("start_longitude")),
                )
                if start_map_result is not None and start_map_result.get("display_name"):
                    segment["start_address_raw"] = start_map_result["display_name"]
                    segment["start_address"] = (
                        shorten_address(start_map_result["display_name"])
                        or start_map_result["display_name"]
                    )

        if segment.get("manual_resolution_requested_at"):
            return

        trip_context = infer_trip_context(
            segment,
            self._statistics.get("last_segment"),
            self.return_context_hours,
            self.transient_radius,
        )
        if trip_context is not None:
            segment["trip_context"] = trip_context
        segment["return_context"] = (
            (
                trip_context
                if isinstance(trip_context, dict)
                and trip_context.get("previous_trip_type") == TRIP_TYPE_BUSINESS
                else None
            )
            or segment.get("return_context")
            or self._journey_return_context(segment)
        )
        segment["private_return_context"] = (
            trip_context
            if isinstance(trip_context, dict)
            and trip_context.get("previous_trip_type") == TRIP_TYPE_PRIVATE
            else segment.get("private_return_context")
        )
        if await self._async_handle_configured_destination(segment):
            return
        learned_place = await self.repository.async_find_place(
            _as_float(segment.get("end_latitude")),
            _as_float(segment.get("end_longitude")),
            _as_text(segment.get("end_address")),
            self.client_radius,
            self.private_radius,
            self.transient_radius,
            accuracy_m=_as_float(segment.get("end_accuracy_m")),
        )
        if segment.get("manual_resolution_requested_at"):
            return
        if learned_place is not None:
            segment["map_estimate"] = learned_place.get("map_name") or learned_place.get(
                "label"
            )
            segment["matched_place_id"] = learned_place.get("id")
            segment["matched_place_distance_m"] = learned_place.get(
                "match_distance_m"
            )
            segment["matched_place_radius_m"] = learned_place.get("match_radius_m")
            segment["matched_place_method"] = learned_place.get("match_method")
            segment["return_destination_label"] = learned_place.get("label")
            if _address_is_coordinate_fallback(
                segment.get("end_address")
            ) and learned_place.get("matched_address"):
                segment["end_address_raw"] = learned_place["matched_address"]
                segment["end_address"] = (
                    shorten_address(learned_place["matched_address"])
                    or learned_place["matched_address"]
                )
            place_role = learned_place.get("place_role")
            trip_types = place_trip_types(learned_place)
            behavior = learned_place_behavior(
                learned_place, bool(segment.get("return_context"))
            )
            segment["matched_place_role"] = place_role
            segment["known_place_trip_types"] = trip_types
            if behavior == "transient":
                segment["transient_stop"] = {
                    "detected": True,
                    "kind": learned_place.get("transient_kind") or "learned",
                    "name": str(learned_place.get("label") or "Mezizastávka"),
                    "detection_source": (
                        "learned_place"
                        if place_role == PLACE_ROLE_TRANSIENT
                        else "learned_contextual_place"
                    ),
                    "default_trip_types": trip_types,
                }
                await self._async_hold_transient(segment)
                return
            if behavior == "private":
                await self._async_finalize_segment(
                    segment,
                    purpose="Soukromá",
                    trip_type=TRIP_TYPE_PRIVATE,
                    source="learned_private_place",
                    learn_place=False,
                )
                return
            if behavior == "confirm":
                segment["known_place_exception"] = True
                await self._async_queue_pending(segment)
                return
            if behavior == "return" and segment.get("return_context"):
                await self._async_finalize_return(
                    segment,
                    source="learned_return_context",
                    learn_place=False,
                )
                return
            if (
                behavior == "return"
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
                purpose=(
                    "Soukromá"
                    if learned_trip_type == TRIP_TYPE_PRIVATE
                    else str(learned_place.get("label") or "Neznámý zákazník")
                ),
                trip_type=learned_trip_type,
                source="learned_place",
                learn_place=False,
            )
            return

        # Known/configured places must win independently of network timing. Only
        # an otherwise unknown destination can become an implicit quick stop.
        short_continuation = self._find_runtime_continuation(
            segment, _IMPLICIT_TRANSIENT_STOP_MINUTES
        )
        if short_continuation is not None:
            segment["transient_stop"] = {
                "detected": True,
                "kind": "very_short_stop",
                "name": str(segment.get("end_address") or "Krátká mezizastávka"),
                "detection_source": "very_short_continuation",
                "max_duration_minutes": _IMPLICIT_TRANSIENT_STOP_MINUTES,
            }
            await self._async_hold_transient(segment)
            return

        candidates, map_result = await self._async_discover_destination(segment)

        if segment.get("manual_resolution_requested_at"):
            return

        if await self._async_handle_configured_destination(segment):
            return

        transient_stop = (
            detect_transient_stop(map_result, candidates)
            if gps_accuracy_suitable(segment, self.transient_radius)
            else None
        )
        if map_result is not None and not gps_accuracy_suitable(
            segment, self.transient_radius
        ):
            segment["transient_detection_skipped_reason"] = "gps_accuracy"
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

    async def _async_retry_pending_suggestions(
        self, segment: dict[str, Any]
    ) -> None:
        """Repair one pre-fix pending segment from its stored endpoint."""
        await self._async_discover_destination(segment)
        await self._async_save_runtime()
        if str(segment.get("id")) in self._pending and not segment.get(
            "classification_ready"
        ):
            await self._async_schedule_classification_notification(segment)

    async def _async_discover_destination(
        self, segment: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        """Search and audit map candidates around a stored trip endpoint."""
        end_latitude = _as_float(segment.get("end_latitude"))
        end_longitude = _as_float(segment.get("end_longitude"))
        if not gps_accuracy_suitable(segment, self.client_radius):
            segment["map_candidates"] = []
            segment["candidate_search_status"] = "skipped_inaccurate_gps"
            segment["candidate_search_attempts"] = 0
            segment["candidate_search_cache_hit"] = False
            segment["candidate_search_error"] = None
            segment["candidate_search_radius_m"] = self.institution_search_radius
            segment["candidate_search_completed_at"] = _iso_utc(datetime.now(UTC))
            segment["candidate_search_coordinates"] = {
                "latitude": end_latitude,
                "longitude": end_longitude,
            }
            segment["candidate_count"] = 0
            return [], None
        search_response, map_result = await asyncio.gather(
            self.institution_searcher.async_search_with_diagnostics(
                end_latitude,
                end_longitude,
                self.institution_search_radius,
            ),
            self.geocoder.async_reverse(end_latitude, end_longitude),
        )
        candidates, search_result = search_response
        segment["map_candidates"] = candidates
        segment["candidate_search_status"] = search_result.get("status")
        segment["candidate_search_attempts"] = search_result.get("attempts")
        segment["candidate_search_cache_hit"] = bool(search_result.get("cache_hit"))
        segment["candidate_search_error"] = search_result.get("error")
        segment["candidate_search_radius_m"] = self.institution_search_radius
        segment["candidate_search_completed_at"] = _iso_utc(datetime.now(UTC))
        segment["candidate_search_coordinates"] = {
            "latitude": end_latitude,
            "longitude": end_longitude,
        }
        segment["candidate_count"] = len(candidates)
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
            if _address_is_coordinate_fallback(
                segment.get("end_address")
            ) and map_result.get("display_name"):
                segment["end_address_raw"] = map_result["display_name"]
                segment["end_address"] = (
                    shorten_address(map_result["display_name"])
                    or map_result["display_name"]
                )
        return candidates, map_result

    async def _async_handle_configured_destination(
        self, segment: dict[str, Any]
    ) -> bool:
        """Apply the configured home/company rules to a completed destination."""
        if segment.get("manual_resolution_requested_at"):
            return True
        addresses = (segment.get("end_address"), segment.get("map_address"))
        home_match = configured_place_match(
            segment.get("end_latitude"),
            segment.get("end_longitude"),
            addresses,
            self.home_address,
            self.home_latitude,
            self.home_longitude,
            self.home_radius,
            accuracy_m=_as_float(segment.get("end_accuracy_m")),
        )
        if home_match is not None:
            segment["configured_place"] = "home"
            segment["configured_place_match"] = home_match
            segment["configured_place_radius_m"] = self.home_radius
            segment["map_estimate"] = "Domov"
            segment["return_destination_label"] = "Domov"
            if segment.get("return_context"):
                await self._async_finalize_return(
                    segment,
                    source="configured_home_return",
                    learn_place=True,
                )
                return True
            if segment.get("private_return_context"):
                segment["journey_role"] = "return"
                await self._async_finalize_segment(
                    segment,
                    purpose="Soukromá",
                    trip_type=TRIP_TYPE_PRIVATE,
                    source="configured_home_private_return",
                    learn_place=False,
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
            self.company_radius,
            accuracy_m=_as_float(segment.get("end_accuracy_m")),
        )
        if company_match is not None:
            label = self.company_label or "Firma"
            segment["configured_place"] = "company"
            segment["configured_place_match"] = company_match
            segment["configured_place_radius_m"] = self.company_radius
            segment["map_estimate"] = label
            segment["return_destination_label"] = label
            if segment.get("return_context"):
                await self._async_finalize_return(
                    segment,
                    source="configured_company_return",
                    learn_place=False,
                )
                return True
            if segment.get("private_return_context"):
                segment["journey_role"] = "return"
                await self._async_finalize_segment(
                    segment,
                    purpose="Soukromá",
                    trip_type=TRIP_TYPE_PRIVATE,
                    source="configured_company_private_return",
                    learn_place=False,
                )
                return True
            segment["return_context"] = {
                "suggested": True,
                "reason": "configured_company_destination",
                "previous_segment_id": None,
                "previous_purpose": None,
            }
            await self._async_queue_pending(segment)
            return True
        return False

    async def _async_queue_pending(self, segment: dict[str, Any]) -> None:
        """Move a completed segment to the notification decision queue."""
        if segment.get("manual_resolution_requested_at"):
            return
        segment_id = str(segment["id"])
        segment["classification_prepared"] = True
        self._closing.pop(segment_id, None)
        self._transient.pop(segment_id, None)
        segment.setdefault("pending_since", _iso_utc(datetime.now(UTC)))
        self._pending[segment_id] = segment
        await self._async_save_runtime()
        await self._async_schedule_classification_notification(segment)
        self._schedule_pending_review(segment)

    def _schedule_pending_review(self, segment: dict[str, Any]) -> None:
        """Schedule safe closure only for an expired likely intermediate stop."""
        stop = segment.get("transient_stop")
        if not isinstance(stop, dict) or not stop.get("expired"):
            return
        segment_id = str(segment.get("id") or "")
        if not segment_id:
            return
        self._create_task(
            self._async_auto_resolve_pending_stop(segment_id),
            f"{DOMAIN}_review_pending_{segment_id}",
        )

    async def _async_auto_resolve_pending_stop(self, segment_id: str) -> None:
        """Persist an ignored short stop as unclassified instead of waiting forever."""
        while True:
            segment = self._pending.get(segment_id)
            if segment is None:
                return
            pending_since = _parse_datetime(segment.get("pending_since"))
            if pending_since is None:
                pending_since = datetime.now(UTC)
                segment["pending_since"] = _iso_utc(pending_since)
                await self._async_save_runtime()
            deadline = pending_since + timedelta(hours=self.pending_review_hours)
            remaining = (deadline - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                break
            await asyncio.sleep(min(remaining, 60.0))

        async with self._resolution_lock:
            segment = self._pending.get(segment_id)
            stop = segment.get("transient_stop") if segment is not None else None
            if segment is None or not isinstance(stop, dict) or not stop.get("expired"):
                return
            segment["needs_review"] = True
            segment["review_reason"] = "ignored_expired_transient_stop"
            segment["review_created_at"] = _iso_utc(datetime.now(UTC))
            segment["journey_role"] = "destination"
            segment["visit_role"] = "destination"
            await self._async_finalize_segment(
                segment,
                purpose=str(
                    segment.get("map_estimate")
                    or segment.get("end_address")
                    or "Nevyřešená krátká zastávka"
                ),
                trip_type=TRIP_TYPE_UNCLASSIFIED,
                source="transient_auto_review",
                learn_place=False,
            )
            await self._async_clear_classification_notification(segment_id)
            self._notify_listeners()

    async def _async_hold_transient(self, segment: dict[str, Any]) -> None:
        """Hold a likely intermediate stop until the whole journey is known."""
        if segment.get("manual_resolution_requested_at"):
            return
        segment_id = str(segment["id"])
        segment["journey_id"] = segment.get("journey_id") or uuid4().hex
        segment["journey_role"] = "transient_stop"
        segment["visit_role"] = "waypoint_candidate"
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
        segment["visit_role"] = "destination"
        if (
            isinstance(stop, dict)
            and stop.get("detection_source") == "learned_contextual_place"
            and stop.get("default_trip_types") == [TRIP_TYPE_PRIVATE]
        ):
            await self._async_finalize_segment(
                segment,
                purpose="Soukromá",
                trip_type=TRIP_TYPE_PRIVATE,
                source="learned_private_after_transient_wait",
                learn_place=False,
            )
            return
        await self._async_queue_pending(segment)

    def _find_runtime_continuation(
        self,
        transient: dict[str, Any],
        max_gap_minutes: float | None = None,
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
            if (
                self._continuation_details(
                    transient, current, max_gap_minutes=max_gap_minutes
                )
                is not None
            ):
                return current
        return None

    def _link_transient_continuation(
        self,
        current: dict[str, Any],
        preferred: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Attach a new segment to the latest compatible intermediate stop."""
        candidates = [preferred] if preferred is not None else [
            *self._transient.values(),
            *(
                item
                for item in self._pending.values()
                if self._pending_can_be_implicit_transient(item)
            ),
        ]
        candidates = [
            item
            for item in candidates
            if isinstance(item, dict) and not item.get("continued_by_segment_id")
        ]
        candidates.sort(
            key=lambda item: str(item.get("ended_at") or ""), reverse=True
        )
        for transient in candidates:
            transient_id = str(transient.get("id") or "")
            was_pending = self._pending.get(transient_id) is transient
            details = self._continuation_details(
                transient,
                current,
                max_gap_minutes=(
                    _IMPLICIT_TRANSIENT_STOP_MINUTES if was_pending else None
                ),
            )
            if details is None:
                continue
            if was_pending:
                self._pending.pop(transient_id, None)
                transient["journey_role"] = "transient_stop"
                transient["visit_role"] = "waypoint"
                transient["transient_stop"] = {
                    "detected": True,
                    "kind": "very_short_stop",
                    "name": str(
                        transient.get("end_address") or "Krátká mezizastávka"
                    ),
                    "detection_source": "pending_reconsidered_on_continuation",
                    "max_duration_minutes": _IMPLICIT_TRANSIENT_STOP_MINUTES,
                }
                transient["notification_superseded_by_continuation"] = True
                transient.pop("notification_suppressed_reason", None)
                transient["notification_task_scheduled"] = False
                self._transient[transient_id] = transient
                if transient.get("notification_sent_at"):
                    self._create_task(
                        self._async_clear_classification_notification(transient_id),
                        f"{DOMAIN}_clear_short_stop_notification_{transient_id}",
                    )
            current["journey_id"] = transient.get("journey_id") or uuid4().hex
            current["transient_continuation"] = details
            current["return_context"] = (
                current.get("return_context") or transient.get("return_context")
            )
            transient["continued_by_segment_id"] = current.get("id")
            transient["continuation"] = details
            transient["visit_role"] = "waypoint"
            return transient
        return None

    @staticmethod
    def _pending_can_be_implicit_transient(segment: dict[str, Any]) -> bool:
        """Limit retroactive quick-stop promotion to genuinely unknown places."""
        return bool(
            not segment.get("classification_ready")
            and not segment.get("persisted")
            and not segment.get("configured_place")
            and not segment.get("known_place_exception")
            and not segment.get("matched_place_id")
        )

    def _continuation_details(
        self,
        transient: dict[str, Any],
        current: dict[str, Any],
        max_gap_minutes: float | None = None,
    ) -> dict[str, Any] | None:
        """Apply configured limits to a possible journey continuation."""
        return continuation_details(
            transient,
            current,
            (
                self.transient_stop_minutes
                if max_gap_minutes is None
                else max_gap_minutes
            ),
            self.transient_radius,
        )

    def _journey_return_context(
        self, segment: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Carry return evidence across a confirmed service or rest stop."""
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
        self,
        segment: dict[str, Any],
        disconnected_at: datetime,
        start_odometer: float | None,
    ) -> tuple[float | None, datetime | None, bool, str]:
        """Wait for a counter boundary, but never take one from a later trip."""
        segment_id = str(segment.get("id") or "")

        def _signal_for(state: State | None) -> str | None:
            return odometer_update_signal(
                disconnected_at,
                start_odometer,
                _odometer_updated_at(state),
                _odometer_value(state),
                _parse_datetime(segment.get("odometer_rollover_at")),
            )

        current_state = self.hass.states.get(self.odometer_entity)
        current_updated_at = _odometer_updated_at(current_state)
        current_value = _odometer_value(current_state)
        current_signal = _signal_for(current_state)
        if current_signal is not None:
            return current_value, current_updated_at, False, current_signal
        if _parse_datetime(segment.get("odometer_rollover_at")) is not None:
            return None, None, False, "gps_fallback_next_trip_started"

        loop = asyncio.get_running_loop()
        future: asyncio.Future[State] = loop.create_future()
        rollover_event = asyncio.Event()
        self._odometer_rollover_events[segment_id] = rollover_event

        @callback
        def _odometer_changed(event: Event) -> None:
            new_state: State | None = event.data.get("new_state")
            signal = _signal_for(new_state)
            if (
                new_state is not None
                and signal is not None
                and not future.done()
            ):
                future.set_result(new_state)

        unsubscribe = async_track_state_change_event(
            self.hass, [self.odometer_entity], _odometer_changed
        )
        rollover_task: asyncio.Task[bool] | None = None
        try:
            # Close the race between the first check and listener registration.
            current_state = self.hass.states.get(self.odometer_entity)
            current_updated_at = _odometer_updated_at(current_state)
            current_value = _odometer_value(current_state)
            current_signal = _signal_for(current_state)
            if (
                current_state is not None
                and current_signal is not None
                and not future.done()
            ):
                future.set_result(current_state)
            if _parse_datetime(segment.get("odometer_rollover_at")) is not None:
                rollover_event.set()

            rollover_task = asyncio.create_task(rollover_event.wait())
            await asyncio.wait(
                {future, rollover_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if future.done() and not future.cancelled():
                final_state = future.result()
                final_signal = _signal_for(final_state)
                if final_signal is not None:
                    return (
                        _odometer_value(final_state),
                        _odometer_updated_at(final_state),
                        False,
                        final_signal,
                    )
            return None, None, False, "gps_fallback_next_trip_started"
        finally:
            unsubscribe()
            if not future.done():
                future.cancel()
            if rollover_task is not None and not rollover_task.done():
                rollover_task.cancel()
            if self._odometer_rollover_events.get(segment_id) is rollover_event:
                self._odometer_rollover_events.pop(segment_id, None)

    async def _async_apply_previous_final_to_next_segment(
        self, finished_segment: dict[str, Any]
    ) -> None:
        """Use a delayed final counter as the boundary of the next runtime trip."""
        async with self._transition_lock:
            finished_at = _parse_datetime(finished_segment.get("ended_at"))
            final_odometer = _as_float(finished_segment.get("end_odometer_km"))
            final_odo_at = _parse_datetime(finished_segment.get("odometer_updated_at"))
            if finished_at is None or final_odometer is None:
                return

            candidates = [
                *([self._active] if self._active is not None else []),
                *self._closing.values(),
                *self._pending.values(),
                *self._transient.values(),
            ]
            following = []
            for candidate in candidates:
                if (
                    candidate is finished_segment
                    or candidate.get("persisted")
                    or candidate.get("odometer_ready")
                ):
                    continue
                started_at = _parse_datetime(candidate.get("started_at"))
                if started_at is not None and started_at >= finished_at:
                    following.append((started_at, candidate))
            if not following:
                return

            _, next_segment = min(
                following,
                key=lambda item: (item[0], str(item[1].get("id") or "")),
            )
            corrected_start = propagated_start_odometer(
                finished_at,
                final_odometer,
                _parse_datetime(next_segment.get("started_at")),
                _as_float(next_segment.get("start_odometer_km")),
            )
            if corrected_start is None:
                return

            next_segment["start_odometer_km"] = corrected_start
            next_segment["start_odometer_updated_at"] = _iso_utc(final_odo_at)
            next_segment["start_odometer_source"] = "previous_segment_final"
            next_segment["validation_error"] = None

    async def _async_schedule_classification_notification(
        self, segment: dict[str, Any]
    ) -> None:
        """Expose the question now, but delay nonessential phone interruption."""
        segment_id = str(segment.get("id") or "")
        if (
            not segment_id
            or segment.get("classification_ready")
            or segment.get("notification_sent_at")
            or segment.get("notification_task_scheduled")
            or self._pending.get(segment_id) is not segment
        ):
            return

        immediate = bool(
            segment.get("known_place_exception")
            or segment.get("configured_place")
        )
        ended_at = _parse_datetime(segment.get("ended_at")) or datetime.now(UTC)
        due_at = (
            datetime.now(UTC)
            if immediate
            else ended_at
            + timedelta(minutes=PHONE_NOTIFICATION_GRACE_MINUTES)
        )
        segment["notification_due_at"] = _iso_utc(due_at)
        segment["notification_task_scheduled"] = True
        await self._async_save_runtime()
        self._create_task(
            self._async_deliver_scheduled_notification(segment_id),
            f"{DOMAIN}_classification_notification_{segment_id}",
        )

    async def _async_deliver_scheduled_notification(self, segment_id: str) -> None:
        """Send one useful unresolved question after the short-stop grace period."""
        segment = self._pending.get(segment_id)
        if segment is None:
            return
        try:
            due_at = _parse_datetime(segment.get("notification_due_at"))
            if due_at is not None:
                while True:
                    remaining = (due_at - datetime.now(UTC)).total_seconds()
                    if remaining <= 0:
                        break
                    await asyncio.sleep(min(remaining, 60.0))
                    if self._pending.get(segment_id) is not segment or segment.get(
                        "classification_ready"
                    ):
                        return

            if self._pending.get(segment_id) is not segment or segment.get(
                "classification_ready"
            ):
                return
            allowed, reason = mobile_notification_policy(segment)
            segment["notification_reason"] = reason
            if not allowed:
                segment["notification_suppressed_reason"] = reason
                await self._async_save_runtime()
                return
            segment.pop("notification_suppressed_reason", None)
            await self._async_send_classification_notification(segment)
        finally:
            segment["notification_task_scheduled"] = False

    async def _async_send_classification_notification(
        self, segment: dict[str, Any], validation_message: str | None = None
    ) -> None:
        """Ask the phone to classify an unknown destination."""
        if (
            segment.get("classification_ready")
            or (
                validation_message is None
                and segment.get("notification_sent_at")
            )
        ):
            return
        segment_id = str(segment["id"])
        estimate = str(segment.get("map_estimate") or "Neznámý cíl")
        return_context = segment.get("return_context")
        known_place_exception = bool(segment.get("known_place_exception"))
        if known_place_exception:
            base_message = (
                f"Místo {estimate} je výjimka používaná služebně i soukromě. "
                "Jaký typ měla tato jízda?"
            )
        elif isinstance(return_context, dict) and return_context.get("suggested"):
            previous_purpose = return_context.get("previous_purpose")
            if previous_purpose:
                base_message = (
                    f"Možný návrat po návštěvě {previous_purpose}. "
                    f"Odhadovaný cíl: {estimate}. Jaký typ měla tato jízda?"
                )
            elif return_context.get("reason") == "configured_home_destination":
                base_message = (
                    "Dojeli jste domů. Byla tato jízda služební, nebo soukromá?"
                )
            else:
                base_message = (
                    f"Odhadovaný cíl: {estimate}. Jaký typ měla tato jízda?"
                )
        elif segment.get("candidate_search_status") == "error":
            base_message = (
                f"Jízda ukončena v cíli {estimate}. Mapový odhad není úplný. "
                "Jaký typ měla tato jízda?"
            )
        else:
            base_message = (
                f"Jízda ukončena. Odhadovaný cíl: {estimate}. "
                "Jaký typ měla tato jízda?"
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
            segment["notification_error"] = (
                f"notify.{service} is not registered"
            )
            await self._async_save_runtime()
            _LOGGER.warning(
                "Notification service notify.%s does not exist; segment %s remains pending",
                service,
                segment_id,
            )
            return

        actions = []
        if isinstance(return_context, dict) and (
            return_context.get("previous_segment_id")
            or return_context.get("previous_purpose")
        ):
            actions.append(
                {
                    "action": _action_id(ACTION_RETURN, segment_id),
                    "title": "Služební návrat",
                }
            )
        actions.extend(
            [
                {
                    "action": _action_id(ACTION_BUSINESS, segment_id),
                    "title": "Služební",
                },
                {
                    "action": _action_id(ACTION_PRIVATE, segment_id),
                    "title": "Soukromá",
                },
            ]
        )
        try:
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
        except Exception as err:  # noqa: BLE001 - the panel remains authoritative
            segment["notification_error"] = f"{type(err).__name__}: {err}"
            await self._async_save_runtime()
            _LOGGER.warning(
                "Could not notify phone about pending segment %s: %s",
                segment_id,
                err,
            )
            return
        segment.pop("notification_error", None)
        segment["notification_sent_at"] = _iso_utc(datetime.now(UTC))
        segment["notification_tag"] = f"kniha_jizd_{segment_id}"
        await self._async_save_runtime()

    async def _async_clear_classification_notification(self, segment_id: str) -> None:
        """Dismiss the actionable notification after a successful decision."""
        service = self.notify_service
        if not self.hass.services.has_service("notify", service):
            return
        try:
            await self.hass.services.async_call(
                "notify",
                service,
                {
                    "message": "clear_notification",
                    "data": {"tag": f"kniha_jizd_{segment_id}"},
                },
                blocking=True,
            )
        except Exception:  # noqa: BLE001 - classification is already safely stored
            _LOGGER.exception(
                "Could not clear trip notification for segment %s", segment_id
            )

    async def _async_send_place_notification(
        self, prompt: dict[str, Any], validation_message: str | None = None
    ) -> None:
        """Ask only whether an already classified destination should be remembered."""
        segment_id = str(prompt.get("segment_id") or "")
        if not segment_id or segment_id not in self._place_prompts:
            return
        if prompt.get("notification_sent_at") and validation_message is None:
            return
        service = self.notify_service
        if not self.hass.services.has_service("notify", service):
            prompt["notification_error"] = f"notify.{service} is not registered"
            await self._async_save_runtime()
            return

        suggested_label = str(prompt.get("suggested_label") or "Místo")
        message = (
            f"Jízda je uložená jako {prompt.get('trip_type_label')}. "
            f"Chcete cíl {suggested_label} uložit pro automatické rozpoznání příště?"
        )
        if validation_message:
            message = f"{validation_message} {message}"
        actions = [
            {
                "action": _action_id(ACTION_SAVE_PLACE, segment_id),
                "title": f"Uložit {suggested_label[:24]}",
            }
        ]
        if place_name_input_allowed(str(prompt.get("trip_type") or "")):
            actions.append(
                {
                    "action": _action_id(ACTION_SAVE_NAMED_PLACE, segment_id),
                    "title": "Jiný název",
                    "behavior": "textInput",
                    "textInputButtonTitle": "Uložit místo",
                    "textInputPlaceholder": "Název místa",
                }
            )
        actions.append(
            {
                "action": _action_id(ACTION_SKIP_PLACE, segment_id),
                "title": "Jen tentokrát",
            },
        )
        try:
            await self.hass.services.async_call(
                "notify",
                service,
                {
                    "title": "Kniha jízd · místo",
                    "message": message,
                    "data": {
                        "tag": f"kniha_jizd_place_{segment_id}",
                        "actions": actions,
                    },
                },
                blocking=True,
            )
        except Exception as err:  # noqa: BLE001 - panel remains available
            prompt["notification_error"] = f"{type(err).__name__}: {err}"
            await self._async_save_runtime()
            return
        prompt.pop("notification_error", None)
        prompt["notification_sent_at"] = _iso_utc(datetime.now(UTC))
        await self._async_save_runtime()

    async def _async_clear_place_notification(self, segment_id: str) -> None:
        """Dismiss the independent place-save question."""
        service = self.notify_service
        if not self.hass.services.has_service("notify", service):
            return
        try:
            await self.hass.services.async_call(
                "notify",
                service,
                {
                    "message": "clear_notification",
                    "data": {"tag": f"kniha_jizd_place_{segment_id}"},
                },
                blocking=True,
            )
        except Exception:  # noqa: BLE001 - the decision is already stored
            _LOGGER.exception(
                "Could not clear place notification for segment %s", segment_id
            )

    async def async_resolve_trip(
        self,
        segment_id: str,
        action: str,
        value: str = "",
        candidate_index: int | None = None,
        channel: str = "panel",
    ) -> dict[str, Any]:
        """Resolve one pending question identically from the panel or phone."""
        normalized_action = str(action or "").strip().upper()
        if normalized_action not in {
            ACTION_CONFIRM,
            ACTION_NEW,
            ACTION_BUSINESS,
            ACTION_PRIVATE,
            ACTION_RETURN,
        }:
            raise ValueError("unsupported trip resolution action")
        if candidate_index is not None and (
            isinstance(candidate_index, bool)
            or candidate_index < 1
            or candidate_index > 3
        ):
            raise ValueError("candidate_index must be an integer from 1 to 3")
        async with self._resolution_lock:
            return await self._async_resolve_trip_locked(
                segment_id,
                normalized_action,
                str(value or "").strip(),
                candidate_index,
                channel,
            )

    async def async_resolve_place(
        self,
        segment_id: str,
        action: str,
        label: str = "",
        channel: str = "panel",
    ) -> dict[str, Any]:
        """Save or dismiss a place independently of the trip classification."""
        normalized_action = str(action or "").strip().casefold()
        if normalized_action not in {"save", "skip"}:
            raise ValueError("action must be save or skip")
        async with self._resolution_lock:
            prompt = self._place_prompts.get(segment_id)
            if prompt is None:
                await self._async_clear_place_notification(segment_id)
                return {"updated": 0, "state": "already_resolved"}
            result: dict[str, Any] = {"place_updated": False}
            if normalized_action == "save":
                selected_label = str(label or prompt.get("suggested_label") or "").strip()
                if not selected_label:
                    raise ValueError("zadejte název místa")
                result = await self.repository.async_sync_place_from_trip(
                    segment_id,
                    str(prompt.get("purpose") or ""),
                    str(prompt.get("trip_type") or TRIP_TYPE_PRIVATE),
                    self.client_radius,
                    self.private_radius,
                    selected_label,
                )
                if not result.get("place_updated"):
                    reason = str(result.get("reason") or "unknown")
                    messages = {
                        "missing_destination": "Cíl nemá souřadnice ani použitelnou adresu.",
                        "unreliable_destination": (
                            "Poloha cíle je příliš nepřesná pro bezpečné uložení."
                        ),
                        "configured_place": (
                            "Domov a firma se upravují v nastavení integrace."
                        ),
                    }
                    raise ValueError(messages.get(reason, "Místo se nepodařilo uložit."))
            self._place_prompts.pop(segment_id, None)
            await self._async_save_runtime()
            await self._async_clear_place_notification(segment_id)
            await self._async_refresh_statistics()
            self._last_notification_action = {
                "segment_id": segment_id,
                "action": f"place_{normalized_action}",
                "channel": channel,
                "processed_at": _iso_utc(datetime.now(UTC)),
            }
            self._notify_listeners()
            return {
                "updated": 1,
                "state": "saved" if normalized_action == "save" else "skipped",
                **result,
            }

    async def _async_resolve_trip_locked(
        self,
        segment_id: str,
        action: str,
        value: str,
        candidate_index: int | None,
        channel: str,
    ) -> dict[str, Any]:
        """Apply one validated pending decision while holding the resolution lock."""
        segment = self._pending.get(segment_id)
        if segment is None:
            await self._async_clear_classification_notification(segment_id)
            return {"updated": 0, "state": "already_resolved"}

        candidates = _map_candidates(segment)
        selected_candidate: dict[str, Any] | None = None
        learned_label: str | None = None
        place_role: str | None = None
        learn_place = False
        return_context = segment.get("return_context")

        if action == ACTION_RETURN:
            if not isinstance(return_context, dict) or not (
                return_context.get("previous_segment_id")
                or return_context.get("previous_purpose")
            ):
                raise ValueError("this trip has no confirmed business return context")
            await self._async_finalize_return(
                segment,
                source=(
                    "notification_return"
                    if channel == "notification"
                    else "manual_panel_return"
                ),
                learn_place=False,
            )
        else:
            if action == ACTION_CONFIRM:
                if segment.get("known_place_exception"):
                    purpose = str(
                        segment.get("map_estimate")
                        or segment.get("return_destination_label")
                        or ""
                    )
                    trip_type = TRIP_TYPE_BUSINESS
                    learn_place = False
                elif candidate_index is not None:
                    if candidate_index > len(candidates):
                        raise ValueError("selected map candidate is not available")
                    selected_candidate = candidates[candidate_index - 1]
                    purpose = str(selected_candidate["name"])
                    trip_type = TRIP_TYPE_BUSINESS
                else:
                    # The phone's generic confirm action intentionally accepts
                    # the first displayed proposal. Panel buttons always send
                    # their explicit candidate index.
                    selected_candidate = candidates[0] if candidates else None
                    purpose = str(
                        (selected_candidate or {}).get("name")
                        or segment.get("map_estimate")
                        or ""
                    )
                    trip_type = TRIP_TYPE_BUSINESS
            elif action == ACTION_NEW:
                selected_value = value
                if candidate_index is None and selected_value.isdigit():
                    candidate_index = int(selected_value)
                    selected_value = ""
                if candidate_index is not None:
                    if not 1 <= candidate_index <= min(3, len(candidates)):
                        raise ValueError("selected map candidate is not available")
                    selected_candidate = candidates[candidate_index - 1]
                    purpose = str(selected_candidate["name"])
                elif selected_value:
                    purpose = selected_value
                else:
                    raise ValueError("enter a customer or purpose")
                trip_type = TRIP_TYPE_BUSINESS
                if isinstance(return_context, dict):
                    segment["journey_role"] = "destination"
            elif action == ACTION_BUSINESS:
                purpose = value
                if not purpose and segment.get("known_place_exception"):
                    purpose = str(
                        segment.get("map_estimate")
                        or segment.get("return_destination_label")
                        or ""
                    )
                trip_type = TRIP_TYPE_BUSINESS
            elif action == ACTION_PRIVATE:
                purpose = "Soukromá"
                trip_type = TRIP_TYPE_PRIVATE
            else:  # guarded by async_resolve_trip
                raise ValueError("unsupported trip resolution action")

            if selected_candidate is not None:
                segment["map_estimate"] = purpose
                segment["selected_map_candidate"] = selected_candidate

            await self._async_finalize_segment(
                segment,
                purpose=purpose,
                trip_type=trip_type,
                source=(
                    "notification_map_candidate"
                    if channel == "notification" and selected_candidate is not None
                    else "notification"
                    if channel == "notification"
                    else "manual_panel_map_candidate"
                    if selected_candidate is not None
                    else "manual_panel"
                ),
                learn_place=learn_place,
                learned_label=learned_label,
                place_role=place_role,
            )

        self._last_notification_action = {
            "segment_id": segment_id,
            "action": action,
            "channel": channel,
            "processed_at": _iso_utc(datetime.now(UTC)),
        }
        await self._async_clear_classification_notification(segment_id)
        self._notify_listeners()
        return {
            "updated": 1,
            "state": "saved" if segment.get("persisted") else "waiting_odometer",
        }

    async def _async_process_notification_action(
        self, event_data: dict[str, Any]
    ) -> None:
        """Classify and persist a pending segment from a mobile app action."""
        action_id = str(event_data.get("action", ""))
        reply_text = str(
            event_data.get("reply_text") or event_data.get("replyText") or ""
        ).strip()
        place_match = _PLACE_ACTION_PATTERN.match(action_id)
        if place_match is not None:
            action, segment_id = place_match.groups()
            try:
                if action == ACTION_SKIP_PLACE:
                    await self.async_resolve_place(
                        segment_id, "skip", channel="notification"
                    )
                else:
                    if action == ACTION_SAVE_NAMED_PLACE and not reply_text:
                        raise ValueError("zadejte název místa")
                    await self.async_resolve_place(
                        segment_id,
                        "save",
                        reply_text,
                        channel="notification",
                    )
            except ValueError as err:
                prompt = self._place_prompts.get(segment_id)
                if prompt is not None:
                    await self._async_send_place_notification(
                        prompt, f"{err}. Zkuste to prosím znovu."
                    )
            return

        match = _ACTION_PATTERN.match(action_id)
        if match is None:
            return
        action, segment_id = match.groups()
        try:
            await self.async_resolve_trip(
                segment_id,
                action,
                reply_text,
                channel="notification",
            )
        except ValueError as err:
            segment = self._pending.get(segment_id)
            if segment is not None:
                await self._async_send_classification_notification(
                    segment, f"{err}. Zkuste to prosím znovu."
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
        # A return is a relationship between trips, never a durable place type.
        # The argument is retained for runtime compatibility with restored tasks.
        del learn_place
        await self._async_finalize_segment(
            segment,
            purpose=purpose,
            trip_type=TRIP_TYPE_BUSINESS,
            source=source,
            learn_place=False,
            learned_label=None,
            place_role=None,
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
        if segment.get("manual_resolution_requested_at") and not source.startswith(
            "manual_panel"
        ):
            return
        if segment.get("configured_place") in {"home", "company"}:
            # Home/company already have an authoritative configured zone. The
            # trip may be private or business without creating another map place.
            learn_place = False
            learned_label = None
            place_role = None
        segment_id = str(segment["id"])
        segment["purpose"] = purpose
        segment["trip_type"] = trip_type
        segment["classification_source"] = source
        if trip_type != TRIP_TYPE_UNCLASSIFIED:
            segment["needs_review"] = False
            segment.pop("review_reason", None)
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
        """Write a ready segment and remember its real destination when safe."""
        if segment.get("configured_place") in {"home", "company"}:
            # Also protect segments restored with pre-fix classification options.
            learn_place = False
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
                await self.repository.async_append_segment(transient)
                self._transient.pop(str(transient.get("id")), None)

            await self.repository.async_append_segment(segment)
            segment_date = str(segment.get("date") or "")
            if segment_date:
                await self.repository.async_reconcile_day(segment_date)

            # Legacy classification options stay accepted for restored runtime
            # data; destination learning now happens after the trip is durable.
            del learn_place, learned_label, place_role
        if self._should_offer_place_save(segment, source, trip_type):
            result = await self.repository.async_sync_place_from_trip(
                str(segment.get("id") or ""),
                purpose,
                trip_type,
                self.client_radius,
                self.private_radius,
                place_label_suggestion(segment, purpose, trip_type),
                False,
            )
            if not result.get("place_updated"):
                # Keep the explicit fallback available when a destination cannot
                # be stored safely from the captured location data.
                await self._async_queue_place_prompt(segment, purpose, trip_type)
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

    def _should_offer_place_save(
        self, segment: dict[str, Any], source: str, trip_type: str
    ) -> bool:
        """Offer learning only after an explicit classification of a real destination."""
        radius = (
            self.private_radius
            if trip_type == TRIP_TYPE_PRIVATE
            else self.client_radius
        )
        return should_offer_place_save(segment, source, trip_type, radius)

    async def _async_queue_place_prompt(
        self, segment: dict[str, Any], purpose: str, trip_type: str
    ) -> None:
        """Create a fallback decision when automatic place saving was unsafe."""
        segment_id = str(segment.get("id") or "")
        if not segment_id or segment_id in self._place_prompts:
            return
        suggested_label = place_label_suggestion(segment, purpose, trip_type)
        prompt = {
            "segment_id": segment_id,
            "title": "Uložit místo pro příště?",
            "prompt": (
                f"Jízda už je zařazená jako "
                f"{'služební' if trip_type == TRIP_TYPE_BUSINESS else 'soukromá'}. "
                "Teď můžete samostatně rozhodnout, zda se má cíl příště rozpoznat automaticky."
            ),
            "suggested_label": suggested_label,
            "purpose": purpose,
            "trip_type": trip_type,
            "started_at": segment.get("started_at"),
            "start_address": segment.get("start_address"),
            "end_address": segment.get("end_address"),
            "map_estimate": segment.get("map_estimate"),
            "trip_type_label": (
                "služební" if trip_type == TRIP_TYPE_BUSINESS else "soukromá"
            ),
            "name_input_allowed": place_name_input_allowed(trip_type),
            "candidates": [
                {
                    "index": index,
                    "name": str(candidate.get("name") or "").strip(),
                }
                for index, candidate in enumerate(_map_candidates(segment)[:3], start=1)
                if str(candidate.get("name") or "").strip()
            ],
            "created_at": _iso_utc(datetime.now(UTC)),
        }
        self._place_prompts[segment_id] = prompt
        await self._async_save_runtime()
        self._create_task(
            self._async_send_place_notification(prompt),
            f"{DOMAIN}_place_notification_{segment_id}",
        )

    def _capture_location(self) -> dict[str, float | str | None]:
        """Capture GPS attributes and the full geocoded address."""
        gps_state = self.hass.states.get(self.gps_entity)
        address_state = self.hass.states.get(self.address_entity)
        details = _location_coordinate_details(gps_state, address_state)
        latitude = details.get("latitude") if details is not None else None
        longitude = details.get("longitude") if details is not None else None
        address_raw: str | None = None
        coordinate_updated_at = (
            details.get("updated_at") if details is not None else None
        )
        address_updated_at = (
            _state_updated_at(address_state) if address_state is not None else None
        )
        address_matches_fix = bool(
            coordinate_updated_at is None
            or address_updated_at is None
            or address_updated_at
            >= coordinate_updated_at - timedelta(minutes=5)
        )
        if (
            address_matches_fix
            and address_state is not None
            and address_state.state.casefold() not in UNAVAILABLE_STATES
        ):
            address_raw = address_state.state.strip()
        address = shorten_address(address_raw)
        if not address and latitude is not None and longitude is not None:
            address = f"{latitude:.6f}, {longitude:.6f}"
            address_raw = address
        return {
            "latitude": latitude,
            "longitude": longitude,
            "address": address,
            "address_raw": address_raw,
            "coordinate_source": (
                details.get("source_detail") if details is not None else None
            ),
            "coordinate_updated_at": _iso_utc(coordinate_updated_at),
            "address_updated_at": _iso_utc(address_updated_at),
            "accuracy_m": details.get("accuracy_m") if details is not None else None,
        }

    async def _async_save_runtime(self) -> None:
        """Persist all unfinished journey state across HA restarts."""
        async with self._runtime_lock:
            await self._runtime_store.async_save(
                {
                    "active": deepcopy(self._active),
                    "closing": deepcopy(self._closing),
                    "pending": deepcopy(self._pending),
                    "transient": deepcopy(self._transient),
                    "place_prompts": deepcopy(self._place_prompts),
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


def _location_coordinates(
    gps_state: State | None, address_state: State | None
) -> tuple[float | None, float | None, str | None]:
    """Return coordinates from the freshest trustworthy phone source."""
    selected = _location_coordinate_details(gps_state, address_state)
    if selected is None:
        return None, None, None
    return (
        selected["latitude"],
        selected["longitude"],
        str(selected["source_detail"]),
    )


def _location_coordinate_details(
    gps_state: State | None, address_state: State | None
) -> dict[str, Any] | None:
    """Build and rank valid coordinate candidates with their update times."""
    candidates: list[dict[str, Any]] = []
    for source, state in (("gps_entity", gps_state), ("address_entity", address_state)):
        if state is None or state.state.casefold() in UNAVAILABLE_STATES:
            continue
        coordinates = coordinates_from_state(state.state, state.attributes)
        if coordinates is not None:
            latitude, longitude, representation = coordinates
            accuracy = _as_float(
                state.attributes.get("gps_accuracy")
                if state.attributes.get("gps_accuracy") is not None
                else state.attributes.get("accuracy")
            )
            if accuracy is not None and accuracy < 0:
                accuracy = None
            candidates.append(
                {
                    "source": source,
                    "source_detail": f"{source}:{representation}",
                    "latitude": latitude,
                    "longitude": longitude,
                    "updated_at": _state_updated_at(state),
                    "accuracy_m": accuracy,
                }
            )
    return select_coordinate_candidate(candidates)


def _state_updated_at(state: State) -> datetime:
    """Read an integration timestamp before falling back to HA metadata."""
    return (
        _parse_datetime(state.attributes.get("last_updated"))
        or _parse_datetime(state.attributes.get("timestamp"))
        or _ensure_utc(state.last_updated)
    )


def _odometer_details(state: State | None) -> tuple[float | None, str | None]:
    """Parse a numeric state or a known odometer attribute."""
    if state is None:
        return None, None
    primary_state = (
        None if state.state.casefold() in UNAVAILABLE_STATES else state.state
    )
    parsed = odometer_from_state(primary_state, state.attributes)
    return parsed if parsed is not None else (None, None)


def _odometer_value(state: State | None) -> float | None:
    """Parse the odometer state as kilometres."""
    return _odometer_details(state)[0]


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
    return parse_decimal(value)


def _whole_km(value: float) -> int:
    """Round a non-negative kilometre value to a whole kilometre."""
    return int(floor(max(0.0, value) + 0.5))


def _segment_gps_distance_km(segment: dict[str, Any]) -> float | None:
    """Return a provisional direct GPS distance for an unresolved segment."""
    distance_m = coordinate_distance_m(
        segment.get("start_latitude"),
        segment.get("start_longitude"),
        segment.get("end_latitude"),
        segment.get("end_longitude"),
    )
    return (
        round(max(0.0, distance_m) / 1000, 3)
        if distance_m is not None
        else None
    )


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
        if isinstance(candidate, dict)
        and str(candidate.get("name") or "").strip()
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
    decision = _classification_decision(segment)
    return {
        "id": segment.get("id"),
        "journey_id": segment.get("journey_id"),
        "started_at": segment.get("started_at"),
        "ended_at": segment.get("ended_at"),
        "start_address": segment.get("start_address"),
        "end_address": segment.get("end_address"),
        "distance_km": segment.get("distance_km"),
        "distance_reconciliation_source": segment.get(
            "distance_reconciliation_source"
        ),
        "manual_distance_override": bool(segment.get("manual_distance_override")),
        "purpose": segment.get("purpose"),
        "trip_type": segment.get("trip_type"),
        "journey_role": segment.get("journey_role"),
        "visit_role": segment.get("visit_role"),
        "needs_review": bool(segment.get("needs_review")),
        "review_reason": segment.get("review_reason"),
        "decision": decision,
        "question": panel_question(segment, status),
        "odometer_ready": bool(segment.get("odometer_ready") or status == "saved"),
        "odometer_completion_source": segment.get("odometer_completion_source"),
        "status": status,
        "editable": bool(
            segment.get("ended_at") is not None
            and status not in {"driving", "processing_destination"}
        ),
    }


def _classification_decision(segment: dict[str, Any]) -> dict[str, Any]:
    """Build a concise, user-facing audit explanation for one classification."""
    source = str(segment.get("classification_source") or "unknown")
    inherited = source.startswith("journey_inherited:")
    base_source = source.split(":", 1)[1] if inherited else source
    labels = {
        "manual_panel": "Ruční oprava v panelu",
        "manual_panel_return": "Služební návrat potvrzený v panelu",
        "manual_panel_map_candidate": "Mapový návrh potvrzený v panelu",
        "notification": "Potvrzení z telefonu",
        "notification_map_candidate": "Mapový návrh potvrzený z telefonu",
        "configured_company": "Nakonfigurovaná firemní zóna",
        "configured_company_return": "Návazná jízda do firemní zóny",
        "configured_company_private_return": (
            "Soukromá návazná jízda do firemní zóny"
        ),
        "configured_home_return": "Návazná jízda do zóny domova",
        "configured_home_private_return": "Soukromá návazná jízda domů",
        "learned_place": "Známé služební místo",
        "learned_private_place": "Známé soukromé místo",
        "learned_private_after_transient_wait": "Známé soukromé místo po čekání",
        "learned_return_context": "Návaznost na předchozí služební jízdu",
        "notification_return": "Služební návrat potvrzený z telefonu",
        "transient_auto_review": "Automaticky uzavřená nezodpovězená zastávka",
    }
    label = labels.get(base_source, base_source.replace("_", " "))
    if inherited:
        label = f"Převzato z cíle celé cesty: {label}"

    explanation = str(segment.get("classification_explanation") or "").strip()
    matched_label = segment.get("map_estimate") or segment.get("return_destination_label")
    distance = segment.get("matched_place_distance_m")
    radius = segment.get("matched_place_radius_m")
    match_method = segment.get("matched_place_method")
    selected_candidate = segment.get("selected_map_candidate")
    if isinstance(selected_candidate, dict) and distance is None:
        distance = selected_candidate.get("distance_m")
        radius = segment.get("candidate_search_radius_m")
        matched_label = selected_candidate.get("name") or matched_label
        match_method = "overpass"
    configured_match = segment.get("configured_place_match")
    if not explanation and isinstance(configured_match, dict):
        distance = configured_match.get("distance_m")
        radius = (
            segment.get("configured_place_radius_m")
            or (None if configured_match.get("method") == "address" else radius)
        )
        explanation = (
            f"Cíl odpovídá zóně {segment.get('configured_place') or 'místa'} "
            f"metodou {configured_match.get('method') or 'neznámou'}."
        )
    if not explanation and segment.get("matched_place_id"):
        explanation = (
            f"Cíl odpovídá uloženému místu {matched_label or segment.get('matched_place_id')}."
        )
    context = segment.get("return_context")
    if not explanation and isinstance(context, dict):
        explanation = (
            "Jízda začala v místě konce předchozí služební jízdy"
            + (
                f" po {context.get('gap_minutes')} minutách."
                if context.get("gap_minutes") is not None
                else "."
            )
        )
    if not explanation:
        explanation = label[:1].upper() + label[1:] + "."

    confidence = "high"
    if base_source == "transient_auto_review":
        confidence = "review"
    elif base_source in {"notification", "notification_map_candidate", "manual_panel"}:
        confidence = "confirmed"
    elif source == "unknown":
        confidence = "unknown"
    return {
        "source": source,
        "source_label": label,
        "explanation": explanation,
        "confidence": confidence,
        "matched_place_id": segment.get("matched_place_id"),
        "matched_place_label": matched_label,
        "match_method": match_method,
        "distance_m": distance,
        "radius_m": radius,
        "return_reason": context.get("reason") if isinstance(context, dict) else None,
        "return_gap_minutes": (
            context.get("gap_minutes") if isinstance(context, dict) else None
        ),
        "candidate_search_status": segment.get("candidate_search_status"),
        "candidate_search_attempts": segment.get("candidate_search_attempts"),
        "candidate_search_cache_hit": bool(
            segment.get("candidate_search_cache_hit")
        ),
        "candidate_search_error": segment.get("candidate_search_error"),
        "suggested_candidates": [
            {
                "name": candidate.get("name"),
                "distance_m": candidate.get("distance_m"),
                "score": candidate.get("score"),
            }
            for candidate in _map_candidates(segment)[:3]
        ],
    }


def _map_trip_row(segment: dict[str, Any], status: str) -> dict[str, Any]:
    """Serialize coordinates and context for a single map route segment."""
    stop = segment.get("transient_stop")
    stop = stop if isinstance(stop, dict) else {}
    visit_role = segment.get("visit_role")
    short_stop = visit_role in {"waypoint", "waypoint_candidate"} or (
        segment.get("journey_role") == "transient_stop"
    )
    return {
        "id": segment.get("id"),
        "started_at": segment.get("started_at"),
        "ended_at": segment.get("ended_at"),
        "start_latitude": _as_float(segment.get("start_latitude")),
        "start_longitude": _as_float(segment.get("start_longitude")),
        "end_latitude": _as_float(segment.get("end_latitude")),
        "end_longitude": _as_float(segment.get("end_longitude")),
        "start_address": segment.get("start_address"),
        "end_address": segment.get("end_address"),
        "purpose": segment.get("purpose"),
        "trip_type": segment.get("trip_type"),
        "journey_id": segment.get("journey_id"),
        "journey_role": segment.get("journey_role"),
        "visit_role": visit_role,
        "short_stop": short_stop,
        "short_stop_label": (
            stop.get("name")
            or segment.get("map_estimate")
            or segment.get("end_address")
            or "Krátká zastávka"
        ),
        "short_stop_kind": stop.get("kind"),
        "short_stop_confirmed": visit_role == "waypoint"
        or (
            status == "saved"
            and visit_role != "waypoint_candidate"
            and segment.get("journey_role") == "transient_stop"
        ),
        "needs_review": bool(segment.get("needs_review")),
        "status": status,
    }
