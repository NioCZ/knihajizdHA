"""Authenticated history endpoint for the bundled administration panel."""

from __future__ import annotations

from datetime import date, datetime

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .manager import KnihaJizdManager


class KnihaJizdHistoryView(HomeAssistantView):
    """Serve month totals and trips for one selected calendar day."""

    url = f"/api/{DOMAIN}/history"
    name = f"api:{DOMAIN}:history"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        """Store Home Assistant for runtime manager lookup."""
        self.hass = hass

    async def get(self, request: web.Request) -> web.Response:
        """Return history data to an authenticated administrator."""
        user = request["hass_user"]
        if not user.is_admin:
            raise web.HTTPForbidden(text="Administrator access is required")

        today = dt_util.now().date()
        month = str(request.query.get("month") or today.strftime("%Y-%m"))
        try:
            parsed_month = datetime.strptime(month, "%Y-%m")
        except ValueError as error:
            raise web.HTTPBadRequest(text="month must use YYYY-MM") from error
        if parsed_month.strftime("%Y-%m") != month:
            raise web.HTTPBadRequest(text="month must use YYYY-MM")

        default_date = (
            today.isoformat()
            if month == today.strftime("%Y-%m")
            else f"{month}-01"
        )
        selected_date = str(request.query.get("date") or default_date)
        try:
            parsed_date = date.fromisoformat(selected_date)
        except ValueError as error:
            raise web.HTTPBadRequest(text="date must use YYYY-MM-DD") from error
        if parsed_date.strftime("%Y-%m") != month:
            raise web.HTTPBadRequest(text="date must belong to selected month")

        manager = self._loaded_manager()
        if manager is None:
            raise web.HTTPServiceUnavailable(text="Kniha jízd is not loaded")
        response = self.json(
            await manager.async_get_history_data(month, selected_date)
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    def _loaded_manager(self) -> KnihaJizdManager | None:
        """Find the single loaded runtime manager."""
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if entry.state is not ConfigEntryState.LOADED:
                continue
            manager = getattr(entry, "runtime_data", None)
            if isinstance(manager, KnihaJizdManager):
                return manager
        return None
