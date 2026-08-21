"""Tolerant parsing of Home Assistant location and odometer states."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any

_ODOMETER_ATTRIBUTES = (
    "odometer",
    "odometer_km",
    "mileage",
    "mileage_km",
    "total_distance",
    "total_distance_km",
    "kilometers",
    "kilometres",
    "km",
    "value_km",
    "value",
)
_NUMBER_PATTERN = re.compile(r"[-+]?\d(?:[\d\s\u00a0\u202f.,]*\d)?")


def _normalized_attributes(attributes: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalize attribute names from Android, iOS and integrations."""
    if not attributes:
        return {}
    return {
        str(key).strip().casefold().replace(" ", "_"): value
        for key, value in attributes.items()
    }


def _coordinate(value: Any) -> float | None:
    """Parse one coordinate without interpreting punctuation as thousands."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(str(value).strip().replace("\u00a0", "").replace(",", "."))
    except (TypeError, ValueError):
        return None


def _valid_coordinates(latitude: Any, longitude: Any) -> tuple[float, float] | None:
    """Return a valid WGS84 coordinate pair."""
    parsed_latitude = _coordinate(latitude)
    parsed_longitude = _coordinate(longitude)
    if (
        parsed_latitude is None
        or parsed_longitude is None
        or not -90 <= parsed_latitude <= 90
        or not -180 <= parsed_longitude <= 180
    ):
        return None
    return parsed_latitude, parsed_longitude


def _composite_coordinates(value: Any) -> tuple[float, float] | None:
    """Parse Location/gps attributes represented as a list, mapping or text."""
    if isinstance(value, Mapping):
        normalized = _normalized_attributes(value)
        return _valid_coordinates(
            normalized.get("latitude", normalized.get("lat")),
            normalized.get("longitude", normalized.get("lon", normalized.get("lng"))),
        )
    if (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes, bytearray))
        and len(value) >= 2
    ):
        return _valid_coordinates(value[0], value[1])
    if isinstance(value, str):
        numbers = re.findall(r"[-+]?\d+(?:[.,]\d+)?", value)
        if len(numbers) >= 2:
            return _valid_coordinates(numbers[0], numbers[1])
    return None


def coordinates_from_state(
    state_value: Any, attributes: Mapping[str, Any] | None
) -> tuple[float, float, str] | None:
    """Extract coordinates from all common HA and Companion representations."""
    normalized = _normalized_attributes(attributes)
    direct = _valid_coordinates(
        normalized.get("latitude", normalized.get("lat")),
        normalized.get("longitude", normalized.get("lon", normalized.get("lng"))),
    )
    if direct is not None:
        return direct[0], direct[1], "latitude_longitude"
    for key in ("location", "gps", "coordinates", "coordinate"):
        composite = _composite_coordinates(normalized.get(key))
        if composite is not None:
            return composite[0], composite[1], key
    composite_state = _composite_coordinates(state_value)
    if composite_state is not None:
        return composite_state[0], composite_state[1], "state"
    return None


def parse_measurement(value: Any) -> float | None:
    """Parse a localized numeric measurement, optionally including its unit."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = _NUMBER_PATTERN.search(str(value))
    if match is None:
        return None
    token = re.sub(r"[\s\u00a0\u202f]", "", match.group(0))
    dot = token.rfind(".")
    comma = token.rfind(",")
    if dot >= 0 and comma >= 0:
        decimal_separator = "." if dot > comma else ","
        thousands_separator = "," if decimal_separator == "." else "."
        token = token.replace(thousands_separator, "")
        token = token.replace(decimal_separator, ".")
    elif "." in token or "," in token:
        separator = "." if "." in token else ","
        parts = token.split(separator)
        is_grouped_thousands = (
            len(parts) > 2 and all(len(part) == 3 for part in parts[1:])
        ) or (
            len(parts) == 2
            and 1 <= len(parts[0].lstrip("+-")) <= 3
            and len(parts[1]) == 3
        )
        token = "".join(parts) if is_grouped_thousands else token.replace(separator, ".")
    try:
        return float(token)
    except ValueError:
        return None


def odometer_from_state(
    state_value: Any, attributes: Mapping[str, Any] | None
) -> tuple[float, str] | None:
    """Read odometer from the state first, then known integration attributes."""
    state_measurement = parse_measurement(state_value)
    if state_measurement is not None:
        return state_measurement, "state"
    normalized = _normalized_attributes(attributes)
    for key in _ODOMETER_ATTRIBUTES:
        measurement = parse_measurement(normalized.get(key))
        if measurement is not None:
            return measurement, f"attribute:{key}"
    return None
