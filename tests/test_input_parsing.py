"""Tests for tolerant Home Assistant input parsing."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "kniha_jizd_input_parsing",
    ROOT / "custom_components/kniha_jizd/input_parsing.py",
)
assert SPEC is not None and SPEC.loader is not None
INPUT_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INPUT_MODULE)


class InputParsingTest(unittest.TestCase):
    """Cover Companion GPS fallbacks and localized odometer values."""

    def test_reads_standard_device_tracker_coordinates(self) -> None:
        """Use the normal latitude and longitude attributes first."""
        result = INPUT_MODULE.coordinates_from_state(
            "home", {"latitude": 49.295, "longitude": 17.393}
        )

        self.assertEqual(result, (49.295, 17.393, "latitude_longitude"))

    def test_reads_android_geocoded_location_attribute(self) -> None:
        """Accept the lowercase Location attribute exposed by Android."""
        result = INPUT_MODULE.coordinates_from_state(
            "Vrchlického 699", {"location": [49.2958889, 17.3934167]}
        )

        self.assertEqual(result, (49.2958889, 17.3934167, "location"))

    def test_reads_ios_location_text_attribute_case_insensitively(self) -> None:
        """Accept an iOS-style capitalized Location attribute represented as text."""
        result = INPUT_MODULE.coordinates_from_state(
            "Address", {"Location": "49.2958889, 17.3934167"}
        )

        self.assertEqual(result, (49.2958889, 17.3934167, "location"))

    def test_parses_localized_odometer_state_with_unit(self) -> None:
        """Handle spaces, thousands separators, decimals and a km suffix."""
        self.assertEqual(INPUT_MODULE.parse_measurement("98 332 km"), 98332.0)
        self.assertEqual(INPUT_MODULE.parse_measurement("98.332 km"), 98332.0)
        self.assertEqual(INPUT_MODULE.parse_measurement("98,332.5 km"), 98332.5)
        self.assertEqual(INPUT_MODULE.parse_measurement("98332,5 km"), 98332.5)

    def test_uses_known_odometer_attribute_when_state_is_not_numeric(self) -> None:
        """Some vehicle integrations expose mileage as an entity attribute."""
        result = INPUT_MODULE.odometer_from_state(
            "unknown", {"total_distance": "98 332 km"}
        )

        self.assertEqual(result, (98332.0, "attribute:total_distance"))


if __name__ == "__main__":
    unittest.main()
