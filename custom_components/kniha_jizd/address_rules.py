"""Pure helpers for configured home and company address matching."""

from __future__ import annotations

from collections.abc import Iterable
import re
import unicodedata
from math import asin, cos, radians, sin, sqrt
from typing import Any

_CZECH_COUNTRIES = {"cesko", "ceska republika", "czechia", "czech republic"}


def address_matches_reference(observed: Any, reference: Any) -> bool:
    """Match a short configured address inside a full geocoded address safely."""
    observed_tokens = set(_address_tokens(observed))
    reference_tokens = _address_tokens(reference)
    return bool(reference_tokens) and all(
        token in observed_tokens for token in reference_tokens
    )


def shorten_address(value: Any) -> str | None:
    """Shorten a domestic address while preserving foreign addresses verbatim."""
    if not isinstance(value, str) or not value.strip():
        return None
    original = value.strip()
    parts = [part.strip() for part in original.split(",") if part.strip()]
    if not parts:
        return original
    country_present = _ascii_text(parts[-1]) in _CZECH_COUNTRIES
    domestic = country_present or any(
        re.search(r"\b\d{3}\s?\d{2}\b", part) for part in parts
    )
    if not domestic:
        return original
    if country_present:
        parts.pop()
    shortened: list[str] = []
    for part in parts:
        without_postcode = re.sub(r"\b\d{3}\s?\d{2}\b", "", part).strip(" -")
        normalized = _ascii_text(without_postcode)
        if not without_postcode or normalized.startswith(("okres ", "kraj ")):
            continue
        if normalized.endswith(" kraj"):
            continue
        if normalized not in {_ascii_text(existing) for existing in shortened}:
            shortened.append(without_postcode)
    return ", ".join(shortened[:3]) or original


def coordinate_distance_m(
    latitude: Any,
    longitude: Any,
    reference_latitude: Any,
    reference_longitude: Any,
) -> float | None:
    """Return GPS distance in metres, or None when coordinates are unavailable."""
    try:
        latitude_value = float(latitude)
        longitude_value = float(longitude)
        reference_latitude_value = float(reference_latitude)
        reference_longitude_value = float(reference_longitude)
    except (TypeError, ValueError):
        return None
    earth_radius_m = 6_371_000.0
    delta_latitude = radians(reference_latitude_value - latitude_value)
    delta_longitude = radians(reference_longitude_value - longitude_value)
    a = (
        sin(delta_latitude / 2) ** 2
        + cos(radians(latitude_value))
        * cos(radians(reference_latitude_value))
        * sin(delta_longitude / 2) ** 2
    )
    return 2 * earth_radius_m * asin(sqrt(a))


def configured_place_match(
    latitude: Any,
    longitude: Any,
    addresses: Iterable[Any],
    reference_address: Any,
    reference_latitude: Any,
    reference_longitude: Any,
    radius_m: float,
) -> dict[str, Any] | None:
    """Prefer GPS within a radius and use an address only when GPS is absent."""
    distance_m = coordinate_distance_m(
        latitude,
        longitude,
        reference_latitude,
        reference_longitude,
    )
    if distance_m is not None:
        if distance_m <= radius_m:
            return {"method": "gps", "distance_m": round(distance_m, 1)}
        return None
    if reference_address and any(
        address_matches_reference(address, reference_address)
        for address in addresses
    ):
        return {"method": "address", "distance_m": None}
    return None


def _address_tokens(value: Any) -> list[str]:
    """Normalize accents and punctuation while preserving house numbers."""
    return re.findall(r"[a-z0-9]+", _ascii_text(value))


def _ascii_text(value: Any) -> str:
    """Normalize accents and case for comparisons."""
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(
        character for character in text if not unicodedata.combining(character)
    )
