"""Readiness binary sensor for Kniha jízd."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
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
    """Set up the Kniha jízd readiness sensor."""
    manager: KnihaJizdManager = entry.runtime_data
    async_add_entities([KnihaJizdReadyBinarySensor(manager, entry)])


class KnihaJizdReadyBinarySensor(KnihaJizdEntity, BinarySensorEntity):
    """Report whether all essential runtime inputs are usable."""

    _attr_icon = "mdi:check-network-outline"

    def __init__(self, manager: KnihaJizdManager, entry: ConfigEntry) -> None:
        """Initialize the readiness sensor."""
        super().__init__(manager, entry, "ready", "připravena")

    @property
    def is_on(self) -> bool:
        """Return True when the three inputs required for logging work."""
        return bool(self.manager.diagnostics["ready"])

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Show every individual readiness check."""
        diagnostics = self.manager.diagnostics
        return {
            **self._kind_attributes(),
            "trigger_ok": diagnostics["trigger_ok"],
            "gps_ok": diagnostics["gps_ok"],
            "address_ok": diagnostics["address_ok"],
            "odometer_ok": diagnostics["odometer_ok"],
            "notify_ok": diagnostics["notify_ok"],
        }
