"""Sidebar panel registration for Kniha jízd."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

PANEL_URL_PATH = "kniha-jizd"
PANEL_STATIC_URL = "/kniha_jizd/frontend"
PANEL_COMPONENT = "kniha-jizd-panel"
PANEL_MODULE_URL = f"{PANEL_STATIC_URL}/kniha-jizd-panel.js?v=1.2.0"
PANEL_DIRECTORY = Path(__file__).parent / "frontend"
PANEL_STATIC_REGISTERED = "kniha_jizd_panel_static_registered"


async def async_register_panel(hass: HomeAssistant) -> None:
    """Register static panel assets and the admin-only sidebar page."""
    if not hass.data.get(PANEL_STATIC_REGISTERED):
        await hass.http.async_register_static_paths(
            [StaticPathConfig(PANEL_STATIC_URL, str(PANEL_DIRECTORY), False)]
        )
        hass.data[PANEL_STATIC_REGISTERED] = True

    if frontend.async_panel_exists(hass, PANEL_URL_PATH):
        frontend.async_remove_panel(hass, PANEL_URL_PATH)

    await panel_custom.async_register_panel(
        hass,
        webcomponent_name=PANEL_COMPONENT,
        frontend_url_path=PANEL_URL_PATH,
        module_url=PANEL_MODULE_URL,
        sidebar_title="Kniha jízd",
        sidebar_icon="mdi:car-clock",
        require_admin=True,
        config={},
    )


def async_unregister_panel(hass: HomeAssistant) -> None:
    """Remove the sidebar entry while leaving the harmless static path."""
    if frontend.async_panel_exists(hass, PANEL_URL_PATH):
        frontend.async_remove_panel(hass, PANEL_URL_PATH)
