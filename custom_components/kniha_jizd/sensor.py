"""Diagnostic and statistics sensors for Kniha jízd."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import KnihaJizdEntity
from .manager import KnihaJizdManager


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Kniha jízd sensors."""
    manager: KnihaJizdManager = entry.runtime_data
    async_add_entities(
        [
            KnihaJizdStatusSensor(manager, entry),
            KnihaJizdStatisticSensor(
                manager, entry, "today_business_km", "dnes služební", "km"
            ),
            KnihaJizdStatisticSensor(
                manager, entry, "today_private_km", "dnes soukromé", "km"
            ),
            KnihaJizdStatisticSensor(
                manager, entry, "today_segments", "dnes jízdy", None
            ),
            KnihaJizdPendingSensor(manager, entry),
            KnihaJizdStatisticSensor(
                manager, entry, "segments_total", "celkem jízd", None
            ),
            KnihaJizdStatisticSensor(
                manager, entry, "business_km_total", "celkem služební", "km"
            ),
            KnihaJizdStatisticSensor(
                manager, entry, "private_km_total", "celkem soukromé", "km"
            ),
            KnihaJizdLastTripSensor(manager, entry),
            KnihaJizdExportSensor(manager, entry),
        ]
    )


class KnihaJizdStatusSensor(KnihaJizdEntity, SensorEntity):
    """Expose the trip workflow and health details."""

    _attr_icon = "mdi:car-clock"

    def __init__(self, manager: KnihaJizdManager, entry: ConfigEntry) -> None:
        """Initialize the status sensor."""
        super().__init__(manager, entry, "status", "stav")

    @property
    def native_value(self) -> str:
        """Return the current workflow state."""
        return self.manager.status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return health checks and workflow counters."""
        return self.manager.public_diagnostics


class KnihaJizdStatisticSensor(KnihaJizdEntity, SensorEntity):
    """Expose one numeric raw-log statistic."""

    _attr_icon = "mdi:counter"

    def __init__(
        self,
        manager: KnihaJizdManager,
        entry: ConfigEntry,
        key: str,
        name: str,
        unit: str | None,
    ) -> None:
        """Initialize a statistic sensor."""
        super().__init__(manager, entry, key, name)
        self._key = key
        self._attr_native_unit_of_measurement = unit
        if unit == "km":
            self._attr_icon = "mdi:map-marker-distance"

    @property
    def native_value(self) -> int | float:
        """Return the selected statistic."""
        return self.manager.statistics.get(self._key, 0)

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Identify the sensor to the bundled panel."""
        return self._kind_attributes()


class KnihaJizdPendingSensor(KnihaJizdEntity, SensorEntity):
    """Expose all trips that still need processing or classification."""

    _attr_icon = "mdi:clipboard-clock-outline"

    def __init__(self, manager: KnihaJizdManager, entry: ConfigEntry) -> None:
        """Initialize the pending sensor."""
        super().__init__(manager, entry, "pending", "čekající jízdy")

    @property
    def native_value(self) -> int:
        """Return the total unfinished trip count."""
        diagnostics = self.manager.diagnostics
        return (
            int(diagnostics["closing_count"])
            + int(diagnostics["pending_count"])
            + int(diagnostics["transient_count"])
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return individual workflow queues."""
        diagnostics = self.manager.diagnostics
        return {
            **self._kind_attributes(),
            "waiting_odometer": diagnostics["closing_count"],
            "waiting_classification": diagnostics["pending_count"],
            "waiting_journey_destination": diagnostics["transient_count"],
        }


class KnihaJizdLastTripSensor(KnihaJizdEntity, SensorEntity):
    """Expose the last persisted trip."""

    _attr_icon = "mdi:map-marker-check"
    _attr_native_unit_of_measurement = "km"

    def __init__(self, manager: KnihaJizdManager, entry: ConfigEntry) -> None:
        """Initialize the last-trip sensor."""
        super().__init__(manager, entry, "last_trip", "poslední jízda")

    @property
    def native_value(self) -> float | None:
        """Return the last trip distance."""
        segment = self.manager.statistics.get("last_segment")
        if not isinstance(segment, dict):
            return None
        value = segment.get("distance_km")
        return float(value) if isinstance(value, (int, float)) else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return a privacy-safe last-trip summary."""
        segment = self.manager.statistics.get("last_segment")
        if not isinstance(segment, dict):
            return self._kind_attributes()
        keys = (
            "id",
            "date",
            "started_at",
            "ended_at",
            "trip_type",
            "journey_role",
            "journey_id",
            "journey_segment_count",
            "journey_distance_km",
            "journey_distance_complete",
            "journey_inherited_from_segment_id",
            "transient_stop",
            "transient_continuation",
            "return_of_segment_id",
            "return_context",
            "distance_km",
            "odometer_wait_timed_out",
            "validation_error",
        )
        return {
            **self._kind_attributes(),
            **{key: segment.get(key) for key in keys},
        }


class KnihaJizdExportSensor(KnihaJizdEntity, SensorEntity):
    """Expose the current Excel export and its temporary download link."""

    _attr_icon = "mdi:file-excel"

    def __init__(self, manager: KnihaJizdManager, entry: ConfigEntry) -> None:
        """Initialize the export sensor."""
        super().__init__(manager, entry, "export", "Excel export")

    @property
    def native_value(self) -> str:
        """Return the current export state."""
        return str(self.manager.export_status.get("state") or "never")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return export state without leaking a download token or local path."""
        status = self.manager.export_status
        safe_keys = ("state", "month", "filename", "generated_at", "expires_at", "error")
        return {
            **self._kind_attributes(),
            **{key: status.get(key) for key in safe_keys},
        }
