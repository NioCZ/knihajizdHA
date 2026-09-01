"""Pure helpers for grouping short stops into one classified journey."""

from __future__ import annotations

from datetime import UTC, datetime
from math import asin, cos, radians, sin, sqrt
import re
import unicodedata
from typing import Any

_TRANSIENT_AMENITIES = {
    "charging_station",
    "fast_food",
    "food_court",
    "fuel",
    "restaurant",
    "cafe",
    "car_wash",
    "toilets",
}
_TRANSIENT_HIGHWAYS = {"rest_area", "services"}
_TRANSIENT_TOURISM = {"picnic_site"}
_WEAK_PARKING_TYPES = {"parking", "parking_entrance"}
_TRANSIENT_NAME_WORDS = (
    "benzina",
    "cerpaci stanice",
    "odpocivadlo",
    "petrol station",
    "rest area",
    "service area",
)


def detect_transient_stop(
    map_result: dict[str, Any] | None,
    institution_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Return normalized evidence when the precise POI is a journey stop."""
    if not isinstance(map_result, dict):
        return None

    category = _normalize(map_result.get("category"))
    poi_type = _normalize(map_result.get("type"))
    name = str(map_result.get("name") or "").strip()
    searchable = _normalize(
        " ".join(
            str(map_result.get(key) or "")
            for key in ("name", "display_name", "category", "type")
        )
    )

    stop_kind: str | None = None
    if category == "shop":
        stop_kind = "shop"
    elif category == "highway" and poi_type in _TRANSIENT_HIGHWAYS:
        stop_kind = "rest"
    elif category == "tourism" and poi_type in _TRANSIENT_TOURISM:
        stop_kind = "rest"
    elif category == "amenity" and poi_type in _TRANSIENT_AMENITIES:
        if poi_type in {"fuel", "charging_station", "car_wash"}:
            stop_kind = "fuel"
        elif poi_type in _WEAK_PARKING_TYPES:
            stop_kind = "parking"
        elif poi_type in {"restaurant", "cafe", "fast_food", "food_court"}:
            stop_kind = "food"
        else:
            stop_kind = "service"
    elif (
        category == "amenity"
        and poi_type in _WEAK_PARKING_TYPES
        and any(word in searchable for word in _TRANSIENT_NAME_WORDS)
    ):
        stop_kind = "parking"
    elif any(word in searchable for word in _TRANSIENT_NAME_WORDS):
        stop_kind = "service"

    if stop_kind is None:
        return None
    if stop_kind == "parking" and _nearby_institution_is_anchor(
        institution_candidates
    ):
        return None

    return {
        "detected": True,
        "kind": stop_kind,
        "name": name or str(map_result.get("display_name") or "Mezizastávka"),
        "category": category or None,
        "type": poi_type or None,
        "detection_source": "nominatim",
    }


def continuation_details(
    previous: dict[str, Any],
    current: dict[str, Any],
    max_gap_minutes: float,
    max_distance_meters: float,
) -> dict[str, Any] | None:
    """Verify that a segment really continues after one short parked stop."""
    details = parking_boundary_details(
        previous, current, max_gap_minutes, max_distance_meters
    )
    if details is None:
        return None

    previous_odometer = _number(previous.get("end_odometer_km"))
    current_odometer = _number(current.get("start_odometer_km"))
    odometer_continuity: bool | None = None
    if previous_odometer is not None and current_odometer is not None:
        odometer_continuity = abs(previous_odometer - current_odometer) <= 1.0
        if not odometer_continuity:
            return None

    return {**details, "odometer_continuity": odometer_continuity}


def parking_boundary_details(
    previous: dict[str, Any],
    current: dict[str, Any],
    max_gap_minutes: float,
    max_distance_meters: float,
) -> dict[str, Any] | None:
    """Verify time and place continuity without assuming an odometer boundary."""
    ended_at = _parse_datetime(previous.get("ended_at"))
    started_at = _parse_datetime(current.get("started_at"))
    if ended_at is None or started_at is None:
        return None
    gap_seconds = (started_at - ended_at).total_seconds()
    if gap_seconds < 0 or gap_seconds > max_gap_minutes * 60:
        return None

    distance: float | None = None
    match_method: str | None = None
    coordinates = (
        _number(previous.get("end_latitude")),
        _number(previous.get("end_longitude")),
        _number(current.get("start_latitude")),
        _number(current.get("start_longitude")),
    )
    if all(value is not None for value in coordinates):
        distance = _haversine_meters(*coordinates)  # type: ignore[arg-type]
        if distance > max_distance_meters:
            return None
        match_method = "gps"
    else:
        previous_address = _normalize(previous.get("end_address"))
        current_address = _normalize(current.get("start_address"))
        if not previous_address or previous_address != current_address:
            return None
        match_method = "address"

    return {
        "gap_minutes": round(gap_seconds / 60, 1),
        "match_method": match_method,
        "distance_m": round(distance, 1) if distance is not None else None,
    }


def apply_journey_classification(
    transient_segments: list[dict[str, Any]],
    destination: dict[str, Any],
    purpose: str,
    trip_type: str,
    source: str,
) -> None:
    """Apply one meaningful destination to every leg of its journey chain."""
    destination_id = destination.get("id")
    all_segments = [*transient_segments, destination]
    distances = [_number(segment.get("distance_km")) for segment in all_segments]
    journey_distance = round(sum(value or 0.0 for value in distances), 3)
    journey_distance_complete = all(value is not None for value in distances)
    for segment in transient_segments:
        segment["purpose"] = purpose
        segment["trip_type"] = trip_type
        segment["classification_source"] = f"journey_inherited:{source}"
        segment["journey_role"] = "transient_stop"
        segment["journey_inherited_from_segment_id"] = destination_id

    destination["purpose"] = purpose
    destination["trip_type"] = trip_type
    destination["classification_source"] = source
    destination["journey_role"] = destination.get("journey_role") or "destination"
    for segment in all_segments:
        segment["journey_segment_count"] = len(all_segments)
        segment["journey_distance_km"] = journey_distance
        segment["journey_distance_complete"] = journey_distance_complete


def normalize_trip_purpose(value: Any, trip_type: str) -> str:
    """Keep the customer optional and remove the private sentinel from business trips."""
    if trip_type == "private":
        return "Soukromá"
    purpose = str(value or "").strip()
    if trip_type == "business" and _normalize(purpose) == "soukroma":
        return ""
    return purpose


def map_routes_without_transient_stops(
    routes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Hide short stops and collapse their journey into one visible map route."""
    visible: list[dict[str, Any]] = []
    journeys: dict[str, list[dict[str, Any]]] = {}
    for route in routes:
        if not isinstance(route, dict):
            continue
        journey_id = str(route.get("journey_id") or "").strip()
        if journey_id:
            journeys.setdefault(journey_id, []).append(route)
        elif route.get("journey_role") != "transient_stop":
            visible.append(route.copy())

    for journey_routes in journeys.values():
        ordered = sorted(
            journey_routes, key=lambda row: str(row.get("started_at") or "")
        )
        transient = [
            row for row in ordered if row.get("journey_role") == "transient_stop"
        ]
        destinations = [
            row for row in ordered if row.get("journey_role") != "transient_stop"
        ]
        if not destinations:
            continue
        if not transient:
            visible.extend(row.copy() for row in destinations)
            continue

        collapsed = destinations[-1].copy()
        first = ordered[0]
        for field in (
            "started_at",
            "start_latitude",
            "start_longitude",
            "start_address",
        ):
            if first.get(field) is not None:
                collapsed[field] = first[field]
        collapsed["collapsed_stop_count"] = len(transient)
        visible.append(collapsed)

    return sorted(visible, key=lambda row: str(row.get("started_at") or ""))


def _nearby_institution_is_anchor(
    candidates: list[dict[str, Any]] | None,
) -> bool:
    """Protect a customer-campus parking point from transient classification."""
    if not isinstance(candidates, list):
        return False
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        distance = _number(candidate.get("distance_m"))
        score = _number(candidate.get("score"))
        if candidate.get("contains_parking_point"):
            return True
        if distance is not None and distance <= 500 and (score or 0.0) >= 5:
            return True
    return False


def _parse_datetime(value: Any) -> datetime | None:
    """Parse an ISO timestamp and normalize it to UTC."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _normalize(value: Any) -> str:
    """Normalize text, accents and whitespace for comparisons."""
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    without_accents = "".join(
        character for character in text if not unicodedata.combining(character)
    )
    return re.sub(r"\s+", " ", without_accents).strip()


def _number(value: Any) -> float | None:
    """Convert an optional JSON number."""
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _haversine_meters(
    latitude_a: float, longitude_a: float, latitude_b: float, longitude_b: float
) -> float:
    """Return great-circle distance between two points."""
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
