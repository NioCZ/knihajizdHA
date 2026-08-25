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
    PLACE_ROLE_CLIENT,
    PLACE_ROLE_MIXED,
    PLACE_ROLE_PRIVATE,
    PLACE_ROLE_RETURN,
    PLACE_ROLE_TRANSIENT,
    RAW_DATA_FILENAME,
    TRIP_TYPE_BUSINESS,
    TRIP_TYPE_CONTEXTUAL,
    TRIP_TYPE_PRIVATE,
    TRIP_TYPE_UNCLASSIFIED,
)

_PHYSICAL_POINT_MERGE_DISTANCE_M = 25
_MAX_ANCHORS_PER_PLACE = 1
_RAW_DATA_VERSION = 5
_LEARNED_PLACES_VERSION = 6


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


def place_trip_types(place: dict[str, Any]) -> list[str]:
    """Return the durable business/private classifications of one place."""
    selected: set[str] = set()
    stored = place.get("trip_types")
    if isinstance(stored, list):
        selected.update(
            str(item)
            for item in stored
            if item in {TRIP_TYPE_BUSINESS, TRIP_TYPE_PRIVATE}
        )
    trip_type = place.get("trip_type")
    if trip_type in {TRIP_TYPE_BUSINESS, TRIP_TYPE_PRIVATE}:
        selected.add(str(trip_type))
    return [
        trip_type
        for trip_type in (TRIP_TYPE_BUSINESS, TRIP_TYPE_PRIVATE)
        if trip_type in selected
    ]


def learned_place_behavior(
    place: dict[str, Any], has_business_return_context: bool
) -> str:
    """Choose automatic handling for one known physical place."""
    role = str(place.get("place_role") or "")
    trip_types = place_trip_types(place)
    if role == PLACE_ROLE_TRANSIENT or (
        has_business_return_context
        and trip_types == [TRIP_TYPE_PRIVATE]
    ):
        return "transient"
    if len(trip_types) > 1 or role == PLACE_ROLE_MIXED:
        return "confirm"
    if role == PLACE_ROLE_RETURN:
        return "return"
    if role == PLACE_ROLE_PRIVATE or trip_types == [TRIP_TYPE_PRIVATE]:
        return "private"
    return "business"


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


def _validated_place_radius(value: Any) -> float:
    """Return a finite user-editable radius inside conservative hard limits."""
    radius = _optional_float(value)
    if radius is None or not 25 <= radius <= 5000:
        raise ValueError("radius_m must be between 25 and 5000")
    return round(radius, 1)


def effective_place_radius(
    place: dict[str, Any],
    fallback_radius: float,
    private_radius: float = LEARNED_PRIVATE_RADIUS,
    transient_radius: float = LEARNED_TRANSIENT_RADIUS,
) -> float:
    """Return a conservative matching radius for one learned place."""
    stored_radius = _optional_float(place.get("radius_m"))
    if stored_radius is not None and stored_radius > 0:
        return stored_radius
    role = str(place.get("place_role") or "")
    if len(place_trip_types(place)) > 1 or role == PLACE_ROLE_MIXED:
        return min(fallback_radius, private_radius)
    if role == PLACE_ROLE_TRANSIENT:
        return min(fallback_radius, transient_radius)
    if role == PLACE_ROLE_PRIVATE or (
        not role and place.get("trip_type") == TRIP_TYPE_PRIVATE
    ):
        return min(fallback_radius, private_radius)
    return fallback_radius


def places_for_map(
    document: dict[str, Any],
    fallback_radius: float,
    private_radius: float = LEARNED_PRIVATE_RADIUS,
    transient_radius: float = LEARNED_TRANSIENT_RADIUS,
) -> list[dict[str, Any]]:
    """Flatten durable learned anchors into map markers with effective zones."""
    markers: list[dict[str, Any]] = []
    places = document.get("places")
    if not isinstance(places, list):
        return markers
    for place in places:
        if not isinstance(place, dict):
            continue
        place_id = str(place.get("id") or "")
        trip_type = str(place.get("trip_type") or "") or None
        trip_types = place_trip_types(place)
        role = (
            PLACE_ROLE_MIXED
            if len(trip_types) > 1
            else str(place.get("place_role") or "")
        ) or (
            PLACE_ROLE_PRIVATE if trip_type == TRIP_TYPE_PRIVATE else "client"
        )
        if role in {PLACE_ROLE_RETURN, PLACE_ROLE_TRANSIENT}:
            # Return and short-stop anchors remain internal journey context. They
            # are not durable user-facing place categories and would duplicate
            # configured home/company or otherwise confuse the place map.
            continue
        radius = effective_place_radius(
            place, fallback_radius, private_radius, transient_radius
        )
        for index, anchor in enumerate(_place_anchors(place)):
            latitude = _optional_float(anchor.get("latitude"))
            longitude = _optional_float(anchor.get("longitude"))
            if latitude is None or longitude is None:
                continue
            markers.append(
                {
                    "id": f"{place_id}:{index}",
                    "place_id": place_id or None,
                    "anchor_index": index,
                    "label": place.get("label") or place.get("map_name") or "Místo",
                    "map_name": place.get("map_name"),
                    "trip_type": trip_type,
                    "trip_types": trip_types,
                    "place_role": role,
                    "radius_m": radius,
                    "latitude": latitude,
                    "longitude": longitude,
                    "address": anchor.get("address"),
                    "updated_at": anchor.get("updated_at") or place.get("updated_at"),
                }
            )
    return markers


def suppress_configured_place_duplicates(
    markers: list[dict[str, Any]],
    configured_places: list[dict[str, Any]],
    direct_overlap_meters: float = 75,
) -> list[dict[str, Any]]:
    """Prefer a configured marker over learned markers for the same location."""
    visible: list[dict[str, Any]] = []
    for marker in markers:
        latitude = _optional_float(marker.get("latitude"))
        longitude = _optional_float(marker.get("longitude"))
        duplicate = False
        if latitude is not None and longitude is not None:
            for configured in configured_places:
                configured_latitude = _optional_float(configured.get("latitude"))
                configured_longitude = _optional_float(configured.get("longitude"))
                if configured_latitude is None or configured_longitude is None:
                    continue
                distance = _haversine_meters(
                    latitude,
                    longitude,
                    configured_latitude,
                    configured_longitude,
                )
                configured_radius = _optional_float(configured.get("radius_m")) or 0
                if distance <= max(direct_overlap_meters, configured_radius):
                    duplicate = True
                    break
        if not duplicate:
            visible.append(marker)
    return visible


def _places_overlap(
    first: dict[str, Any],
    second: dict[str, Any],
    max_distance_meters: float = _PHYSICAL_POINT_MERGE_DISTANCE_M,
) -> bool:
    """Return whether two records describe the same physical parking point."""
    first_anchors = _place_anchors(first)
    second_anchors = _place_anchors(second)
    compared_coordinates = False
    for first_anchor in first_anchors:
        first_latitude = _optional_float(first_anchor.get("latitude"))
        first_longitude = _optional_float(first_anchor.get("longitude"))
        if first_latitude is None or first_longitude is None:
            continue
        for second_anchor in second_anchors:
            second_latitude = _optional_float(second_anchor.get("latitude"))
            second_longitude = _optional_float(second_anchor.get("longitude"))
            if second_latitude is None or second_longitude is None:
                continue
            compared_coordinates = True
            if (
                _haversine_meters(
                    first_latitude,
                    first_longitude,
                    second_latitude,
                    second_longitude,
                )
                <= max_distance_meters
            ):
                return True
    if compared_coordinates:
        return False
    first_addresses = {
        _normalize_address(anchor.get("address")) for anchor in first_anchors
    } - {""}
    second_addresses = {
        _normalize_address(anchor.get("address")) for anchor in second_anchors
    } - {""}
    return bool(first_addresses & second_addresses)


def _merge_place_records(
    existing: dict[str, Any], incoming: dict[str, Any]
) -> dict[str, Any]:
    """Merge two colocated records without creating a second map point."""
    merged = existing.copy()
    anchors = _place_anchors(existing)
    for candidate in _place_anchors(incoming):
        candidate_latitude = _optional_float(candidate.get("latitude"))
        candidate_longitude = _optional_float(candidate.get("longitude"))
        duplicate_index: int | None = None
        for index, anchor in enumerate(anchors):
            anchor_latitude = _optional_float(anchor.get("latitude"))
            anchor_longitude = _optional_float(anchor.get("longitude"))
            if (
                candidate_latitude is not None
                and candidate_longitude is not None
                and anchor_latitude is not None
                and anchor_longitude is not None
                and _haversine_meters(
                    candidate_latitude,
                    candidate_longitude,
                    anchor_latitude,
                    anchor_longitude,
                )
                <= 25
            ) or (
                candidate_latitude is None
                and _normalize_address(candidate.get("address"))
                and _normalize_address(candidate.get("address"))
                == _normalize_address(anchor.get("address"))
            ):
                duplicate_index = index
                break
        if duplicate_index is None:
            anchors.append(candidate.copy())
        else:
            anchors[duplicate_index] = {
                **anchors[duplicate_index],
                **{key: value for key, value in candidate.items() if value is not None},
            }

    for key in ("label", "map_name", "updated_at"):
        if incoming.get(key):
            merged[key] = incoming[key]
    combined_trip_types = [
        trip_type
        for trip_type in (TRIP_TYPE_BUSINESS, TRIP_TYPE_PRIVATE)
        if trip_type in {*place_trip_types(existing), *place_trip_types(incoming)}
    ]
    merged["trip_types"] = combined_trip_types
    incoming_role = incoming.get("place_role")
    existing_role = existing.get("place_role")
    if len(combined_trip_types) > 1:
        merged["trip_type"] = TRIP_TYPE_CONTEXTUAL
        merged["place_role"] = PLACE_ROLE_MIXED
        merged["radius_m"] = min(
            effective_place_radius(existing, LEARNED_PRIVATE_RADIUS),
            effective_place_radius(incoming, LEARNED_PRIVATE_RADIUS),
        )
    elif combined_trip_types:
        merged["trip_type"] = combined_trip_types[0]
        if incoming_role and not (
            incoming_role == PLACE_ROLE_TRANSIENT
            and existing_role not in {None, PLACE_ROLE_TRANSIENT}
        ):
            merged["place_role"] = incoming_role
        elif existing_role:
            merged["place_role"] = existing_role
        if incoming.get("radius_m") is not None:
            merged["radius_m"] = incoming["radius_m"]
    else:
        merged["trip_type"] = incoming.get("trip_type") or existing.get("trip_type")
        merged["place_role"] = incoming_role or existing_role
        if incoming.get("radius_m") is not None:
            merged["radius_m"] = incoming["radius_m"]
    merged["transient_capable"] = bool(
        existing.get("transient_capable") or incoming.get("transient_capable")
    )
    if incoming.get("transient_kind") or existing.get("transient_kind"):
        merged["transient_kind"] = incoming.get("transient_kind") or existing.get(
            "transient_kind"
        )
    merged["anchors"] = anchors[-_MAX_ANCHORS_PER_PLACE:]
    for legacy_key in ("latitude", "longitude", "address"):
        merged.pop(legacy_key, None)
    return merged


def split_multi_anchor_places(document: dict[str, Any]) -> bool:
    """Migrate every geographically distinct anchor to its own place record."""
    source = document.get("places")
    if not isinstance(source, list):
        return False
    split_places: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for raw_place in source:
        if not isinstance(raw_place, dict):
            continue
        place = raw_place.copy()
        anchors: list[dict[str, Any]] = []
        for candidate in _place_anchors(place):
            duplicate_index = next(
                (
                    index
                    for index, anchor in enumerate(anchors)
                    if _places_overlap(
                        {"anchors": [anchor]},
                        {"anchors": [candidate]},
                    )
                ),
                None,
            )
            if duplicate_index is None:
                anchors.append(candidate.copy())
            else:
                anchors[duplicate_index] = {
                    **anchors[duplicate_index],
                    **{
                        key: value
                        for key, value in candidate.items()
                        if value is not None
                    },
                }

        records = anchors or [None]
        original_id = str(place.get("id") or "")
        for anchor_index, anchor in enumerate(records):
            point = place.copy()
            point_id = original_id if anchor_index == 0 else ""
            if not point_id or point_id in used_ids:
                point_id = uuid4().hex
            used_ids.add(point_id)
            point["id"] = point_id
            point["trip_types"] = place_trip_types(point)
            point["anchors"] = [anchor.copy()] if anchor is not None else []
            if anchor is not None and anchor.get("updated_at"):
                point["updated_at"] = anchor["updated_at"]
            for legacy_key in ("latitude", "longitude", "address"):
                point.pop(legacy_key, None)
            split_places.append(point)

    changed = (
        split_places != source
        or document.get("version") != _LEARNED_PLACES_VERSION
    )
    document["version"] = _LEARNED_PLACES_VERSION
    document["places"] = split_places
    return changed


def consolidate_learned_places(document: dict[str, Any]) -> bool:
    """Keep one record per physical point and merge only GPS duplicates."""
    split_changed = split_multi_anchor_places(document)
    source = document.get("places")
    if not isinstance(source, list):
        return False
    consolidated: list[dict[str, Any]] = []
    for raw_place in source:
        if not isinstance(raw_place, dict):
            continue
        place = raw_place.copy()
        place["trip_types"] = place_trip_types(place)
        place["anchors"] = _place_anchors(place)[-_MAX_ANCHORS_PER_PLACE:]
        for legacy_key in ("latitude", "longitude", "address"):
            place.pop(legacy_key, None)
        replacement_index: int | None = None
        for index, existing in enumerate(consolidated):
            if _places_overlap(existing, place):
                replacement_index = index
                break
        if replacement_index is None:
            existing_ids = {str(item.get("id") or "") for item in consolidated}
            if not place.get("id") or str(place.get("id")) in existing_ids:
                place["id"] = uuid4().hex
            consolidated.append(place)
        else:
            consolidated[replacement_index] = _merge_place_records(
                consolidated[replacement_index], place
            )
    changed = (
        split_changed
        or consolidated != source
        or document.get("version") != _LEARNED_PLACES_VERSION
    )
    document["version"] = _LEARNED_PLACES_VERSION
    document["places"] = consolidated
    return changed


def migrate_return_places(document: dict[str, Any]) -> bool:
    """Remove the legacy return-place role while preserving real classifications."""
    source = document.get("places")
    if not isinstance(source, list):
        return False
    migrated: list[dict[str, Any]] = []
    changed = False
    for raw_place in source:
        if not isinstance(raw_place, dict):
            changed = True
            continue
        place = raw_place.copy()
        if place.get("place_role") != PLACE_ROLE_RETURN:
            migrated.append(place)
            continue
        trip_types = place_trip_types(place)
        changed = True
        if not trip_types:
            # A pure return anchor carries no durable meaning once return is stored
            # on the trip itself.
            continue
        if len(trip_types) > 1:
            place["trip_type"] = TRIP_TYPE_CONTEXTUAL
            place["trip_types"] = [TRIP_TYPE_BUSINESS, TRIP_TYPE_PRIVATE]
            place["place_role"] = PLACE_ROLE_MIXED
        elif trip_types[0] == TRIP_TYPE_PRIVATE:
            place["trip_type"] = TRIP_TYPE_PRIVATE
            place["trip_types"] = [TRIP_TYPE_PRIVATE]
            place["place_role"] = PLACE_ROLE_PRIVATE
        else:
            place["trip_type"] = TRIP_TYPE_BUSINESS
            place["trip_types"] = [TRIP_TYPE_BUSINESS]
            place["place_role"] = PLACE_ROLE_CLIENT
        migrated.append(place)
    if migrated != source:
        document["places"] = migrated
    return changed


def _classification_for_place(place: dict[str, Any]) -> str:
    """Return the panel classification identifier for one stored place."""
    role = str(place.get("place_role") or "")
    trip_types = place_trip_types(place)
    if role == PLACE_ROLE_TRANSIENT:
        return "transient"
    if role == PLACE_ROLE_MIXED or len(trip_types) > 1:
        return "mixed"
    if role == PLACE_ROLE_PRIVATE or trip_types == [TRIP_TYPE_PRIVATE]:
        return "private"
    return "business"


def _apply_place_classification(
    place: dict[str, Any], classification: str, radius_m: float
) -> None:
    """Set exactly one explicit user-selected place classification."""
    if classification == "business":
        place.update(
            trip_type=TRIP_TYPE_BUSINESS,
            trip_types=[TRIP_TYPE_BUSINESS],
            place_role=PLACE_ROLE_CLIENT,
        )
    elif classification == "private":
        place.update(
            trip_type=TRIP_TYPE_PRIVATE,
            trip_types=[TRIP_TYPE_PRIVATE],
            place_role=PLACE_ROLE_PRIVATE,
        )
    elif classification == "mixed":
        place.update(
            trip_type=TRIP_TYPE_CONTEXTUAL,
            trip_types=[TRIP_TYPE_BUSINESS, TRIP_TYPE_PRIVATE],
            place_role=PLACE_ROLE_MIXED,
        )
    elif classification == "transient":
        place.update(
            trip_type=TRIP_TYPE_CONTEXTUAL,
            trip_types=[],
            place_role=PLACE_ROLE_TRANSIENT,
        )
    else:
        raise ValueError("classification must be business, private, mixed or transient")
    place["radius_m"] = radius_m
    place["updated_at"] = datetime.now(UTC).isoformat()


def places_for_management(
    document: dict[str, Any],
    client_radius: float,
    private_radius: float,
    transient_radius: float,
) -> list[dict[str, Any]]:
    """Serialize editable place records without exposing legacy return anchors."""
    result: list[dict[str, Any]] = []
    for raw_place in document.get("places", []):
        if not isinstance(raw_place, dict) or raw_place.get("place_role") == PLACE_ROLE_RETURN:
            continue
        place = raw_place.copy()
        classification = _classification_for_place(place)
        anchors = _place_anchors(place)
        result.append(
            {
                "id": str(place.get("id") or ""),
                "label": place.get("label") or place.get("map_name") or "Místo",
                "map_name": place.get("map_name"),
                "classification": classification,
                "trip_types": place_trip_types(place),
                "place_role": place.get("place_role"),
                "radius_m": effective_place_radius(
                    place, client_radius, private_radius, transient_radius
                ),
                "anchor_count": len(anchors),
                "anchors": deepcopy_json(anchors),
                "transient_capable": bool(place.get("transient_capable")),
                "updated_at": place.get("updated_at"),
            }
        )
    return sorted(result, key=lambda item: str(item["label"]).casefold())


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
            places_data = self._load_places_sync()
            changed = migrate_return_places(places_data)
            changed = consolidate_learned_places(places_data) or changed
            if changed:
                _write_json_atomic(self.places_path, places_data)

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
            segment["classification_explanation"] = (
                "Typ jízdy byl ručně potvrzen v administračním panelu."
            )
            segment["manually_edited_at"] = edited_at
            segment["needs_review"] = False
            segment.pop("review_reason", None)
            changed += 1
        if (
            start_address is not None
            and str(start_address).strip()
            != str(target.get("start_address") or "").strip()
        ):
            target["start_address"] = str(start_address).strip()
            target["start_address_manual"] = True
        if (
            end_address is not None
            and str(end_address).strip()
            != str(target.get("end_address") or "").strip()
        ):
            target["end_address"] = str(end_address).strip()
            target["end_address_manual"] = True
        manual_distance = _optional_float(distance_km)
        current_distance = _optional_float(target.get("distance_km"))
        if (
            manual_distance is not None
            and (
                target.get("manual_distance_override")
                or current_distance is None
                or _whole_km(manual_distance) != _whole_km(current_distance)
            )
        ):
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

    async def async_get_history(
        self, month: str, selected_date: str
    ) -> dict[str, Any]:
        """Return calendar totals and persisted rows for one selected day."""
        async with self._lock:
            return await self.hass.async_add_executor_job(
                self._get_history_sync, month, selected_date
            )

    def _get_history_sync(self, month: str, selected_date: str) -> dict[str, Any]:
        """Build history data from the raw log in an executor."""
        return calculate_history(
            self._load_raw_sync()["segments"], month, selected_date
        )

    async def async_get_places_for_map(
        self,
        fallback_radius: float,
        private_radius: float = LEARNED_PRIVATE_RADIUS,
        transient_radius: float = LEARNED_TRANSIENT_RADIUS,
    ) -> list[dict[str, Any]]:
        """Return learned parking anchors for the authenticated panel map."""
        async with self._lock:
            return await self.hass.async_add_executor_job(
                self._get_places_for_map_sync,
                fallback_radius,
                private_radius,
                transient_radius,
            )

    def _get_places_for_map_sync(
        self,
        fallback_radius: float,
        private_radius: float = LEARNED_PRIVATE_RADIUS,
        transient_radius: float = LEARNED_TRANSIENT_RADIUS,
    ) -> list[dict[str, Any]]:
        """Load and serialize learned map anchors in an executor."""
        return places_for_map(
            self._load_places_sync(), fallback_radius, private_radius, transient_radius
        )

    async def async_get_managed_places(
        self, client_radius: float, private_radius: float, transient_radius: float
    ) -> list[dict[str, Any]]:
        """Return editable learned places for the administration panel."""
        async with self._lock:
            return await self.hass.async_add_executor_job(
                self._get_managed_places_sync,
                client_radius,
                private_radius,
                transient_radius,
            )

    def _get_managed_places_sync(
        self, client_radius: float, private_radius: float, transient_radius: float
    ) -> list[dict[str, Any]]:
        return places_for_management(
            self._load_places_sync(), client_radius, private_radius, transient_radius
        )

    async def async_update_place(
        self, place_id: str, label: str, classification: str, radius_m: float
    ) -> dict[str, Any]:
        """Update one learned place explicitly."""
        async with self._lock:
            return await self.hass.async_add_executor_job(
                self._update_place_sync,
                place_id,
                label,
                classification,
                radius_m,
            )

    def _update_place_sync(
        self, place_id: str, label: str, classification: str, radius_m: float
    ) -> dict[str, Any]:
        data = self._load_places_sync()
        places: list[dict[str, Any]] = data["places"]
        target = next((item for item in places if str(item.get("id")) == place_id), None)
        if target is None:
            raise ValueError("place was not found")
        normalized_label = label.strip()
        if not normalized_label:
            raise ValueError("label cannot be empty")
        radius = _validated_place_radius(radius_m)
        target["label"] = normalized_label
        _apply_place_classification(target, classification, radius)
        data["version"] = _LEARNED_PLACES_VERSION
        _write_json_atomic(self.places_path, data)
        return {"updated": place_id}

    async def async_delete_place(self, place_id: str) -> dict[str, Any]:
        """Delete one learned place while retaining historical trips."""
        async with self._lock:
            return await self.hass.async_add_executor_job(
                self._delete_place_sync, place_id
            )

    def _delete_place_sync(self, place_id: str) -> dict[str, Any]:
        data = self._load_places_sync()
        places: list[dict[str, Any]] = data["places"]
        remaining = [item for item in places if str(item.get("id")) != place_id]
        if len(remaining) == len(places):
            raise ValueError("place was not found")
        data["places"] = remaining
        data["version"] = _LEARNED_PLACES_VERSION
        _write_json_atomic(self.places_path, data)
        return {"deleted": place_id}

    async def async_delete_place_anchor(
        self, place_id: str, anchor_index: int
    ) -> dict[str, Any]:
        """Delete one physical point without deleting its whole logical place."""
        async with self._lock:
            return await self.hass.async_add_executor_job(
                self._delete_place_anchor_sync, place_id, anchor_index
            )

    def _delete_place_anchor_sync(
        self, place_id: str, anchor_index: int
    ) -> dict[str, Any]:
        data = self._load_places_sync()
        places: list[dict[str, Any]] = data["places"]
        target = next((item for item in places if str(item.get("id")) == place_id), None)
        if target is None:
            raise ValueError("place was not found")
        anchors = _place_anchors(target)
        if anchor_index < 0 or anchor_index >= len(anchors):
            raise ValueError("anchor was not found")

        if len(anchors) == 1:
            data["places"] = [item for item in places if item is not target]
            place_deleted = True
        else:
            target["anchors"] = [
                anchor for index, anchor in enumerate(anchors) if index != anchor_index
            ]
            for legacy_key in ("latitude", "longitude", "address"):
                target.pop(legacy_key, None)
            target["updated_at"] = datetime.now(UTC).isoformat()
            place_deleted = False

        data["version"] = _LEARNED_PLACES_VERSION
        _write_json_atomic(self.places_path, data)
        return {
            "deleted_anchor": anchor_index,
            "place_id": place_id,
            "place_deleted": place_deleted,
        }

    async def async_merge_places(
        self,
        place_ids: list[str],
        label: str | None,
        classification: str | None,
        radius_m: float | None,
    ) -> dict[str, Any]:
        """Merge selected colocated GPS duplicates into one physical point."""
        async with self._lock:
            return await self.hass.async_add_executor_job(
                self._merge_places_sync,
                place_ids,
                label,
                classification,
                radius_m,
            )

    def _merge_places_sync(
        self,
        place_ids: list[str],
        label: str | None,
        classification: str | None,
        radius_m: float | None,
    ) -> dict[str, Any]:
        unique_ids = list(dict.fromkeys(str(item) for item in place_ids if str(item)))
        if len(unique_ids) < 2:
            raise ValueError("select at least two places to merge")
        data = self._load_places_sync()
        places: list[dict[str, Any]] = data["places"]
        selected = [
            next((item for item in places if str(item.get("id")) == place_id), None)
            for place_id in unique_ids
        ]
        if any(item is None for item in selected):
            raise ValueError("one or more selected places were not found")
        if any(
            not _places_overlap(selected[0], incoming)  # type: ignore[arg-type]
            for incoming in selected[1:]
        ):
            raise ValueError(
                "Vybraná místa nejsou stejný fyzický bod (maximum je 25 m)."
            )
        merged = selected[0].copy()  # type: ignore[union-attr]
        target_id = str(merged.get("id") or unique_ids[0])
        for incoming in selected[1:]:
            merged = _merge_place_records(merged, incoming)  # type: ignore[arg-type]
        merged["id"] = target_id
        final_label = str(label or merged.get("label") or "").strip()
        if not final_label:
            raise ValueError("label cannot be empty")
        merged["label"] = final_label
        final_classification = classification or _classification_for_place(merged)
        final_radius = _validated_place_radius(
            radius_m
            if radius_m is not None
            else effective_place_radius(merged, LEARNED_PRIVATE_RADIUS)
        )
        _apply_place_classification(merged, final_classification, final_radius)
        selected_ids = set(unique_ids)
        data["places"] = [
            merged if str(item.get("id")) == target_id else item
            for item in places
            if str(item.get("id")) not in selected_ids
            or str(item.get("id")) == target_id
        ]
        data["version"] = _LEARNED_PLACES_VERSION
        consolidate_learned_places(data)
        _write_json_atomic(self.places_path, data)
        return {"merged": unique_ids, "place_id": target_id}

    async def async_sync_place_from_trip(
        self,
        segment_id: str,
        purpose: str,
        trip_type: str,
        client_radius: float,
        private_radius: float,
    ) -> dict[str, Any]:
        """Apply a manual historical correction to the destination's learned place."""
        async with self._lock:
            return await self.hass.async_add_executor_job(
                self._sync_place_from_trip_sync,
                segment_id,
                purpose,
                trip_type,
                client_radius,
                private_radius,
            )

    def _sync_place_from_trip_sync(
        self,
        segment_id: str,
        purpose: str,
        trip_type: str,
        client_radius: float,
        private_radius: float,
    ) -> dict[str, Any]:
        raw = self._load_raw_sync()
        segments: list[dict[str, Any]] = raw["segments"]
        selected = next(
            (item for item in segments if str(item.get("id")) == segment_id), None
        )
        if selected is None:
            raise ValueError("trip segment was not found")
        journey_id = selected.get("journey_id")
        journey = [
            item
            for item in segments
            if item is selected or (journey_id and item.get("journey_id") == journey_id)
        ]
        destinations = [
            item for item in journey if item.get("journey_role") != "transient_stop"
        ]
        target = max(
            destinations or [selected], key=lambda item: str(item.get("ended_at") or "")
        )
        if target.get("configured_place") in {"home", "company"}:
            return {"place_updated": False, "reason": "configured_place"}
        latitude = _optional_float(target.get("end_latitude"))
        longitude = _optional_float(target.get("end_longitude"))
        address = target.get("end_address_raw") or target.get("end_address")
        has_coordinates = latitude is not None and longitude is not None
        if not has_coordinates and not address:
            return {"place_updated": False, "reason": "missing_destination"}

        data = self._load_places_sync()
        migrate_return_places(data)
        consolidate_learned_places(data)
        places: list[dict[str, Any]] = data["places"]
        matched_id = str(target.get("matched_place_id") or "")
        match = next(
            (item for item in places if matched_id and str(item.get("id")) == matched_id),
            None,
        )
        if match is None:
            closest: tuple[float, dict[str, Any]] | None = None
            if has_coordinates:
                for place in places:
                    for anchor in _place_anchors(place):
                        anchor_latitude = _optional_float(anchor.get("latitude"))
                        anchor_longitude = _optional_float(anchor.get("longitude"))
                        if anchor_latitude is None or anchor_longitude is None:
                            continue
                        distance = _haversine_meters(
                            latitude, longitude, anchor_latitude, anchor_longitude
                        )
                        if distance <= max(client_radius, private_radius) and (
                            closest is None or distance < closest[0]
                        ):
                            closest = (distance, place)
                match = closest[1] if closest is not None else None
            else:
                normalized_address = _normalize_address(str(address or ""))
                if normalized_address:
                    match = next(
                        (
                            place
                            for place in places
                            if any(
                                _normalize_address(anchor.get("address"))
                                == normalized_address
                                for anchor in _place_anchors(place)
                            )
                        ),
                        None,
                    )

        classification = "private" if trip_type == TRIP_TYPE_PRIVATE else "business"
        radius = private_radius if classification == "private" else client_radius
        label = (
            str(
                target.get("map_estimate")
                or target.get("end_address")
                or "Soukromé místo"
            )
            if classification == "private"
            else str(purpose or target.get("map_estimate") or "Klient")
        ).strip()
        if match is None:
            match = {
                "id": uuid4().hex,
                "label": label,
                "map_name": target.get("map_estimate"),
                "anchors": [
                    {
                        "latitude": latitude,
                        "longitude": longitude,
                        "address": address,
                        "updated_at": datetime.now(UTC).isoformat(),
                    }
                ],
            }
            places.append(match)
        else:
            match["label"] = label
        _apply_place_classification(match, classification, radius)
        target["matched_place_id"] = match["id"]
        target["manual_place_correction"] = True
        target["classification_explanation"] = "Ruční oprava změnila i výchozí typ místa."
        data["version"] = _LEARNED_PLACES_VERSION
        _write_json_atomic(self.places_path, data)
        raw["version"] = _RAW_DATA_VERSION
        _write_json_atomic(self.raw_path, raw)
        return {"place_updated": True, "place_id": match["id"]}

    async def async_find_place(
        self,
        latitude: float | None,
        longitude: float | None,
        address: str | None,
        radius_meters: float,
        private_radius: float = LEARNED_PRIVATE_RADIUS,
        transient_radius: float = LEARNED_TRANSIENT_RADIUS,
    ) -> dict[str, Any] | None:
        """Find the closest coordinate match or an exact normalized address."""
        async with self._lock:
            return await self.hass.async_add_executor_job(
                self._find_place_sync,
                latitude,
                longitude,
                address,
                radius_meters,
                private_radius,
                transient_radius,
            )

    def _find_place_sync(
        self,
        latitude: float | None,
        longitude: float | None,
        address: str | None,
        radius_meters: float,
        private_radius: float = LEARNED_PRIVATE_RADIUS,
        transient_radius: float = LEARNED_TRANSIENT_RADIUS,
    ) -> dict[str, Any] | None:
        """Find a learned place in an executor."""
        places = self._load_places_sync()["places"]
        closest: tuple[float, dict[str, Any], dict[str, Any]] | None = None
        if latitude is not None and longitude is not None:
            for place in places:
                place_radius = effective_place_radius(
                    place, radius_meters, private_radius, transient_radius
                )
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
                result["match_radius_m"] = effective_place_radius(
                    closest[1], radius_meters, private_radius, transient_radius
                )
                result["match_method"] = "gps"
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
                        result["match_radius_m"] = effective_place_radius(
                            place, radius_meters, private_radius, transient_radius
                        )
                        result["match_method"] = "address"
                        return result
        return None

    async def async_learn_place(self, place: dict[str, Any]) -> None:
        """Add or replace a learned place."""
        async with self._lock:
            await self.hass.async_add_executor_job(
                self._learn_place_sync, place.copy()
            )

    def _learn_place_sync(self, place: dict[str, Any]) -> None:
        """Persist one confirmed physical point without grouping by its label."""
        if place.get("place_role") == PLACE_ROLE_RETURN:
            # Return is stored on a trip (journey_role), never as a place.
            return
        data = self._load_places_sync()
        migrate_return_places(data)
        consolidate_learned_places(data)
        data["version"] = _LEARNED_PLACES_VERSION
        places: list[dict[str, Any]] = data["places"]
        place_id = place.get("id")
        normalized_address = _normalize_address(place.get("address"))
        trip_type = place.get("trip_type")
        place_role = place.get("place_role")

        new_anchor = {
            "latitude": place.get("latitude"),
            "longitude": place.get("longitude"),
            "address": place.get("address"),
            "updated_at": place.get("updated_at"),
        }
        incoming_point = {"anchors": [new_anchor]}

        replacement_index: int | None = None
        for index, existing in enumerate(places):
            if _places_overlap(existing, incoming_point):
                replacement_index = index
                break

        if replacement_index is None:
            requested_id = str(place_id or "")
            if not requested_id or any(
                str(existing.get("id") or "") == requested_id for existing in places
            ):
                requested_id = uuid4().hex
            learned = {
                "id": requested_id,
                "label": place.get("label"),
                "trip_type": trip_type,
                "trip_types": place_trip_types(place),
                "place_role": place.get("place_role"),
                "radius_m": place.get("radius_m"),
                "map_name": place.get("map_name"),
                "updated_at": place.get("updated_at"),
                "transient_capable": bool(place.get("transient_capable")),
                "transient_kind": place.get("transient_kind"),
                "anchors": [new_anchor],
            }
            places.append(learned)
        else:
            learned = places[replacement_index].copy()
            existing_trip_types = place_trip_types(learned)
            previous_place_role = learned.get("place_role")
            anchors = _place_anchors(learned)[-_MAX_ANCHORS_PER_PLACE:]
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
                anchors = [new_anchor]
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
                    "transient_capable": bool(
                        learned.get("transient_capable")
                        or place.get("transient_capable")
                    ),
                    "transient_kind": place.get("transient_kind")
                    or learned.get("transient_kind"),
                    "anchors": anchors[-_MAX_ANCHORS_PER_PLACE:],
                }
            )
            combined_trip_types = [
                selected
                for selected in (TRIP_TYPE_BUSINESS, TRIP_TYPE_PRIVATE)
                if selected in {*existing_trip_types, *place_trip_types(place)}
            ]
            if trip_type == TRIP_TYPE_CONTEXTUAL and place_role in {
                PLACE_ROLE_RETURN,
                PLACE_ROLE_TRANSIENT,
            }:
                combined_trip_types = []
            learned["trip_types"] = combined_trip_types
            if len(combined_trip_types) > 1:
                learned["trip_type"] = TRIP_TYPE_CONTEXTUAL
                learned["place_role"] = PLACE_ROLE_MIXED
                learned["radius_m"] = LEARNED_PRIVATE_RADIUS
            for legacy_key in ("latitude", "longitude", "address"):
                learned.pop(legacy_key, None)
            places[replacement_index] = learned
        consolidate_learned_places(data)
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
        "review_count_total": sum(
            1
            for segment in valid_segments
            if segment.get("needs_review")
            or segment.get("trip_type") == TRIP_TYPE_UNCLASSIFIED
        ),
        "today_review_count": sum(
            1
            for segment in today_segments
            if segment.get("needs_review")
            or segment.get("trip_type") == TRIP_TYPE_UNCLASSIFIED
        ),
        "today_rows": deepcopy_json(today_segments),
        "today_odometer_check": odometer_day_check(today_segments),
        "last_segment": deepcopy_json(last_segment),
    }


def calculate_history(
    segments: list[dict[str, Any]], month: str, selected_date: str
) -> dict[str, Any]:
    """Aggregate one calendar month and return rows for the selected day."""
    valid_segments = [segment for segment in segments if isinstance(segment, dict)]
    month_segments = [
        segment
        for segment in valid_segments
        if str(segment.get("date") or "").startswith(f"{month}-")
    ]

    def _distance(segment: dict[str, Any]) -> float:
        value = _optional_float(segment.get("distance_km"))
        return max(0.0, value) if value is not None else 0.0

    days: dict[str, dict[str, Any]] = {}
    for segment in month_segments:
        local_date = str(segment.get("date") or "")
        day = days.setdefault(
            local_date,
            {
                "date": local_date,
                "business_km": 0.0,
                "private_km": 0.0,
                "business_trips": 0,
                "private_trips": 0,
                "review_trips": 0,
                "trips": 0,
            },
        )
        trip_type = segment.get("trip_type")
        day["trips"] += 1
        if trip_type == TRIP_TYPE_BUSINESS:
            day["business_km"] += _distance(segment)
            day["business_trips"] += 1
        elif trip_type == TRIP_TYPE_PRIVATE:
            day["private_km"] += _distance(segment)
            day["private_trips"] += 1
        if segment.get("needs_review") or trip_type == TRIP_TYPE_UNCLASSIFIED:
            day["review_trips"] += 1

    calendar_days: list[dict[str, Any]] = []
    for local_date in sorted(days):
        day = days[local_date]
        calendar_days.append(
            {
                **day,
                "business_km": _whole_km(day["business_km"]),
                "private_km": _whole_km(day["private_km"]),
            }
        )

    selected_rows = sorted(
        (
            deepcopy_json(segment)
            for segment in month_segments
            if str(segment.get("date") or "") == selected_date
        ),
        key=lambda segment: str(segment.get("started_at") or ""),
    )
    return {
        "month": month,
        "selected_date": selected_date,
        "days": calendar_days,
        "month_business_km": _whole_km(
            sum(
                _distance(segment)
                for segment in month_segments
                if segment.get("trip_type") == TRIP_TYPE_BUSINESS
            )
        ),
        "month_private_km": _whole_km(
            sum(
                _distance(segment)
                for segment in month_segments
                if segment.get("trip_type") == TRIP_TYPE_PRIVATE
            )
        ),
        "month_review_trips": sum(
            1
            for segment in month_segments
            if segment.get("needs_review")
            or segment.get("trip_type") == TRIP_TYPE_UNCLASSIFIED
        ),
        "month_trips": len(month_segments),
        "rows": selected_rows,
    }


def deepcopy_json(value: Any) -> Any:
    """Copy JSON-compatible data without sharing nested mutable objects."""
    if value is None:
        return None
    return json.loads(json.dumps(value, ensure_ascii=False))
