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
        self.assertEqual(statistics["today_business_km"], 32.65)
        self.assertEqual(statistics["today_private_km"], 8.6)
        self.assertEqual(statistics["last_segment"]["id"], "segment-c")


if __name__ == "__main__":
    unittest.main()
