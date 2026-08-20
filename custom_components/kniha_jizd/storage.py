"""File-backed storage for Kniha jízd."""

from __future__ import annotations

import asyncio
import json
from math import asin, cos, radians, sin, sqrt
import os
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from homeassistant.core import HomeAssistant

from .const import LEARNED_PLACES_FILENAME, RAW_DATA_FILENAME

_MAX_ANCHORS_PER_PLACE = 50
_RAW_DATA_VERSION = 2


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


def _normalize_label(value: Any) -> str:
    """Normalize a customer label for anchor grouping."""
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


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


def _place_anchors(place: dict[str, Any]) -> list[dict[str, Any]]:
    """Return anchors from the current format plus any legacy top-level point."""
    anchors: list[dict[str, Any]] = []
    stored = place.get("anchors")
    if isinstance(stored, list):
        anchors.extend(item.copy() for item in stored if isinstance(item, dict))

    legacy = {
        "latitude": place.get("latitude"),
        "longitude": place.get("longitude"),
        "address": place.get("address"),
        "updated_at": place.get("updated_at"),
    }
    if any(legacy.get(key) is not None for key in ("latitude", "longitude", "address")):
        legacy_address = _normalize_address(legacy.get("address"))
        if not any(
            legacy_address
            and _normalize_address(anchor.get("address")) == legacy_address
            for anchor in anchors
        ):
            anchors.append(legacy)
    return anchors


def _optional_float(value: Any) -> float | None:
    """Return a finite-enough coordinate number or None."""
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


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
            _write_json_atomic(
                self.raw_path, {"version": _RAW_DATA_VERSION, "segments": []}
            )
        else:
            self._load_raw_sync()

        if not self.places_path.exists():
            _write_json_atomic(self.places_path, {"version": 2, "places": []})
        else:
            self._load_places_sync()

    def _load_raw_sync(self) -> dict[str, Any]:
        """Load and validate raw data."""
        data = _read_json(
            self.raw_path, {"version": _RAW_DATA_VERSION, "segments": []}
        )
        if not isinstance(data.get("segments"), list):
            raise ValueError(f"{self.raw_path} must contain a 'segments' list")
        return data

    def _load_places_sync(self) -> dict[str, Any]:
        """Load learned places, accepting a legacy bare mapping or list."""
        if not self.places_path.exists():
            return {"version": 2, "places": []}

        with self.places_path.open("r", encoding="utf-8") as file_handle:
            loaded = json.load(file_handle)

        if isinstance(loaded, list):
            return {"version": 2, "places": loaded}
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
            return {"version": 2, "places": places}
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
        data["version"] = _RAW_DATA_VERSION
        _write_json_atomic(self.raw_path, data)
        return True

    async def async_get_statistics(self, local_date: str) -> dict[str, Any]:
        """Return compact totals for diagnostic entities and the panel."""
        async with self._lock:
            return await self.hass.async_add_executor_job(
                self._get_statistics_sync, local_date
            )

    def _get_statistics_sync(self, local_date: str) -> dict[str, Any]:
        """Calculate raw-log statistics in an executor."""
        return calculate_statistics(self._load_raw_sync()["segments"], local_date)

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
        closest: tuple[float, dict[str, Any], dict[str, Any]] | None = None
        if latitude is not None and longitude is not None:
            for place in places:
                place_radius = _optional_float(place.get("radius_m")) or radius_meters
                for anchor in _place_anchors(place):
                    anchor_latitude = _optional_float(anchor.get("latitude"))
                    anchor_longitude = _optional_float(anchor.get("longitude"))
                    if anchor_latitude is None or anchor_longitude is None:
                        continue
                    distance = _haversine_meters(
                        latitude,
                        longitude,
                        anchor_latitude,
                        anchor_longitude,
                    )
                    if distance <= place_radius and (
                        closest is None or distance < closest[0]
                    ):
                        closest = (distance, place, anchor)
            if closest is not None:
                result = closest[1].copy()
                result["match_distance_m"] = round(closest[0], 1)
                result["matched_address"] = closest[2].get("address")
                return result
            # With valid GPS the configured circle is authoritative. An address
            # can cover a whole campus and must not create a match outside it.
            return None

        normalized = _normalize_address(address)
        if normalized:
            for place in places:
                for anchor in _place_anchors(place):
                    if _normalize_address(anchor.get("address")) == normalized:
                        result = place.copy()
                        result["matched_address"] = anchor.get("address")
                        return result
        return None

    async def async_learn_place(self, place: dict[str, Any]) -> None:
        """Add or replace a learned place."""
        async with self._lock:
            await self.hass.async_add_executor_job(
                self._learn_place_sync, place.copy()
            )

    def _learn_place_sync(self, place: dict[str, Any]) -> None:
        """Persist a customer and append a confirmed parking anchor."""
        data = self._load_places_sync()
        data["version"] = 2
        places: list[dict[str, Any]] = data["places"]
        place_id = place.get("id")
        normalized_address = _normalize_address(place.get("address"))
        normalized_label = _normalize_label(place.get("label"))
        trip_type = place.get("trip_type")

        replacement_index: int | None = None
        for index, existing in enumerate(places):
            if place_id and existing.get("id") == place_id:
                replacement_index = index
                break
            if (
                normalized_label
                and _normalize_label(existing.get("label")) == normalized_label
                and existing.get("trip_type") == trip_type
            ):
                replacement_index = index
                break

        new_anchor = {
            "latitude": place.get("latitude"),
            "longitude": place.get("longitude"),
            "address": place.get("address"),
            "updated_at": place.get("updated_at"),
        }

        if replacement_index is None:
            learned = {
                "id": place_id or uuid4().hex,
                "label": place.get("label"),
                "trip_type": trip_type,
                "map_name": place.get("map_name"),
                "updated_at": place.get("updated_at"),
                "anchors": [new_anchor],
            }
            places.append(learned)
        else:
            learned = places[replacement_index].copy()
            anchors = _place_anchors(learned)
            new_latitude = _optional_float(new_anchor.get("latitude"))
            new_longitude = _optional_float(new_anchor.get("longitude"))
            duplicate_index: int | None = None
            for index, anchor in enumerate(anchors):
                anchor_latitude = _optional_float(anchor.get("latitude"))
                anchor_longitude = _optional_float(anchor.get("longitude"))
                if (
                    new_latitude is not None
                    and new_longitude is not None
                    and anchor_latitude is not None
                    and anchor_longitude is not None
                    and _haversine_meters(
                        new_latitude,
                        new_longitude,
                        anchor_latitude,
                        anchor_longitude,
                    )
                    <= 25
                ):
                    duplicate_index = index
                    break
                if (
                    new_latitude is None
                    and normalized_address
                    and _normalize_address(anchor.get("address")) == normalized_address
                ):
                    duplicate_index = index
                    break

            if duplicate_index is None:
                anchors.append(new_anchor)
            else:
                anchors[duplicate_index] = {
                    **anchors[duplicate_index],
                    **{key: value for key, value in new_anchor.items() if value is not None},
                }

            learned.update(
                {
                    "id": learned.get("id") or place_id or uuid4().hex,
                    "label": place.get("label"),
                    "trip_type": trip_type,
                    "map_name": place.get("map_name") or learned.get("map_name"),
                    "updated_at": place.get("updated_at"),
                    "anchors": anchors[-_MAX_ANCHORS_PER_PLACE:],
                }
            )
            for legacy_key in ("latitude", "longitude", "address"):
                learned.pop(legacy_key, None)
            places[replacement_index] = learned
        _write_json_atomic(self.places_path, data)


def calculate_statistics(
    segments: list[dict[str, Any]], local_date: str
) -> dict[str, Any]:
    """Calculate stable totals from a raw segment list."""
    valid_segments = [segment for segment in segments if isinstance(segment, dict)]
    today_segments = [
        segment for segment in valid_segments if str(segment.get("date")) == local_date
    ]

    def _distance(segment: dict[str, Any]) -> float:
        value = _optional_float(segment.get("distance_km"))
        return max(0.0, value) if value is not None else 0.0

    def _sum_distance(items: list[dict[str, Any]], trip_type: str) -> float:
        return round(
            sum(
                _distance(segment)
                for segment in items
                if segment.get("trip_type") == trip_type
            ),
            3,
        )

    last_segment = max(
        valid_segments,
        key=lambda segment: str(
            segment.get("ended_at") or segment.get("started_at") or ""
        ),
        default=None,
    )
    return {
        "segments_total": len(valid_segments),
        "business_km_total": _sum_distance(valid_segments, "business"),
        "private_km_total": _sum_distance(valid_segments, "private"),
        "today_segments": len(today_segments),
        "today_business_km": _sum_distance(today_segments, "business"),
        "today_private_km": _sum_distance(today_segments, "private"),
        "last_segment": deepcopy_json(last_segment),
    }


def deepcopy_json(value: Any) -> Any:
    """Copy JSON-compatible data without sharing nested mutable objects."""
    if value is None:
        return None
    return json.loads(json.dumps(value, ensure_ascii=False))
