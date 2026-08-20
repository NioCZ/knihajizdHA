"""File-backed storage for Kniha jízd."""

from __future__ import annotations

import asyncio
import json
from math import asin, cos, radians, sin, sqrt
import os
from pathlib import Path
import re
from typing import Any

from homeassistant.core import HomeAssistant

from .const import LEARNED_PLACES_FILENAME, RAW_DATA_FILENAME


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    """Read a JSON object, returning a fresh default for an absent file."""
    if not path.exists():
        return default.copy()
    with path.open("r", encoding="utf-8") as file_handle:
        loaded = json.load(file_handle)
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return loaded


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    """Atomically replace a JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as file_handle:
        json.dump(data, file_handle, ensure_ascii=False, indent=2)
        file_handle.write("\n")
        file_handle.flush()
        os.fsync(file_handle.fileno())
    os.replace(temporary, path)


def _normalize_address(value: str | None) -> str:
    """Normalize an address for conservative exact matching."""
    return re.sub(r"\s+", " ", (value or "").strip().casefold())


def _haversine_meters(
    latitude_a: float, longitude_a: float, latitude_b: float, longitude_b: float
) -> float:
    """Calculate great-circle distance between two coordinates."""
    earth_radius_m = 6_371_000.0
    delta_latitude = radians(latitude_b - latitude_a)
    delta_longitude = radians(longitude_b - longitude_a)
    a = (
        sin(delta_latitude / 2) ** 2
        + cos(radians(latitude_a))
        * cos(radians(latitude_b))
        * sin(delta_longitude / 2) ** 2
    )
    return 2 * earth_radius_m * asin(sqrt(a))


class KnihaJizdRepository:
    """Serialize access to the two user-visible JSON files."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize paths and the shared file lock."""
        self.hass = hass
        self.raw_path = Path(hass.config.path(RAW_DATA_FILENAME))
        self.places_path = Path(hass.config.path(LEARNED_PLACES_FILENAME))
        self._lock = asyncio.Lock()

    async def async_initialize(self) -> None:
        """Create empty files when needed and validate existing files."""
        async with self._lock:
            await self.hass.async_add_executor_job(self._initialize_sync)

    def _initialize_sync(self) -> None:
        """Initialize the files in an executor."""
        if not self.raw_path.exists():
            _write_json_atomic(self.raw_path, {"version": 1, "segments": []})
        else:
            self._load_raw_sync()

        if not self.places_path.exists():
            _write_json_atomic(self.places_path, {"version": 1, "places": []})
        else:
            self._load_places_sync()

    def _load_raw_sync(self) -> dict[str, Any]:
        """Load and validate raw data."""
        data = _read_json(self.raw_path, {"version": 1, "segments": []})
        if not isinstance(data.get("segments"), list):
            raise ValueError(f"{self.raw_path} must contain a 'segments' list")
        return data

    def _load_places_sync(self) -> dict[str, Any]:
        """Load learned places, accepting a legacy bare mapping or list."""
        if not self.places_path.exists():
            return {"version": 1, "places": []}

        with self.places_path.open("r", encoding="utf-8") as file_handle:
            loaded = json.load(file_handle)

        if isinstance(loaded, list):
            return {"version": 1, "places": loaded}
        if isinstance(loaded, dict) and isinstance(loaded.get("places"), list):
            return loaded
        if isinstance(loaded, dict):
            places: list[dict[str, Any]] = []
            for key, value in loaded.items():
                if not isinstance(value, dict):
                    continue
                place = value.copy()
                place.setdefault("label", str(key))
                places.append(place)
            return {"version": 1, "places": places}
        raise ValueError(f"{self.places_path} has an unsupported structure")

    async def async_append_segment(self, segment: dict[str, Any]) -> bool:
        """Append a segment once. Return False when the ID already exists."""
        async with self._lock:
            return await self.hass.async_add_executor_job(
                self._append_segment_sync, segment.copy()
            )

    def _append_segment_sync(self, segment: dict[str, Any]) -> bool:
        """Append in an executor."""
        data = self._load_raw_sync()
        segments: list[dict[str, Any]] = data["segments"]
        segment_id = segment.get("id")
        if any(item.get("id") == segment_id for item in segments):
            return False
        segments.append(segment)
        _write_json_atomic(self.raw_path, data)
        return True

    async def async_find_place(
        self,
        latitude: float | None,
        longitude: float | None,
        address: str | None,
        radius_meters: float,
    ) -> dict[str, Any] | None:
        """Find the closest coordinate match or an exact normalized address."""
        async with self._lock:
            return await self.hass.async_add_executor_job(
                self._find_place_sync,
                latitude,
                longitude,
                address,
                radius_meters,
            )

    def _find_place_sync(
        self,
        latitude: float | None,
        longitude: float | None,
        address: str | None,
        radius_meters: float,
    ) -> dict[str, Any] | None:
        """Find a learned place in an executor."""
        places = self._load_places_sync()["places"]
        closest: tuple[float, dict[str, Any]] | None = None
        if latitude is not None and longitude is not None:
            for place in places:
                try:
                    distance = _haversine_meters(
                        latitude,
                        longitude,
                        float(place["latitude"]),
                        float(place["longitude"]),
                    )
                except (KeyError, TypeError, ValueError):
                    continue
                if distance <= radius_meters and (
                    closest is None or distance < closest[0]
                ):
                    closest = (distance, place)
            if closest is not None:
                result = closest[1].copy()
                result["match_distance_m"] = round(closest[0], 1)
                return result

        normalized = _normalize_address(address)
        if normalized:
            for place in places:
                if _normalize_address(place.get("address")) == normalized:
                    return place.copy()
        return None

    async def async_learn_place(self, place: dict[str, Any]) -> None:
        """Add or replace a learned place."""
        async with self._lock:
            await self.hass.async_add_executor_job(
                self._learn_place_sync, place.copy()
            )

    def _learn_place_sync(self, place: dict[str, Any]) -> None:
        """Persist a learned place in an executor."""
        data = self._load_places_sync()
        places: list[dict[str, Any]] = data["places"]
        place_id = place.get("id")
        normalized = _normalize_address(place.get("address"))

        replacement_index: int | None = None
        for index, existing in enumerate(places):
            if place_id and existing.get("id") == place_id:
                replacement_index = index
                break
            if normalized and _normalize_address(existing.get("address")) == normalized:
                replacement_index = index
                break

        if replacement_index is None:
            places.append(place)
        else:
            places[replacement_index] = place
        _write_json_atomic(self.places_path, data)
