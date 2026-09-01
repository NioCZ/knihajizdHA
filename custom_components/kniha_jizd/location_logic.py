"""Pure selection rules for competing Home Assistant location sources."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Any


PREFERRED_SOURCE_FRESHNESS_SECONDS = 30.0


def select_coordinate_candidate(
    candidates: list[dict[str, Any]],
    preferred_source: str = "gps_entity",
    freshness_window_seconds: float = PREFERRED_SOURCE_FRESHNESS_SECONDS,
) -> dict[str, Any] | None:
    """Prefer GPS unless another valid source is materially newer."""
    valid = [candidate for candidate in candidates if _valid_candidate(candidate)]
    if not valid:
        return None

    newest = max(valid, key=_updated_sort_key)
    preferred = next(
        (item for item in valid if item.get("source") == preferred_source), None
    )
    if preferred is None:
        return newest

    newest_at = _aware_datetime(newest.get("updated_at"))
    preferred_at = _aware_datetime(preferred.get("updated_at"))
    if newest_at is None or preferred_at is None:
        return preferred
    if preferred_at >= newest_at - timedelta(seconds=freshness_window_seconds):
        return preferred
    return newest


def location_is_fresh(
    coordinate_updated_at: datetime | None,
    event_at: datetime,
    tolerance_seconds: float = 5.0,
) -> bool:
    """Return whether a coordinate was updated at or just before an event."""
    updated = _aware_datetime(coordinate_updated_at)
    event = _aware_datetime(event_at)
    if updated is None or event is None:
        return False
    return updated >= event - timedelta(seconds=max(0.0, tolerance_seconds))


def _valid_candidate(candidate: dict[str, Any]) -> bool:
    try:
        latitude = float(candidate.get("latitude"))
        longitude = float(candidate.get("longitude"))
    except (TypeError, ValueError):
        return False
    return bool(
        isfinite(latitude)
        and isfinite(longitude)
        and -90 <= latitude <= 90
        and -180 <= longitude <= 180
    )


def _updated_sort_key(candidate: dict[str, Any]) -> datetime:
    return _aware_datetime(candidate.get("updated_at")) or datetime.min.replace(
        tzinfo=UTC
    )


def _aware_datetime(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
