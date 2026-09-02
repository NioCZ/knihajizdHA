"""Pure helpers for recognizing a likely return leg."""

from __future__ import annotations

from datetime import UTC, datetime
from math import asin, cos, isfinite, radians, sin, sqrt
import re
from typing import Any


def infer_return_context(
    segment: dict[str, Any],
    previous: dict[str, Any] | None,
    max_gap_hours: float,
    max_start_distance_m: float,
) -> dict[str, Any] | None:
    """Recognize a leg starting where the previous business leg ended."""
    context = infer_trip_context(
        segment, previous, max_gap_hours, max_start_distance_m
    )
    if context is None or context.get("previous_trip_type") != "business":
        return None
    return context


def infer_trip_context(
    segment: dict[str, Any],
    previous: dict[str, Any] | None,
    max_gap_hours: float,
    max_start_distance_m: float,
) -> dict[str, Any] | None:
    """Recognize a continuous leg and carry its business/private classification."""
    if (
        not isinstance(previous, dict)
        or previous.get("trip_type") not in {"business", "private"}
        or previous.get("journey_role") == "return"
    ):
        return None

    previous_end = _parse_datetime(previous.get("ended_at"))
    current_start = _parse_datetime(segment.get("started_at"))
    if previous_end is None or current_start is None:
        return None
    gap_seconds = (current_start - previous_end).total_seconds()
    if gap_seconds < 0 or gap_seconds > max_gap_hours * 3600:
        return None

    start_distance_m: float | None = None
    match_method: str | None = None
    coordinates = (
        _number(segment.get("start_latitude")),
        _number(segment.get("start_longitude")),
        _number(previous.get("end_latitude")),
        _number(previous.get("end_longitude")),
    )
    if _valid_coordinates(coordinates):
        start_distance_m = _haversine_meters(*coordinates)  # type: ignore[arg-type]
        previous_accuracy = _accuracy(previous.get("end_accuracy_m"))
        current_accuracy = _accuracy(segment.get("start_accuracy_m"))
        accuracy_reliable = all(
            value is None or value <= max_start_distance_m
            for value in (previous_accuracy, current_accuracy)
        )
        if start_distance_m <= max_start_distance_m and accuracy_reliable:
            match_method = "gps"
        elif not accuracy_reliable and _addresses_match(segment, previous):
            match_method = "address_accuracy_fallback"
        else:
            return None
    elif _addresses_match(segment, previous):
        match_method = "address"
    else:
        return None

    previous_odometer = _number(previous.get("end_odometer_km"))
    current_odometer = _number(segment.get("start_odometer_km"))
    odometer_continuity: bool | None = None
    if previous_odometer is not None and current_odometer is not None:
        odometer_continuity = abs(previous_odometer - current_odometer) <= 1.0
        if not odometer_continuity:
            return None
    return {
        "suggested": True,
        "previous_segment_id": previous.get("id"),
        "previous_purpose": previous.get("purpose"),
        "previous_trip_type": previous.get("trip_type"),
        "previous_ended_at": previous.get("ended_at"),
        "gap_minutes": round(gap_seconds / 60, 1),
        "start_match_method": match_method,
        "start_distance_m": (
            round(start_distance_m, 1) if start_distance_m is not None else None
        ),
        "odometer_continuity": odometer_continuity,
    }


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


def _normalize_address(value: Any) -> str:
    """Normalize an address for fallback continuity matching."""
    return re.sub(r"\s+", " ", str(value or "").strip().casefold())


def _addresses_match(segment: dict[str, Any], previous: dict[str, Any]) -> bool:
    """Require one nonempty exact normalized parking address."""
    current = _normalize_address(segment.get("start_address"))
    return bool(current and current == _normalize_address(previous.get("end_address")))


def _number(value: Any) -> float | None:
    """Convert an optional JSON number."""
    try:
        parsed = float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    return parsed if parsed is not None and isfinite(parsed) else None


def _accuracy(value: Any) -> float | None:
    """Return a meaningful nonnegative GPS accuracy."""
    parsed = _number(value)
    return parsed if parsed is not None and parsed >= 0 else None


def _valid_coordinates(values: tuple[float | None, ...]) -> bool:
    """Require two valid WGS84 pairs before computing a continuity distance."""
    return bool(
        all(value is not None for value in values)
        and -90 <= values[0] <= 90  # type: ignore[operator]
        and -180 <= values[1] <= 180  # type: ignore[operator]
        and -90 <= values[2] <= 90  # type: ignore[operator]
        and -180 <= values[3] <= 180  # type: ignore[operator]
    )


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
