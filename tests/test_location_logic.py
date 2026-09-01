"""Tests for deterministic selection of phone location sources."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "kniha_jizd_location_logic",
    ROOT / "custom_components/kniha_jizd/location_logic.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class LocationLogicTest(unittest.TestCase):
    """Prefer a stable GPS feed unless it has become stale."""

    def test_recent_gps_wins_over_nearly_simultaneous_address_coordinates(self) -> None:
        now = datetime(2026, 9, 1, 8, tzinfo=UTC)
        selected = MODULE.select_coordinate_candidate(
            [
                {
                    "source": "gps_entity",
                    "latitude": 50,
                    "longitude": 14,
                    "updated_at": now,
                },
                {
                    "source": "address_entity",
                    "latitude": 51,
                    "longitude": 15,
                    "updated_at": now + timedelta(seconds=15),
                },
            ]
        )

        self.assertEqual(selected["source"], "gps_entity")

    def test_materially_newer_fallback_replaces_stale_gps(self) -> None:
        now = datetime(2026, 9, 1, 8, tzinfo=UTC)
        selected = MODULE.select_coordinate_candidate(
            [
                {
                    "source": "gps_entity",
                    "latitude": 50,
                    "longitude": 14,
                    "updated_at": now - timedelta(minutes=20),
                },
                {
                    "source": "address_entity",
                    "latitude": 51,
                    "longitude": 15,
                    "updated_at": now,
                },
            ]
        )

        self.assertEqual(selected["source"], "address_entity")

    def test_settled_location_must_be_fresh_for_trip_end(self) -> None:
        ended = datetime(2026, 9, 1, 8, tzinfo=UTC)

        self.assertTrue(
            MODULE.location_is_fresh(ended - timedelta(seconds=4), ended)
        )
        self.assertFalse(
            MODULE.location_is_fresh(ended - timedelta(minutes=2), ended)
        )


if __name__ == "__main__":
    unittest.main()
