"""Road routing and distance estimation for Kniha jízd."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import ClientError, ClientSession

from .address_rules import coordinate_distance_m

_LOGGER = logging.getLogger(__name__)

DEFAULT_OSRM_URL = "https://router.project-osrm.org/route/v1/driving"
DEFAULT_ROAD_FACTOR = 1.27


def estimate_road_distance_km(
    start_latitude: Any,
    start_longitude: Any,
    end_latitude: Any,
    end_longitude: Any,
    road_factor: float = DEFAULT_ROAD_FACTOR,
) -> float | None:
    """Return estimated road driving distance based on great-circle distance and road curvature."""
    crow_flies_m = coordinate_distance_m(
        start_latitude,
        start_longitude,
        end_latitude,
        end_longitude,
    )
    if crow_flies_m is None:
        return None
    if crow_flies_m < 30.0:
        return 0.0
    factor = max(1.0, min(float(road_factor), 2.5))
    return round(max(0.1, (crow_flies_m * factor) / 1000.0), 3)


class OsrmRoutingService:
    """Client for resolving driving road distances via OSRM with local fallback."""

    def __init__(
        self,
        session: ClientSession,
        base_url: str = DEFAULT_OSRM_URL,
        road_factor: float = DEFAULT_ROAD_FACTOR,
    ) -> None:
        self._session = session
        self.base_url = (base_url or DEFAULT_OSRM_URL).rstrip("/")
        self.road_factor = max(1.0, min(float(road_factor), 2.5))
        self._lock = asyncio.Lock()

    async def async_route_distance_km(
        self,
        start_latitude: Any,
        start_longitude: Any,
        end_latitude: Any,
        end_longitude: Any,
    ) -> float | None:
        """Query OSRM for driving distance, falling back to estimated road distance."""
        try:
            start_lat = float(start_latitude)
            start_lon = float(start_longitude)
            end_lat = float(end_latitude)
            end_lon = float(end_longitude)
        except (TypeError, ValueError):
            return None

        direct_m = coordinate_distance_m(start_lat, start_lon, end_lat, end_lon)
        if direct_m is None:
            return None
        if direct_m < 30.0:
            return 0.0

        endpoint = (
            f"{self.base_url}/{start_lon:.6f},{start_lat:.6f};"
            f"{end_lon:.6f},{end_lat:.6f}?overview=false"
        )

        async with self._lock:
            try:
                async with self._session.get(
                    endpoint,
                    timeout=5.0,
                    headers={"User-Agent": "HomeAssistant-kniha-jizd/1.0"},
                ) as response:
                    if response.status == 200:
                        payload = await response.json()
                        routes = payload.get("routes")
                        if isinstance(routes, list) and routes:
                            distance_m = routes[0].get("distance")
                            if isinstance(distance_m, (int, float)) and distance_m >= 0:
                                return round(float(distance_m) / 1000.0, 3)
            except (TimeoutError, ClientError, Exception) as err:
                _LOGGER.debug(
                    "OSRM routing request failed (%s), using road-factor estimation",
                    err,
                )

        # Fallback to road-factor estimated distance
        return estimate_road_distance_km(
            start_lat,
            start_lon,
            end_lat,
            end_lon,
            self.road_factor,
        )
