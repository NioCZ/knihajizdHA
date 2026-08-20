"""Nearby institution discovery and relevance scoring."""

from __future__ import annotations

import asyncio
import logging
from math import asin, cos, radians, sin, sqrt
import re
import time
from typing import Any
import unicodedata

from aiohttp import ClientError, ClientSession

_LOGGER = logging.getLogger(__name__)

_AMENITY_WEIGHTS = {
    "research_institute": 45.0,
    "laboratory": 40.0,
    "university": 30.0,
    "hospital": 25.0,
    "clinic": 20.0,
    "college": 15.0,
}


class NearbyInstitutionSearcher:
    """Find relevant OSM institutions around a parking location."""

    def __init__(
        self,
        session: ClientSession,
        endpoint: str,
        user_agent: str,
        keywords: str,
    ) -> None:
        """Initialize a rate-limited Overpass client."""
        self._session = session
        self._endpoint = endpoint
        self._user_agent = user_agent
        self._keywords = parse_keywords(keywords)
        self._request_lock = asyncio.Lock()
        self._last_request_started = 0.0

    async def async_search(
        self,
        latitude: float | None,
        longitude: float | None,
        radius_meters: float,
    ) -> list[dict[str, Any]]:
        """Return up to five scored institution candidates."""
        if latitude is None or longitude is None:
            return []

        radius = max(100, min(10_000, int(round(radius_meters))))
        query = build_overpass_query(latitude, longitude, radius)
        try:
            async with self._request_lock:
                delay = 2.0 - (time.monotonic() - self._last_request_started)
                if delay > 0:
                    await asyncio.sleep(delay)
                self._last_request_started = time.monotonic()
                async with asyncio.timeout(45):
                    async with self._session.post(
                        self._endpoint,
                        data={"data": query},
                        headers={
                            "User-Agent": self._user_agent,
                            "Accept": "application/json",
                        },
                    ) as response:
                        response.raise_for_status()
                        payload = await response.json(content_type=None)
        except (TimeoutError, ClientError, ValueError) as err:
            _LOGGER.warning("Nearby institution search failed: %s", err)
            return []

        return rank_overpass_candidates(
            payload,
            latitude,
            longitude,
            self._keywords,
            limit=5,
        )


def build_overpass_query(latitude: float, longitude: float, radius: int) -> str:
    """Build one bounded query covering relevant medical/research OSM tags."""
    around = f"(around:{radius},{latitude:.7f},{longitude:.7f})"
    institution_amenities = (
        "hospital|clinic|university|college|research_institute|laboratory"
    )
    return (
        "[out:json][timeout:25];("
        f'nwr{around}["amenity"~"^({institution_amenities})$"];'
        f'nwr{around}["healthcare"];'
        f'nwr{around}["healthcare:speciality"];'
        f'nwr{around}["office"="research"];'
        f'nwr{around}["university"~"^(institute|department|faculty)$"];'
        f'nwr{around}["research"];'
        f'nwr{around}["laboratory"];'
        ");out bb;"
    )


def parse_keywords(value: str) -> tuple[str, ...]:
    """Normalize comma, semicolon or newline separated keyword roots."""
    normalized: list[str] = []
    for item in re.split(r"[,;\n]+", value):
        keyword = _normalize_text(item)
        if keyword and keyword not in normalized:
            normalized.append(keyword)
    return tuple(normalized)


def rank_overpass_candidates(
    payload: Any,
    latitude: float,
    longitude: float,
    keywords: tuple[str, ...],
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Normalize, score and deduplicate an Overpass JSON response."""
    if not isinstance(payload, dict) or not isinstance(payload.get("elements"), list):
        return []

    by_name: dict[str, dict[str, Any]] = {}
    for element in payload["elements"]:
        candidate = _candidate_from_element(element, latitude, longitude, keywords)
        if candidate is None:
            continue
        normalized_name = _normalize_text(candidate["name"])
        existing = by_name.get(normalized_name)
        if existing is None or (
            candidate["score"], -candidate["distance_m"]
        ) > (existing["score"], -existing["distance_m"]):
            by_name[normalized_name] = candidate

    return sorted(
        by_name.values(),
        key=lambda item: (-item["score"], item["distance_m"], item["name"]),
    )[:limit]


def _candidate_from_element(
    element: Any,
    latitude: float,
    longitude: float,
    keywords: tuple[str, ...],
) -> dict[str, Any] | None:
    """Convert one OSM element to a compact scored candidate."""
    if not isinstance(element, dict) or not isinstance(element.get("tags"), dict):
        return None
    tags: dict[str, Any] = element["tags"]
    name = _first_text(
        tags,
        "name:cs",
        "name",
        "official_name",
        "short_name",
        "operator",
        "brand",
    )
    if name is None:
        return None

    center = element.get("center")
    bounds = element.get("bounds")
    candidate_latitude = element.get("lat")
    candidate_longitude = element.get("lon")
    contains_parking_point = False
    if isinstance(bounds, dict):
        try:
            minimum_latitude = float(bounds["minlat"])
            maximum_latitude = float(bounds["maxlat"])
            minimum_longitude = float(bounds["minlon"])
            maximum_longitude = float(bounds["maxlon"])
            candidate_latitude = (minimum_latitude + maximum_latitude) / 2
            candidate_longitude = (minimum_longitude + maximum_longitude) / 2
            contains_parking_point = (
                minimum_latitude <= latitude <= maximum_latitude
                and minimum_longitude <= longitude <= maximum_longitude
            )
        except (KeyError, TypeError, ValueError):
            pass
    if isinstance(center, dict):
        candidate_latitude = center.get("lat", candidate_latitude)
        candidate_longitude = center.get("lon", candidate_longitude)
    try:
        candidate_latitude = float(candidate_latitude)
        candidate_longitude = float(candidate_longitude)
    except (TypeError, ValueError):
        return None

    distance = _haversine_meters(
        latitude, longitude, candidate_latitude, candidate_longitude
    )
    score, reasons, keyword_matches = _score_tags(
        tags, distance, keywords, contains_parking_point
    )
    return {
        "name": name,
        "distance_m": round(distance, 1),
        "score": round(score, 1),
        "category": _category_label(tags),
        "keyword_matches": keyword_matches,
        "score_reasons": reasons,
        "osm_type": element.get("type"),
        "osm_id": element.get("id"),
        "latitude": candidate_latitude,
        "longitude": candidate_longitude,
        "contains_parking_point": contains_parking_point,
    }


def _score_tags(
    tags: dict[str, Any],
    distance_meters: float,
    keywords: tuple[str, ...],
    contains_parking_point: bool,
) -> tuple[float, list[str], list[str]]:
    """Score domain fit first and distance second."""
    score = 0.0
    reasons: list[str] = []
    amenity = str(tags.get("amenity") or "").casefold()
    if weight := _AMENITY_WEIGHTS.get(amenity):
        score += weight
        reasons.append(f"amenity={amenity} +{weight:g}")

    healthcare = str(tags.get("healthcare") or "").casefold()
    if healthcare:
        healthcare_weight = 40.0 if "labor" in healthcare else 18.0
        score += healthcare_weight
        reasons.append(f"healthcare={healthcare} +{healthcare_weight:g}")

    if str(tags.get("office") or "").casefold() == "research":
        score += 40.0
        reasons.append("office=research +40")
    if str(tags.get("university") or "").casefold() in {
        "institute",
        "department",
        "faculty",
    }:
        score += 35.0
        reasons.append("univerzitní pracoviště +35")
    if tags.get("research"):
        score += 25.0
        reasons.append("research=* +25")
    if tags.get("laboratory"):
        score += 35.0
        reasons.append("laboratory=* +35")
    if contains_parking_point:
        score += 30.0
        reasons.append("parkování uvnitř areálu +30")

    searchable = _normalize_text(
        " ".join(
            str(tags.get(key) or "")
            for key in (
                "name:cs",
                "name",
                "official_name",
                "short_name",
                "operator",
                "brand",
                "description",
                "research",
                "subject",
                "healthcare:speciality",
            )
        )
    )
    keyword_matches = [
        keyword for keyword in keywords if _keyword_matches(keyword, searchable)
    ]
    keyword_bonus = min(72.0, len(keyword_matches) * 18.0)
    if keyword_bonus:
        score += keyword_bonus
        reasons.append(f"odborná klíčová slova +{keyword_bonus:g}")

    distance_penalty = min(30.0, distance_meters / 100.0)
    score -= distance_penalty
    reasons.append(f"vzdálenost -{distance_penalty:.1f}")
    return score, reasons, keyword_matches


def _category_label(tags: dict[str, Any]) -> str:
    """Return a readable primary OSM classification."""
    for key in ("amenity", "healthcare", "office", "university", "research"):
        if value := tags.get(key):
            return f"{key}={value}"
    return "instituce"


def _first_text(tags: dict[str, Any], *keys: str) -> str | None:
    """Return the first nonempty string tag."""
    for key in keys:
        value = tags.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _normalize_text(value: Any) -> str:
    """Casefold, strip accents and collapse whitespace for matching."""
    text = unicodedata.normalize("NFKD", str(value or "").casefold())
    without_accents = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", without_accents).strip()


def _keyword_matches(keyword: str, searchable: str) -> bool:
    """Match short abbreviations as words and longer terms as useful roots."""
    if len(keyword) <= 3:
        return re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", searchable) is not None
    return keyword in searchable


def _haversine_meters(
    latitude_a: float, longitude_a: float, latitude_b: float, longitude_b: float
) -> float:
    """Calculate great-circle distance between coordinates."""
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
