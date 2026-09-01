"""Authenticated learned-place management endpoint for the bundled panel."""

from __future__ import annotations

from typing import Any

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .manager import KnihaJizdManager


class KnihaJizdPlacesView(HomeAssistantView):
    """Return and mutate learned places for an authenticated administrator."""

    url = f"/api/{DOMAIN}/places"
    name = f"api:{DOMAIN}:places"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request) -> web.Response:
        """Return editable learned-place records."""
        manager = self._manager(request)
        response = self.json(await manager.async_get_places_data())
        response.headers["Cache-Control"] = "no-store"
        return response

    async def post(self, request: web.Request) -> web.Response:
        """Apply one validated place-management action."""
        manager = self._manager(request)
        try:
            payload: Any = await request.json()
        except (ValueError, TypeError) as err:
            return web.json_response({"error": f"Invalid JSON: {err}"}, status=400)
        if not isinstance(payload, dict):
            return web.json_response(
                {"error": "JSON body must be an object"}, status=400
            )
        try:
            result = await manager.async_manage_place(payload)
        except ValueError as err:
            return web.json_response({"error": str(err)}, status=400)
        response = self.json(result)
        response.headers["Cache-Control"] = "no-store"
        return response

    def _manager(self, request: web.Request) -> KnihaJizdManager:
        """Require administrator access and return the loaded manager."""
        user = request["hass_user"]
        if not user.is_admin:
            raise web.HTTPForbidden(text="Administrator access is required")
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            manager = getattr(entry, "runtime_data", None)
            if isinstance(manager, KnihaJizdManager):
                return manager
        raise web.HTTPServiceUnavailable(text="Kniha jízd is not loaded")
