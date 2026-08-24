"""Tests for multi-anchor learned customer locations."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest

ROOT = Path(__file__).parents[1]

homeassistant_stub = types.ModuleType("homeassistant")
homeassistant_core_stub = types.ModuleType("homeassistant.core")
homeassistant_core_stub.HomeAssistant = object
sys.modules.setdefault("homeassistant", homeassistant_stub)
sys.modules.setdefault("homeassistant.core", homeassistant_core_stub)

package = types.ModuleType("custom_components.kniha_jizd")
package.__path__ = [str(ROOT / "custom_components/kniha_jizd")]
sys.modules.setdefault("custom_components.kniha_jizd", package)

const_spec = importlib.util.spec_from_file_location(
    "custom_components.kniha_jizd.const",
    ROOT / "custom_components/kniha_jizd/const.py",
)
assert const_spec is not None and const_spec.loader is not None
const_module = importlib.util.module_from_spec(const_spec)
sys.modules[const_spec.name] = const_module
const_spec.loader.exec_module(const_module)

storage_spec = importlib.util.spec_from_file_location(
    "custom_components.kniha_jizd.storage",
    ROOT / "custom_components/kniha_jizd/storage.py",
)
assert storage_spec is not None and storage_spec.loader is not None
STORAGE_MODULE = importlib.util.module_from_spec(storage_spec)
sys.modules[storage_spec.name] = STORAGE_MODULE
storage_spec.loader.exec_module(STORAGE_MODULE)


class LearnedPlacesTest(unittest.TestCase):
    """Verify grouping and matching of multiple parking points."""

    def test_map_uses_conservative_private_and_transient_zones(self) -> None:
        """Keep broad client zones from being reused for contextual places."""
        markers = STORAGE_MODULE.places_for_map(
            {
                "places": [
                    {
                        "id": "private",
                        "label": "Kino",
                        "trip_type": "private",
                        "anchors": [{"latitude": 50.0, "longitude": 14.0}],
                    },
                    {
                        "id": "fuel",
                        "label": "ORLEN",
                        "trip_type": "contextual",
                        "place_role": "transient",
                        "anchors": [{"latitude": 50.1, "longitude": 14.1}],
                    },
                    {
                        "id": "client",
                        "label": "Nemocnice",
                        "trip_type": "business",
                        "place_role": "client",
                        "anchors": [{"latitude": 50.2, "longitude": 14.2}],
                    },
                ]
            },
            1000,
        )

        by_id = {marker["place_id"]: marker for marker in markers}
        self.assertEqual(by_id["private"]["radius_m"], 250)
        self.assertEqual(by_id["private"]["place_role"], "private")
        self.assertEqual(by_id["fuel"]["radius_m"], 200)
        self.assertEqual(by_id["client"]["radius_m"], 1000)

    def test_same_customer_keeps_two_distant_anchors(self) -> None:
        """Two confirmations with one label remain one customer with two anchors."""
        test_output = ROOT / "test-output"
        test_output.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=test_output) as temporary_directory:
            repository = STORAGE_MODULE.KnihaJizdRepository.__new__(
                STORAGE_MODULE.KnihaJizdRepository
            )
            repository.places_path = Path(temporary_directory) / "learned_places.json"

            repository._learn_place_sync(
                {
                    "id": "first",
                    "latitude": 50.0,
                    "longitude": 14.0,
                    "address": "Hlavní parkoviště",
                    "label": "Genetické centrum",
                    "trip_type": "business",
                    "map_name": "Genetické centrum",
                    "updated_at": "2026-08-20T10:00:00+00:00",
                }
            )
            repository._learn_place_sync(
                {
                    "id": "second",
                    "latitude": 50.018,
                    "longitude": 14.0,
                    "address": "Zadní vjezd",
                    "label": "Genetické centrum",
                    "trip_type": "business",
                    "map_name": "Genetické centrum",
                    "updated_at": "2026-08-21T10:00:00+00:00",
                }
            )

            document = json.loads(repository.places_path.read_text(encoding="utf-8"))
            self.assertEqual(document["version"], 3)
            self.assertEqual(len(document["places"]), 1)
            self.assertEqual(len(document["places"][0]["anchors"]), 2)

            first_match = repository._find_place_sync(50.0005, 14.0, None, 1000)
            second_match = repository._find_place_sync(50.0175, 14.0, None, 1000)
            self.assertEqual(first_match["label"], "Genetické centrum")
            self.assertEqual(second_match["label"], "Genetické centrum")
            self.assertEqual(second_match["matched_address"], "Zadní vjezd")

    def test_gps_never_matches_same_address_outside_radius(self) -> None:
        """An imprecise campus address cannot bypass the coordinate circle."""
        test_output = ROOT / "test-output"
        test_output.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=test_output) as temporary_directory:
            repository = STORAGE_MODULE.KnihaJizdRepository.__new__(
                STORAGE_MODULE.KnihaJizdRepository
            )
            repository.places_path = Path(temporary_directory) / "learned_places.json"
            repository._learn_place_sync(
                {
                    "latitude": 50.0,
                    "longitude": 14.0,
                    "address": "Univerzitní kampus",
                    "label": "Laboratoř A",
                    "trip_type": "business",
                }
            )

            match = repository._find_place_sync(
                50.02, 14.0, "Univerzitní kampus", 1000
            )
            fallback = repository._find_place_sync(
                None, None, "Univerzitní kampus", 1000
            )

            self.assertIsNone(match)
            self.assertEqual(fallback["label"], "Laboratoř A")

    def test_existing_private_place_can_become_contextual_return(self) -> None:
        """Preserve one anchor while teaching home as a context-sensitive return."""
        test_output = ROOT / "test-output"
        test_output.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=test_output) as temporary_directory:
            repository = STORAGE_MODULE.KnihaJizdRepository.__new__(
                STORAGE_MODULE.KnihaJizdRepository
            )
            repository.places_path = Path(temporary_directory) / "learned_places.json"
            repository._learn_place_sync(
                {
                    "id": "home",
                    "latitude": 50.0,
                    "longitude": 14.0,
                    "address": "Domov",
                    "label": "Domov",
                    "trip_type": "private",
                }
            )
            repository._learn_place_sync(
                {
                    "id": "home",
                    "latitude": 50.0001,
                    "longitude": 14.0,
                    "address": "Domov",
                    "label": "Domov",
                    "trip_type": "contextual",
                    "place_role": "return",
                }
            )

            match = repository._find_place_sync(50.0, 14.0, None, 1000)
            document = json.loads(repository.places_path.read_text(encoding="utf-8"))
            self.assertEqual(document["version"], 3)
            self.assertEqual(len(document["places"]), 1)
            self.assertEqual(match["place_role"], "return")
            self.assertEqual(match["trip_type"], "contextual")

    def test_transient_place_uses_its_small_stored_radius(self) -> None:
        """Keep a learned fuel stop from swallowing a nearby customer."""
        test_output = ROOT / "test-output"
        test_output.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=test_output) as temporary_directory:
            repository = STORAGE_MODULE.KnihaJizdRepository.__new__(
                STORAGE_MODULE.KnihaJizdRepository
            )
            repository.places_path = Path(temporary_directory) / "learned_places.json"
            repository._learn_place_sync(
                {
                    "id": "fuel",
                    "latitude": 50.0,
                    "longitude": 14.0,
                    "address": "Benzinka",
                    "label": "ORLEN",
                    "trip_type": "contextual",
                    "place_role": "transient",
                    "radius_m": 200,
                }
            )

            close = repository._find_place_sync(50.001, 14.0, None, 1000)
            outside = repository._find_place_sync(50.003, 14.0, None, 1000)

            self.assertEqual(close["place_role"], "transient")
            self.assertIsNone(outside)

            repository._learn_place_sync(
                {
                    "id": "fuel",
                    "latitude": 50.0,
                    "longitude": 14.0,
                    "address": "Benzinka",
                    "label": "Soukromá",
                    "trip_type": "private",
                    "place_role": "client",
                }
            )
            converted = repository._find_place_sync(50.003, 14.0, None, 1000)

            self.assertEqual(converted["trip_type"], "private")
            self.assertIsNone(converted.get("radius_m"))

    def test_raw_statistics_for_entities_and_panel(self) -> None:
        """Calculate daily totals and the last trip from persisted segments."""
        document = json.loads(
            (ROOT / "tests/fixtures/raw_sample.json").read_text(encoding="utf-8")
        )

        statistics = STORAGE_MODULE.calculate_statistics(
            document["segments"], "2026-08-19"
        )

        self.assertEqual(statistics["segments_total"], 3)
        self.assertEqual(statistics["today_segments"], 3)
        self.assertEqual(statistics["today_business_km"], 33)
        self.assertEqual(statistics["today_private_km"], 9)
        self.assertEqual(len(statistics["today_rows"]), 3)
        self.assertEqual(statistics["last_segment"]["id"], "segment-c")

    def test_manual_edit_updates_the_whole_persisted_journey(self) -> None:
        """Keep all legs consistent when one daily-table row is corrected."""
        test_output = ROOT / "test-output"
        test_output.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=test_output) as temporary_directory:
            repository = STORAGE_MODULE.KnihaJizdRepository.__new__(
                STORAGE_MODULE.KnihaJizdRepository
            )
            repository.raw_path = Path(temporary_directory) / "raw.json"
            repository.raw_path.write_text(
                json.dumps(
                    {
                        "version": 2,
                        "segments": [
                            {
                                "id": "fuel",
                                "journey_id": "journey",
                                "purpose": "Původní",
                                "trip_type": "business",
                            },
                            {
                                "id": "destination",
                                "journey_id": "journey",
                                "purpose": "Původní",
                                "trip_type": "business",
                            },
                            {
                                "id": "other",
                                "journey_id": "other-journey",
                                "purpose": "Jiné",
                                "trip_type": "business",
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            changed = repository._update_trip_sync(
                "fuel", "Soukromá", "private"
            )
            document = json.loads(
                repository.raw_path.read_text(encoding="utf-8")
            )

            self.assertEqual(changed, 2)
            self.assertTrue(
                all(
                    segment["trip_type"] == "private"
                    for segment in document["segments"][:2]
                )
            )
            self.assertEqual(
                document["segments"][0]["classification_source"],
                "manual_panel",
            )
            self.assertEqual(document["segments"][2]["purpose"], "Jiné")

    def test_manual_edit_can_override_addresses_and_distance(self) -> None:
        """Keep an explicit panel correction stable for one selected segment."""
        test_output = ROOT / "test-output"
        test_output.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=test_output) as temporary_directory:
            repository = STORAGE_MODULE.KnihaJizdRepository.__new__(
                STORAGE_MODULE.KnihaJizdRepository
            )
            repository.raw_path = Path(temporary_directory) / "raw.json"
            repository.raw_path.write_text(
                json.dumps(
                    {
                        "version": 3,
                        "segments": [
                            {
                                "id": "segment",
                                "date": "2026-08-21",
                                "purpose": "Původní",
                                "trip_type": "business",
                                "distance_km": 0.0,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            repository._update_trip_sync(
                "segment",
                "Bez zákazníka",
                "business",
                "Opravený start",
                "Opravený cíl",
                14.25,
            )
            segment = json.loads(
                repository.raw_path.read_text(encoding="utf-8")
            )["segments"][0]

            self.assertEqual(segment["start_address"], "Opravený start")
            self.assertEqual(segment["end_address"], "Opravený cíl")
            self.assertEqual(segment["distance_km"], 14)
            self.assertTrue(segment["manual_distance_override"])

    def test_combined_cloud_increment_is_reassigned_to_zero_leg(self) -> None:
        """Split one later trusted counter increase across both driven legs."""
        segments = [
            {
                "id": "first",
                "started_at": "2026-08-21T08:00:00+00:00",
                "start_odometer_km": 1000.0,
                "end_odometer_km": 1000.0,
                "distance_km": 0.0,
                "odometer_wait_timed_out": False,
                "odometer_shared_update": True,
                "odometer_completion_source": (
                    "post_disconnect_update_and_increase"
                ),
                "start_latitude": 50.0,
                "start_longitude": 14.0,
                "end_latitude": 50.09,
                "end_longitude": 14.0,
            },
            {
                "id": "second",
                "started_at": "2026-08-21T10:00:00+00:00",
                "start_odometer_km": 1000.0,
                "end_odometer_km": 1020.0,
                "distance_km": 20.0,
                "odometer_wait_timed_out": False,
                "odometer_completion_source": (
                    "post_disconnect_update_and_increase"
                ),
                "start_latitude": 50.09,
                "start_longitude": 14.0,
                "end_latitude": 50.18,
                "end_longitude": 14.0,
            },
        ]

        changed, check = STORAGE_MODULE.reconcile_odometer_day(segments)

        self.assertGreater(changed, 0)
        self.assertGreater(segments[0]["distance_km"], 0)
        self.assertGreater(segments[1]["distance_km"], 0)
        self.assertAlmostEqual(
            segments[0]["distance_km"] + segments[1]["distance_km"],
            20.0,
            places=3,
        )
        self.assertTrue(check["consistent"])

    def test_next_start_anchor_backfills_previous_timeout(self) -> None:
        """A fresh counter at the next departure exactly closes one zero leg."""
        segments = [
            {
                "id": "previous",
                "started_at": "2026-08-21T08:00:00+00:00",
                "start_odometer_km": 2000.0,
                "end_odometer_km": 2000.0,
                "distance_km": 0.0,
                "odometer_wait_timed_out": True,
                "odometer_completion_source": "timeout_latest_value",
            }
        ]

        _, check = STORAGE_MODULE.reconcile_odometer_day(segments, 2012.5)

        self.assertEqual(segments[0]["distance_km"], 13)
        self.assertEqual(
            segments[0]["odometer_reconciliation_boundary_source"],
            "next_segment_start",
        )
        self.assertTrue(check["consistent"])

    def test_final_day_anchor_repairs_163_km_segments_to_real_156_km(self) -> None:
        """The newest counter wins when an earlier cloud value was too high."""
        displayed_distances = [72.815, 5.902, 75.8, 0.483, 7.0, 1.0]
        segments = []
        for index, distance in enumerate(displayed_distances):
            segment = {
                "id": f"segment-{index}",
                "started_at": f"2026-08-21T{8 + index:02d}:00:00+00:00",
                "start_odometer_km": 10000.0,
                "end_odometer_km": 10000.0,
                "distance_km": distance,
                "distance_km_raw": distance,
                "odometer_wait_timed_out": False,
                "odometer_shared_update": index < 3,
                "odometer_completion_source": (
                    "post_disconnect_update_and_increase"
                ),
            }
            segments.append(segment)
        segments[3]["end_odometer_km"] = 10155.0
        segments[4]["end_odometer_km"] = 10162.0
        segments[5]["end_odometer_km"] = 10156.0
        segments[5]["manual_distance_override"] = True

        changed, check = STORAGE_MODULE.reconcile_odometer_day(segments)

        self.assertGreater(changed, 0)
        self.assertEqual(sum(segment["distance_km"] for segment in segments), 156)
        self.assertTrue(all(isinstance(segment["distance_km"], int) for segment in segments))
        self.assertTrue(all(segment["distance_km"] >= 1 for segment in segments))
        self.assertEqual(check["odometer_delta_km"], 156)
        self.assertEqual(check["difference_km"], 0)
        self.assertTrue(check["consistent"])
        self.assertEqual(
            segments[0]["daily_odometer_override_reason"],
            "non_monotonic_odometer_anchors",
        )
        changed_again, second_check = STORAGE_MODULE.reconcile_odometer_day(segments)
        self.assertEqual(changed_again, 0)
        self.assertTrue(second_check["consistent"])


if __name__ == "__main__":
    unittest.main()
