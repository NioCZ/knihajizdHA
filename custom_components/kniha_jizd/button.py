"""Excel export button for Kniha jízd."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SERVICE_EXPORT_EXCEL
from .entity import KnihaJizdEntity
from .manager import KnihaJizdManager


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Kniha jízd export button."""
    manager: KnihaJizdManager = entry.runtime_data
    async_add_entities([KnihaJizdExportButton(manager, entry)])


class KnihaJizdExportButton(KnihaJizdEntity, ButtonEntity):
    """Generate the default two-sheet Excel workbook."""

    _attr_icon = "mdi:microsoft-excel"

    def __init__(self, manager: KnihaJizdManager, entry: ConfigEntry) -> None:
        """Initialize the export button."""
        super().__init__(manager, entry, "export_button", "vygenerovat Excel")

    async def async_press(self) -> None:
        """Run the normal integration export service."""
        await self.hass.services.async_call(
            DOMAIN,
            SERVICE_EXPORT_EXCEL,
            {},
            blocking=True,
        )

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Identify the button to the bundled panel."""
        return self._kind_attributes()
