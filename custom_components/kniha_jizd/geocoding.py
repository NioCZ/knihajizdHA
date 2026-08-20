"""Reverse geocoding support for Kniha jízd."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from aiohttp import ClientError, ClientSession

_LOGGER = logging.getLogger(__name__)


class NominatimGeocoder:
    """Small, rate-limited reverse geocoder using a configurable endpoint."""

    def __init__(
        self,
        session: ClientSession,
        endpoint: str,
        user_agent: str,
        email: str | None = None,
    ) -> None:
        """Initialize the geocoder."""
        self._session = session
        self._endpoint = endpoint
        self._user_agent = user_agent
        self._email = email or None
        self._request_lock = asyncio.Lock()
        self._last_request_started = 0.0

    async def async_reverse(
        self, latitude: float | None, longitude: float | None
    ) -> dict[str, Any] | None:
        """Return a normalized reverse-geocoding result."""
        if latitude is None or longitude is None:
            return None

        parameters: dict[str, str | float | int] = {
            "format": "jsonv2",
            "lat": latitude,
            "lon": longitude,
            "zoom": 18,
            "addressdetails": 1,
            "namedetails": 1,
            "accept-language": "cs",
        }
        if self._email:
            parameters["email"] = self._email

        try:
            async with self._request_lock:
                delay = 1.0 - (time.monotonic() - self._last_request_started)
                if delay > 0:
                    await asyncio.sleep(delay)
                self._last_request_started = time.monotonic()
                async with asyncio.timeout(20):
                    async with self._session.get(
                        self._endpoint,
                        params=parameters,
                        headers={
                            "User-Agent": self._user_agent,
                            "Accept-Language": "cs",
                            "Accept": "application/json",
                        },
                    ) as response:
                        response.raise_for_status()
                        payload = await response.json(content_type=None)
        except (TimeoutError, ClientError, ValueError) as err:
            _LOGGER.warning("Reverse geocoding failed: %s", err)
            return None

        if not isinstance(payload, dict):
            return None

        display_name = _as_nonempty_string(payload.get("display_name"))
        return {
            "name": _choose_poi_name(payload),
            "display_name": display_name,
            "osm_type": _as_nonempty_string(payload.get("osm_type")),
            "osm_id": payload.get("osm_id"),
            "category": _as_nonempty_string(payload.get("category")),
            "type": _as_nonempty_string(payload.get("type")),
            "attribution": "© OpenStreetMap contributors, ODbL",
        }


def _as_nonempty_string(value: Any) -> str | None:
    """Return stripped text or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _choose_poi_name(payload: dict[str, Any]) -> str | None:
    """Choose the most useful company/POI label from Nominatim JSON."""
    direct_name = _as_nonempty_string(payload.get("name"))
    if direct_name:
        return direct_name

    namedetails = payload.get("namedetails")
    if isinstance(namedetails, dict):
        for key in ("name:cs", "name", "official_name", "brand"):
            name = _as_nonempty_string(namedetails.get(key))
            if name:
                return name

    address = payload.get("address")
    if isinstance(address, dict):
        for key in (
            "company",
            "office",
            "shop",
            "amenity",
            "tourism",
            "leisure",
            "building",
            "industrial",
        ):
            name = _as_nonempty_string(address.get(key))
            if name:
                return name

    display_name = _as_nonempty_string(payload.get("display_name"))
    if display_name:
        return display_name.split(",", maxsplit=1)[0].strip() or None
    return None
