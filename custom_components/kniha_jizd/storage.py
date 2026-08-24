"""File-backed storage for Kniha jízd."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
from math import asin, cos, floor, radians, sin, sqrt
import os
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from homeassistant.core import HomeAssistant

from .address_rules import shorten_address
from .const import (
    LEARNED_PLACES_FILENAME,
    LEARNED_PRIVATE_RADIUS,
    LEARNED_TRANSIENT_RADIUS,
    PLACE_ROLE_PRIVATE,
    PLACE_ROLE_TRANSIENT,
    RAW_DATA_FILENAME,
    TRIP_TYPE_PRIVATE,
)

_MAX_ANCHORS_PER_PLACE = 50
_RAW_DATA_VERSION = 4
_LEARNED_PLACES_VERSION = 3


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


def effective_place_radius(place: dict[str, Any], fallback_radius: float) -> float:
    """Return a conservative matching radius for one learned place."""
    stored_radius = _optional_float(place.get("radius_m"))
    if stored_radius is not None and stored_radius > 0:
        return stored_radius
    role = str(place.get("place_role") or "")
    if role == PLACE_ROLE_TRANSIENT:
        return min(fallback_radius, LEARNED_TRANSIENT_RADIUS)
    if role == PLACE_ROLE_PRIVATE or (
        not role and place.get("trip_type") == TRIP_TYPE_PRIVATE
    ):
        return min(fallback_radius, LEARNED_PRIVATE_RADIUS)
    return fallback_radius


def places_for_map(
    document: dict[str, Any], fallback_radius: float
) -> list[dict[str, Any]]:
    """Flatten learned anchors into safe map markers with their effective zones."""
    markers: list[dict[str, Any]] = []
    places = document.get("places")
    if not isinstance(places, list):
        return markers
    for place in places:
        if not isinstance(place, dict):
            continue
        place_id = str(place.get("id") or "")
        trip_type = str(place.get("trip_type") or "") or None
        role = str(place.get("place_role") or "") or (
            PLACE_ROLE_PRIVATE if trip_type == TRIP_TYPE_PRIVATE else "client"
        )
        radius = effective_place_radius(place, fallback_radius)
        for index, anchor in enumerate(_place_anchors(place)):
            latitude = _optional_float(anchor.get("latitude"))
            longitude = _optional_float(anchor.get("longitude"))
            if latitude is None or longitude is None:
                continue
            markers.append(
                {
                    "id": f"{place_id}:{index}",
                    "place_id": place_id or None,
                    "label": place.get("label") or place.get("map_name") or "Místo",
                    "map_name": place.get("map_name"),
                    "trip_type": trip_type,
                    "place_role": role,
                    "radius_m": radius,
                    "latitude": latitude,
                    "longitude": longitude,
                    "address": anchor.get("address"),
                    "updated_at": anchor.get("updated_at") or place.get("updated_at"),
                }
            )
    return markers


def _whole_km(value: float) -> int:
    """Round a non-negative kilometre value to the nearest whole kilometre."""
    return int(floor(max(0.0, value) + 0.5))


def _raw_trusted_odometer_end(segment: dict[str, Any]) -> float | None:
    """Return only a trustworthy raw post-disconnect odometer value."""
    source = str(segment.get("odometer_completion_source") or "")
    if (
        segment.get("odometer_wait_timed_out")
        or segment.get("odometer_shared_update")
        or not source.startswith("post_disconnect_update")
    ):
        return None
    return _optional_float(segment.get("end_odometer_km"))


def _trusted_odometer_end(segment: dict[str, Any]) -> float | None:
    """Return a raw or retrospectively established odometer boundary."""
    boundary = _optional_float(segment.get("odometer_reconciliation_boundary_km"))
    if boundary is not None:
        return boundary
    if segment.get("odometer_anchor_ignored_due_to_daily_conflict"):
        return None
    return _raw_trusted_odometer_end(segment)


def _segment_distance_weight(segment: dict[str, Any]) -> float:
    """Estimate only the relative share of a combined odometer increment."""
    coordinates = (
        _optional_float(segment.get("start_latitude")),
        _optional_float(segment.get("start_longitude")),
        _optional_float(segment.get("end_latitude")),
        _optional_float(segment.get("end_longitude")),
    )
    if all(value is not None for value in coordinates):
        return max(0.1, _haversine_meters(*coordinates) / 1000)  # type: ignore[arg-type]
    raw_distance = _optional_float(
        segment.get("distance_km_raw", segment.get("distance_km"))
    )
    return max(0.1, raw_distance or 0.0)


def _set_if_changed(segment: dict[str, Any], key: str, value: Any) -> int:
    """Set one JSON field and report whether it changed."""
    if segment.get(key) == value:
        return 0
    segment[key] = value
    return 1


def _set_integer_if_changed(segment: dict[str, Any], key: str, value: int) -> int:
    """Persist whole kilometres as a JSON integer, not a decimal-looking float."""
    current = segment.get(key)
    if current == value and isinstance(current, int) and not isinstance(current, bool):
        return 0
    segment[key] = value
    return 1


def _integer_weighted_allocations(weights: list[float], total_km: int) -> list[int]:
    """Allocate whole kilometres while preserving the exact requested total."""
    if not weights:
        return []
    total_km = max(0, total_km)
    minimums = [1 if total_km >= len(weights) else 0 for _ in weights]
    remaining = total_km - sum(minimums)
    weight_total = sum(weights) or float(len(weights))
    quotas = [remaining * weight / weight_total for weight in weights]
    allocations = [minimum + floor(quota) for minimum, quota in zip(minimums, quotas)]
    leftover = total_km - sum(allocations)
    order = sorted(
        range(len(weights)),
        key=lambda index: (quotas[index] - floor(quotas[index]), weights[index]),
        reverse=True,
    )
    for index in order[:leftover]:
        allocations[index] += 1
    return allocations


def _allocate_odometer_group(
    group: list[dict[str, Any]],
    anchor_start_km: float,
    anchor_end_km: float,
    boundary_source: str,
    reconciliation_source: str | None = None,
) -> int:
    """Distribute one trusted odometer delta as whole kilometres."""
    total_km = anchor_end_km - anchor_start_km
    if total_km < -0.001 or not group:
        return 0
    total_whole_km = _whole_km(total_km)
    manual_distances = [
        _whole_km(_optional_float(segment.get("distance_km")) or 0.0)
        for segment in group
        if segment.get("manual_distance_override")
    ]
    manual_total = sum(manual_distances)
    adjustable = [
        segment for segment in group if not segment.get("manual_distance_override")
    ]
    remaining_km = max(0, total_whole_km - manual_total)
    weights = [_segment_distance_weight(segment) for segment in adjustable]
    allocations = _integer_weighted_allocations(weights, remaining_km)

    changed = 0
    allocation_index = 0
    manual_index = 0
    for segment in group:
        if "distance_km_raw" not in segment:
            changed += _set_if_changed(
                segment, "distance_km_raw", segment.get("distance_km")
            )
        hint = round(_segment_distance_weight(segment), 3)
        changed += _set_if_changed(segment, "distance_hint_km", hint)
        changed += _set_if_changed(
            segment, "distance_anchor_start_km", round(anchor_start_km, 3)
        )
        changed += _set_if_changed(
            segment, "distance_anchor_end_km", round(anchor_end_km, 3)
        )
        if segment.get("manual_distance_override"):
            changed += _set_integer_if_changed(
                segment, "distance_km", manual_distances[manual_index]
            )
            manual_index += 1
            continue
        distance = allocations[allocation_index]
        allocation_index += 1
        changed += _set_integer_if_changed(segment, "distance_km", distance)
        source = reconciliation_source or (
            "odometer_anchor_exact_whole_km"
            if len(group) == 1
            else "odometer_anchor_reconciled_gps_weighted_whole_km"
        )
        changed += _set_if_changed(segment, "distance_reconciliation_source", source)
        changed += _set_if_changed(
            segment, "distance_rounding_method", "largest_remainder_whole_km"
        )
    changed += _set_if_changed(
        group[-1],
        "odometer_reconciliation_boundary_km",
        round(anchor_end_km, 3),
    )
    changed += _set_if_changed(
        group[-1], "odometer_reconciliation_boundary_source", boundary_source
    )
    return changed


def _pop_if_present(segment: dict[str, Any], key: str) -> int:
    """Remove a field and report whether the document changed."""
    if key not in segment:
        return 0
    segment.pop(key, None)
    return 1


def _apply_authoritative_daily_anchor(
    ordered: list[dict[str, Any]],
    boundary_index: int,
    anchor_start_km: float,
    anchor_end_km: float,
    boundary_source: str,
    reason: str,
) -> int:
    """Let the newest day boundary override contradictory intermediate values."""
    prefix = ordered[: boundary_index + 1]
    if not prefix:
        return 0
    target_km = _whole_km(anchor_end_km - anchor_start_km)
    manual_total = sum(
        _whole_km(_optional_float(segment.get("distance_km")) or 0.0)
        for segment in prefix
        if segment.get("manual_distance_override")
    )
    adjustable = [
        segment for segment in prefix if not segment.get("manual_distance_override")
    ]
    if manual_total > target_km or (not adjustable and manual_total != target_km):
        return 0

    changed = 0
    for index, segment in enumerate(prefix):
        if index < len(prefix) - 1:
            changed += _pop_if_present(
                segment, "odometer_reconciliation_boundary_km"
            )
            changed += _pop_if_present(
                segment, "odometer_reconciliation_boundary_source"
            )
            if _raw_trusted_odometer_end(segment) is not None:
                changed += _set_if_changed(
                    segment,
                    "odometer_anchor_ignored_due_to_daily_conflict",
                    True,
                )
        else:
            changed += _pop_if_present(
                segment, "odometer_anchor_ignored_due_to_daily_conflict"
            )
        changed += _set_if_changed(segment, "daily_odometer_override_reason", reason)
        changed += _set_if_changed(
            segment, "daily_odometer_authoritative_total_km", target_km
        )
    changed += _allocate_odometer_group(
        prefix,
        anchor_start_km,
        anchor_end_km,
        f"daily_authoritative_{boundary_source}",
        "daily_odometer_reconciled_gps_weighted_whole_km",
    )
    return changed


def reconcile_odometer_day(
    segments: list[dict[str, Any]], terminal_anchor_km: float | None = None
) -> tuple[int, dict[str, Any]]:
    """Backfill legs and make their whole-km sum match the final day counter."""
    ordered = sorted(segments, key=lambda item: str(item.get("started_at") or ""))
    changed = 0
    first_start = next(
        (
            value
            for segment in ordered
            if (value := _optional_float(segment.get("start_odometer_km")))
            is not None
        ),
        None,
    )
    candidates = [
        (
            index,
            value,
            str(
                segment.get("odometer_reconciliation_boundary_source")
                or "trusted_segment_end"
            ),
        )
        for index, segment in enumerate(ordered)
        if (value := _trusted_odometer_end(segment)) is not None
    ]
    terminal = _optional_float(terminal_anchor_km)
    if terminal is not None and ordered:
        candidates = [candidate for candidate in candidates if candidate[0] < len(ordered) - 1]
        candidates.append((len(ordered) - 1, terminal, "next_segment_start"))
    conflicting = any(
        current[1] + 0.001 < previous[1]
        for previous, current in zip(candidates, candidates[1:])
    )
    if conflicting and first_start is not None and candidates:
        boundary_index, authoritative_end, boundary_source = candidates[-1]
        if authoritative_end >= first_start:
            changed += _apply_authoritative_daily_anchor(
                ordered,
                boundary_index,
                first_start,
                authoritative_end,
                boundary_source,
                "non_monotonic_odometer_anchors",
            )
            return changed, odometer_day_check(ordered)

    group: list[dict[str, Any]] = []
    anchor_start: float | None = None
    for segment in ordered:
        start_value = _optional_float(segment.get("start_odometer_km"))
        if not group:
            if anchor_start is None or (
                start_value is not None and abs(start_value - anchor_start) > 1.0
            ):
                anchor_start = start_value
        group.append(segment)
        anchor_end = _trusted_odometer_end(segment)
        if anchor_start is None or anchor_end is None or anchor_end < anchor_start:
            continue
        boundary_source = str(
            segment.get("odometer_reconciliation_boundary_source")
            or "trusted_segment_end"
        )
        changed += _allocate_odometer_group(
            group,
            anchor_start,
            anchor_end,
            boundary_source,
            (
                "daily_odometer_reconciled_gps_weighted_whole_km"
                if boundary_source.startswith("daily_authoritative_")
                else None
            ),
        )
        group = []
        anchor_start = anchor_end

    if group and anchor_start is not None and terminal is not None and terminal >= anchor_start:
        changed += _allocate_odometer_group(
            group, anchor_start, terminal, "next_segment_start"
        )
        group = []
        anchor_start = terminal
    for segment in group:
        if (
            not segment.get("manual_distance_override")
            and (_optional_float(segment.get("distance_km")) or 0.0) <= 0.001
        ):
            changed += _set_if_changed(
                segment,
                "distance_reconciliation_source",
                "awaiting_future_odometer_anchor",
            )
    check = odometer_day_check(ordered)
    if (
        first_start is not None
        and candidates
        and check.get("unresolved_segments") == 0
        and check.get("difference_km") not in (None, 0)
    ):
        boundary_index, authoritative_end, boundary_source = candidates[-1]
        if authoritative_end >= first_start:
            changed += _apply_authoritative_daily_anchor(
                ordered,
                boundary_index,
                first_start,
                authoritative_end,
                boundary_source,
                "daily_segment_sum_mismatch",
            )
            check = odometer_day_check(ordered)
    return changed, check


def odometer_day_check(segments: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare assigned segment kilometres with the latest trusted day anchor."""
    ordered = sorted(segments, key=lambda item: str(item.get("started_at") or ""))
    first_start = next(
        (
            value
            for segment in ordered
            if (value := _optional_float(segment.get("start_odometer_km")))
            is not None
        ),
        None,
    )
    last_end: float | None = None
    last_boundary_index = -1
    for index, segment in enumerate(ordered):
        boundary = _trusted_odometer_end(segment)
        if boundary is not None:
            last_end = boundary
            last_boundary_index = index
    verified_segments = (
        ordered[: last_boundary_index + 1] if last_boundary_index >= 0 else []
    )
    assigned_km = sum(
        _whole_km(_optional_float(item.get("distance_km")) or 0.0)
        for item in verified_segments
    )
    pending_km = sum(
        _whole_km(_optional_float(item.get("distance_km")) or 0.0)
        for item in ordered[last_boundary_index + 1 :]
    )
    anchor_delta_raw = (
        round(max(0.0, last_end - first_start), 3)
        if first_start is not None and last_end is not None
        else None
    )
    anchor_delta = (
        _whole_km(anchor_delta_raw) if anchor_delta_raw is not None else None
    )
    difference = (
        assigned_km - anchor_delta if anchor_delta is not None else None
    )
    unresolved = max(0, len(ordered) - last_boundary_index - 1)
    return {
        "start_odometer_km": first_start,
        "end_odometer_km": last_end,
        "odometer_delta_km": anchor_delta,
        "odometer_delta_raw_km": anchor_delta_raw,
        "assigned_segment_km": assigned_km,
        "pending_segment_km": pending_km,
        "difference_km": difference,
        "unresolved_segments": unresolved,
        "consistent": (
            difference == 0 and unresolved == 0
        ),
    }


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
            raw_data = self._load_raw_sync()
            changed = 0
            for segment in raw_data["segments"]:
                if not isinstance(segment, dict):
                    continue
                stored_distance = _optional_float(segment.get("distance_km"))
                if stored_distance is not None:
                    if "distance_km_raw" not in segment:
                        segment["distance_km_raw"] = stored_distance
                        changed += 1
                    changed += _set_integer_if_changed(
                        segment, "distance_km", _whole_km(stored_distance)
                    )
                for side in ("start", "end"):
                    address_key = f"{side}_address"
                    raw_key = f"{side}_address_raw"
                    current_address = segment.get(address_key)
                    if raw_key not in segment and current_address:
                        segment[raw_key] = current_address
                        changed += 1
                    if segment.get(f"{side}_address_manual"):
                        continue
                    shortened = shorten_address(segment.get(raw_key) or current_address)
                    if shortened and current_address != shortened:
                        segment[address_key] = shortened
                        changed += 1
            dates = {
                str(segment.get("date"))
                for segment in raw_data["segments"]
                if isinstance(segment, dict) and segment.get("date")
            }
            for local_date in dates:
                day_segments = [
                    segment
                    for segment in raw_data["segments"]
                    if isinstance(segment, dict)
                    and str(segment.get("date")) == local_date
                ]
                day_changed, _ = reconcile_odometer_day(day_segments)
                changed += day_changed
            if changed or raw_data.get("version") != _RAW_DATA_VERSION:
                raw_data["version"] = _RAW_DATA_VERSION
                _write_json_atomic(self.raw_path, raw_data)

        if not self.places_path.exists():
            _write_json_atomic(
                self.places_path,
                {"version": _LEARNED_PLACES_VERSION, "places": []},
            )
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
            return {"version": _LEARNED_PLACES_VERSION, "places": []}

        with self.places_path.open("r", encoding="utf-8") as file_handle:
            loaded = json.load(file_handle)

        if isinstance(loaded, list):
            return {"version": _LEARNED_PLACES_VERSION, "places": loaded}
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
            return {"version": _LEARNED_PLACES_VERSION, "places": places}
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

    async def async_reconcile_day(
        self, local_date: str, terminal_anchor_km: float | None = None
    ) -> dict[str, Any]:
        """Reconcile one day in the executor and return its odometer check."""
        async with self._lock:
            return await self.hass.async_add_executor_job(
                self._reconcile_day_sync, local_date, terminal_anchor_km
            )

    def _reconcile_day_sync(
        self, local_date: str, terminal_anchor_km: float | None = None
    ) -> dict[str, Any]:
        """Backfill combined cloud increments without blocking Home Assistant."""
        data = self._load_raw_sync()
        day_segments = [
            segment
            for segment in data["segments"]
            if isinstance(segment, dict) and str(segment.get("date")) == local_date
        ]
        changed, check = reconcile_odometer_day(day_segments, terminal_anchor_km)
        if changed:
            data["version"] = _RAW_DATA_VERSION
            _write_json_atomic(self.raw_path, data)
        return check

    async def async_update_trip(
        self,
        segment_id: str,
        purpose: str,
        trip_type: str,
        start_address: Any = None,
        end_address: Any = None,
        distance_km: Any = None,
    ) -> int:
        """Update one persisted journey and return the number of changed rows."""
        async with self._lock:
            return await self.hass.async_add_executor_job(
                self._update_trip_sync,
                segment_id,
                purpose,
                trip_type,
                start_address,
                end_address,
                distance_km,
            )

    def _update_trip_sync(
        self,
        segment_id: str,
        purpose: str,
        trip_type: str,
        start_address: Any = None,
        end_address: Any = None,
        distance_km: Any = None,
    ) -> int:
        """Apply a manual correction to every segment of the same journey."""
        data = self._load_raw_sync()
        segments: list[dict[str, Any]] = data["segments"]
        target = next(
            (segment for segment in segments if segment.get("id") == segment_id),
            None,
        )
        if target is None:
            return 0
        journey_id = target.get("journey_id")
        changed = 0
        edited_at = datetime.now(UTC).isoformat()
        for segment in segments:
            if segment.get("id") != segment_id and (
                not journey_id or segment.get("journey_id") != journey_id
            ):
                continue
            segment["purpose"] = purpose
            segment["trip_type"] = trip_type
            segment["classification_source"] = "manual_panel"
            segment["manually_edited_at"] = edited_at
            changed += 1
        if start_address is not None:
            target["start_address"] = str(start_address).strip()
            target["start_address_manual"] = True
        if end_address is not None:
            target["end_address"] = str(end_address).strip()
            target["end_address_manual"] = True
        manual_distance = _optional_float(distance_km)
        if manual_distance is not None:
            if "distance_km_raw" not in target:
                target["distance_km_raw"] = target.get("distance_km")
            target["distance_km"] = _whole_km(manual_distance)
            target["manual_distance_override"] = True
            target["distance_reconciliation_source"] = "manual_panel"
        if changed:
            target_date = str(target.get("date") or "")
            if target_date:
                reconcile_odometer_day(
                    [
                        segment
                        for segment in segments
                        if str(segment.get("date")) == target_date
                    ]
                )
            _write_json_atomic(self.raw_path, data)
        return changed

    async def async_get_statistics(self, local_date: str) -> dict[str, Any]:
        """Return compact totals for diagnostic entities and the panel."""
        async with self._lock:
            return await self.hass.async_add_executor_job(
                self._get_statistics_sync, local_date
            )

    def _get_statistics_sync(self, local_date: str) -> dict[str, Any]:
        """Calculate raw-log statistics in an executor."""
        return calculate_statistics(self._load_raw_sync()["segments"], local_date)

    async def async_get_places_for_map(
        self, fallback_radius: float
    ) -> list[dict[str, Any]]:
        """Return learned parking anchors for the authenticated panel map."""
        async with self._lock:
            return await self.hass.async_add_executor_job(
                self._get_places_for_map_sync, fallback_radius
            )

    def _get_places_for_map_sync(
        self, fallback_radius: float
    ) -> list[dict[str, Any]]:
        """Load and serialize learned map anchors in an executor."""
        return places_for_map(self._load_places_sync(), fallback_radius)

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
                place_radius = effective_place_radius(place, radius_meters)
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
        data["version"] = _LEARNED_PLACES_VERSION
        places: list[dict[str, Any]] = data["places"]
        place_id = place.get("id")
        normalized_address = _normalize_address(place.get("address"))
        normalized_label = _normalize_label(place.get("label"))
        trip_type = place.get("trip_type")
        place_role = place.get("place_role")

        replacement_index: int | None = None
        for index, existing in enumerate(places):
            if place_id and existing.get("id") == place_id:
                replacement_index = index
                break
            if (
                normalized_label
                and _normalize_label(existing.get("label")) == normalized_label
                and existing.get("trip_type") == trip_type
                and existing.get("place_role") == place_role
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
                "place_role": place.get("place_role"),
                "radius_m": place.get("radius_m"),
                "map_name": place.get("map_name"),
                "updated_at": place.get("updated_at"),
                "anchors": [new_anchor],
            }
            places.append(learned)
        else:
            learned = places[replacement_index].copy()
            previous_place_role = learned.get("place_role")
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
                    "place_role": (
                        place.get("place_role") or learned.get("place_role")
                    ),
                    "radius_m": (
                        place.get("radius_m")
                        if "radius_m" in place
                        else (
                            None
                            if place_role
                            and place_role != previous_place_role
                            else learned.get("radius_m")
                        )
                    ),
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

    def _sum_distance(items: list[dict[str, Any]], trip_type: str) -> int:
        return _whole_km(
            sum(
                _distance(segment)
                for segment in items
                if segment.get("trip_type") == trip_type
            )
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
        "today_rows": deepcopy_json(today_segments),
        "today_odometer_check": odometer_day_check(today_segments),
        "last_segment": deepcopy_json(last_segment),
    }


def deepcopy_json(value: Any) -> Any:
    """Copy JSON-compatible data without sharing nested mutable objects."""
    if value is None:
        return None
    return json.loads(json.dumps(value, ensure_ascii=False))
