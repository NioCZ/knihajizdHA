"""Authenticated place-map endpoint for the bundled administration panel."""

from __future__ import annotations

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .manager import KnihaJizdManager


class KnihaJizdMapView(HomeAssistantView):
    """Serve live car, learned zones and today's route coordinates."""

    url = f"/api/{DOMAIN}/map"
    name = f"api:{DOMAIN}:map"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        """Store Home Assistant for runtime manager lookup."""
        self.hass = hass

    async def get(self, request: web.Request) -> web.Response:
        """Return current map data to an authenticated administrator."""
        user = request["hass_user"]
        if not user.is_admin:
            raise web.HTTPForbidden(text="Administrator access is required")
        manager = self._loaded_manager()
        if manager is None:
            raise web.HTTPServiceUnavailable(text="Kniha jízd is not loaded")
        return self.json(await manager.async_get_map_data())

    def _loaded_manager(self) -> KnihaJizdManager | None:
        """Find the single loaded runtime manager."""
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            manager = getattr(entry, "runtime_data", None)
            if isinstance(manager, KnihaJizdManager):
                return manager
        return None
