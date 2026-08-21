"""Sidebar panel registration for Kniha jízd."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

PANEL_URL_PATH = "kniha-jizd"
PANEL_STATIC_URL = "/kniha_jizd/frontend"
PANEL_COMPONENT = "kniha-jizd-panel"
PANEL_MODULE_URL = f"{PANEL_STATIC_URL}/kniha-jizd-panel.js?v=1.8.2"
PANEL_DIRECTORY = Path(__file__).parent / "frontend"
PANEL_STATIC_REGISTERED = "kniha_jizd_panel_static_registered"


async def async_register_panel(hass: HomeAssistant) -> None:
    """Register static panel assets and the admin-only sidebar page."""
    if not hass.data.get(PANEL_STATIC_REGISTERED):
        async_register_static_paths = getattr(
            hass.http, "async_register_static_paths", None
        )
        if async_register_static_paths is not None:
            await async_register_static_paths(
                [StaticPathConfig(PANEL_STATIC_URL, str(PANEL_DIRECTORY), False)]
            )
        else:
            hass.http.register_static_path(
                PANEL_STATIC_URL, str(PANEL_DIRECTORY), False
            )
        hass.data[PANEL_STATIC_REGISTERED] = True

    if _panel_exists(hass):
        _remove_panel(hass)

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
    if _panel_exists(hass):
        _remove_panel(hass)


def _panel_exists(hass: HomeAssistant) -> bool:
    """Check for the panel on both current and older Home Assistant versions."""
    async_panel_exists = getattr(frontend, "async_panel_exists", None)
    if async_panel_exists is not None:
        return bool(async_panel_exists(hass, PANEL_URL_PATH))
    return PANEL_URL_PATH in hass.data.get("frontend_panels", {})


def _remove_panel(hass: HomeAssistant) -> None:
    """Remove the panel with a fallback for older frontend implementations."""
    async_remove_panel = getattr(frontend, "async_remove_panel", None)
    if async_remove_panel is not None:
        async_remove_panel(hass, PANEL_URL_PATH)
        return
    hass.data.get("frontend_panels", {}).pop(PANEL_URL_PATH, None)
