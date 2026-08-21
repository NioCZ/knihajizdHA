"""Tests for whole-journey analysis around short transient stops."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "kniha_jizd_journey_chain",
    ROOT / "custom_components/kniha_jizd/journey_chain.py",
)
assert SPEC is not None and SPEC.loader is not None
CHAIN_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CHAIN_MODULE)


class JourneyChainTest(unittest.TestCase):
    """Verify conservative stop detection and segment continuity."""

    def test_fuel_rest_area_and_shop_are_transient_candidates(self) -> None:
        """Recognize the stop types requested by the logbook workflow."""
        examples = (
            ({"category": "amenity", "type": "fuel", "name": "ORLEN"}, "fuel"),
            ({"category": "highway", "type": "rest_area"}, "rest"),
            ({"category": "shop", "type": "supermarket", "name": "Lidl"}, "shop"),
        )

        for result, expected_kind in examples:
            with self.subTest(result=result):
                detected = CHAIN_MODULE.detect_transient_stop(result, [])
                self.assertIsNotNone(detected)
                self.assertEqual(detected["kind"], expected_kind)

    def test_hospital_parking_remains_a_customer_anchor(self) -> None:
        """Do not swallow a visit merely because the car parks on its campus."""
        map_result = {"category": "amenity", "type": "parking"}
        candidates = [
            {
                "name": "Fakultní nemocnice",
                "distance_m": 80,
                "score": 35,
                "contains_parking_point": True,
            }
        ]

        self.assertIsNone(
            CHAIN_MODULE.detect_transient_stop(map_result, candidates)
        )

    def test_short_same_place_stop_continues_journey(self) -> None:
        """Join segments only when time, place and odometer agree."""
        previous = {
            "ended_at": "2026-08-21T10:00:00+00:00",
            "end_latitude": 50.0,
            "end_longitude": 14.0,
            "end_odometer_km": 1050,
        }
        current = {
            "started_at": "2026-08-21T10:20:00+00:00",
            "start_latitude": 50.0004,
            "start_longitude": 14.0,
            "start_odometer_km": 1050,
        }

        details = CHAIN_MODULE.continuation_details(previous, current, 60, 500)

        self.assertIsNotNone(details)
        self.assertEqual(details["match_method"], "gps")
        self.assertTrue(details["odometer_continuity"])

    def test_late_distant_or_moved_car_does_not_continue(self) -> None:
        """Keep unrelated trips out of an earlier journey chain."""
        previous = {
            "ended_at": "2026-08-21T10:00:00+00:00",
            "end_latitude": 50.0,
            "end_longitude": 14.0,
            "end_odometer_km": 1050,
        }
        base = {
            "started_at": "2026-08-21T10:20:00+00:00",
            "start_latitude": 50.0004,
            "start_longitude": 14.0,
            "start_odometer_km": 1050,
        }
        late = {**base, "started_at": "2026-08-21T12:00:01+00:00"}
        distant = {**base, "start_latitude": 50.02}
        moved = {**base, "start_odometer_km": 1055}

        for current in (late, distant, moved):
            with self.subTest(current=current):
                self.assertIsNone(
                    CHAIN_MODULE.continuation_details(
                        previous, current, 60, 500
                    )
                )

    def test_destination_classifies_the_whole_journey(self) -> None:
        """Assign fuel and shop legs to the final customer's business trip."""
        stops = [
            {"id": "fuel", "distance_km": 10.5},
            {"id": "shop", "distance_km": 5.0},
        ]
        destination = {
            "id": "customer",
            "journey_role": None,
            "distance_km": 8.25,
        }

        CHAIN_MODULE.apply_journey_classification(
            stops,
            destination,
            "Genetická laboratoř",
            "business",
            "learned_place",
        )

        self.assertTrue(all(item["trip_type"] == "business" for item in stops))
        self.assertTrue(
            all(item["purpose"] == "Genetická laboratoř" for item in stops)
        )
        self.assertTrue(
            all(
                item["journey_inherited_from_segment_id"] == "customer"
                for item in stops
            )
        )
        self.assertEqual(destination["journey_role"], "destination")
        self.assertEqual(destination["journey_segment_count"], 3)
        self.assertEqual(destination["journey_distance_km"], 23.75)
        self.assertTrue(destination["journey_distance_complete"])


if __name__ == "__main__":
    unittest.main()
