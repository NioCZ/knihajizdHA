"""Admin-only live overview endpoint for the bundled panel."""

from __future__ import annotations

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .manager import KnihaJizdManager


class KnihaJizdOverviewView(HomeAssistantView):
    """Serve live trip details without publishing them as HA state attributes."""

    url = f"/api/{DOMAIN}/overview"
    name = f"api:{DOMAIN}:overview"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request) -> web.Response:
        """Return sensitive live details only to an administrator."""
        user = request["hass_user"]
        if not user.is_admin:
            raise web.HTTPForbidden(text="Administrator access is required")
        manager = self._loaded_manager()
        if manager is None:
            raise web.HTTPServiceUnavailable(text="Kniha jízd is not loaded")
        response = self.json(manager.panel_overview)
        response.headers["Cache-Control"] = "no-store"
        return response

    def _loaded_manager(self) -> KnihaJizdManager | None:
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            manager = getattr(entry, "runtime_data", None)
            if isinstance(manager, KnihaJizdManager):
                return manager
        return None
