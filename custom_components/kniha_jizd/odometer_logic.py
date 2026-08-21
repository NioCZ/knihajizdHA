"""Pure decision helpers for accepting a cloud odometer update."""

from __future__ import annotations

from datetime import datetime


def odometer_update_signal(
    disconnected_at: datetime,
    start_odometer_km: float | None,
    updated_at: datetime | None,
    odometer_km: float | None,
) -> str | None:
    """Describe a usable post-disconnect odometer update, if available."""
    if (
        updated_at is None
        or updated_at <= disconnected_at
        or odometer_km is None
    ):
        return None
    if start_odometer_km is None:
        return "post_disconnect_update"
    if odometer_km > start_odometer_km + 0.001:
        return "post_disconnect_update_and_increase"
    return None
