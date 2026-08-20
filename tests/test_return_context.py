"""Tests for conservative service-return recognition."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "kniha_jizd_trip_context",
    ROOT / "custom_components/kniha_jizd/trip_context.py",
)
assert SPEC is not None and SPEC.loader is not None
CONTEXT_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTEXT_MODULE)


def _previous(trip_type: str = "business") -> dict:
    return {
        "id": "outbound",
        "trip_type": trip_type,
        "purpose": "Genetická laboratoř",
        "ended_at": "2026-08-20T15:00:00+00:00",
        "end_latitude": 50.0000,
        "end_longitude": 14.0000,
        "end_address": "Laboratorní 1",
        "end_odometer_km": 1050.0,
    }


def _current() -> dict:
    return {
        "started_at": "2026-08-20T16:00:00+00:00",
        "start_latitude": 50.0005,
        "start_longitude": 14.0000,
        "start_address": "Laboratorní 1",
        "start_odometer_km": 1050.0,
    }


class ReturnContextTest(unittest.TestCase):
    """Require business, place and time continuity before suggesting a return."""

    def test_detects_continuous_business_return(self) -> None:
        """Recognize a timely departure from the previous customer's location."""
        context = CONTEXT_MODULE.infer_return_context(
            _current(), _previous(), 18, 1000
        )

        self.assertIsNotNone(context)
        self.assertEqual(context["previous_segment_id"], "outbound")
        self.assertEqual(context["previous_purpose"], "Genetická laboratoř")
        self.assertEqual(context["start_match_method"], "gps")
        self.assertTrue(context["odometer_continuity"])

    def test_private_previous_leg_never_suggests_business_return(self) -> None:
        """Do not propagate a private classification into a return context."""
        context = CONTEXT_MODULE.infer_return_context(
            _current(), _previous("private"), 18, 1000
        )

        self.assertIsNone(context)

    def test_previous_return_does_not_start_another_return_chain(self) -> None:
        """Avoid treating a later departure from home or hotel as another return."""
        previous = _previous()
        previous["journey_role"] = "return"

        context = CONTEXT_MODULE.infer_return_context(
            _current(), previous, 18, 1000
        )

        self.assertIsNone(context)

    def test_rejects_distant_or_late_departure(self) -> None:
        """Avoid automatic inference when either continuity signal is missing."""
        distant = _current()
        distant["start_latitude"] = 50.05
        late = _current()
        late["started_at"] = "2026-08-22T16:00:00+00:00"

        self.assertIsNone(
            CONTEXT_MODULE.infer_return_context(distant, _previous(), 18, 1000)
        )
        self.assertIsNone(
            CONTEXT_MODULE.infer_return_context(late, _previous(), 18, 1000)
        )

    def test_rejects_unexplained_odometer_gap(self) -> None:
        """Reject a return when the car moved between the recorded segments."""
        current = _current()
        current["start_odometer_km"] = 1057.0

        context = CONTEXT_MODULE.infer_return_context(
            current, _previous(), 18, 1000
        )

        self.assertIsNone(context)

    def test_address_fallback_works_without_gps(self) -> None:
        """Use an exact normalized address only when coordinates are unavailable."""
        current = _current()
        previous = _previous()
        current["start_latitude"] = None
        current["start_longitude"] = None
        previous["end_latitude"] = None
        previous["end_longitude"] = None
        current["start_address"] = "  LABORATORNÍ   1 "

        context = CONTEXT_MODULE.infer_return_context(
            current, previous, 18, 1000
        )

        self.assertIsNotNone(context)
        self.assertEqual(context["start_match_method"], "address")


if __name__ == "__main__":
    unittest.main()
