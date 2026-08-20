"""Shared entity helpers for Kniha jízd."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import DeviceInfo, Entity

from .const import DOMAIN, NAME
from .manager import KnihaJizdManager


class KnihaJizdEntity(Entity):
    """Base class that connects an entity to manager updates."""

    _attr_should_poll = False

    def __init__(
        self,
        manager: KnihaJizdManager,
        entry: ConfigEntry,
        key: str,
        name: str,
    ) -> None:
        """Initialize a diagnostic entity."""
        self.manager = manager
        self._kind = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = f"{NAME} {name}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=NAME,
            manufacturer="Home Assistant Custom Integration",
            model="Logbook",
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to push updates from the manager."""
        self.async_on_remove(
            self.manager.async_add_listener(self.async_write_ha_state)
        )

    def _kind_attributes(self) -> dict[str, str]:
        """Identify the entity to the bundled custom panel."""
        return {"kniha_jizd_kind": self._kind}
