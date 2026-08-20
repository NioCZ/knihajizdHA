"""Temporary authenticated-by-token Excel download endpoint."""

from __future__ import annotations

from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .manager import KnihaJizdManager


class KnihaJizdDownloadView(HomeAssistantView):
    """Serve a generated workbook through a short-lived unguessable URL."""

    url = f"/api/{DOMAIN}/download/{{token}}"
    name = f"api:{DOMAIN}:download"
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        """Store Home Assistant for runtime manager lookup."""
        self.hass = hass

    async def get(self, request: web.Request, token: str) -> web.StreamResponse:
        """Download the latest export if the 15-minute token is valid."""
        manager = self._loaded_manager()
        path = manager.validate_download_token(token) if manager else None
        if path is None or not await self.hass.async_add_executor_job(path.is_file):
            raise web.HTTPNotFound(text="Export link is invalid or expired")
        filename = str(manager.export_status.get("filename") or "kniha_jizd.xlsx")
        return web.FileResponse(
            path,
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )

    def _loaded_manager(self) -> KnihaJizdManager | None:
        """Find the single loaded runtime manager."""
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            manager = getattr(entry, "runtime_data", None)
            if isinstance(manager, KnihaJizdManager):
                return manager
        return None
