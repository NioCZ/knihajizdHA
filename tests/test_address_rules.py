"""Tests for configured home and company address recognition."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "kniha_jizd_address_rules",
    ROOT / "custom_components/kniha_jizd/address_rules.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AddressRulesTest(unittest.TestCase):
    """Recognize configured short forms inside Companion full addresses."""

    def test_home_address_matches_full_geocoded_address(self) -> None:
        """Ignore accents, punctuation, postcode and country additions."""
        self.assertTrue(
            MODULE.address_matches_reference(
                "Testovací 123, 123 45 Ukázkov, Česko",
                "Testovací 123, Ukázkov",
            )
        )

    def test_company_address_matches_without_city_in_reference(self) -> None:
        """Allow the configured company street and house number to be concise."""
        self.assertTrue(
            MODULE.address_matches_reference(
                "Firemní 456, 123 45 Ukázkov, Česko",
                "Firemní 456",
            )
        )

    def test_house_number_is_required(self) -> None:
        """Do not classify another building in the same street as home."""
        self.assertFalse(
            MODULE.address_matches_reference(
                "Testovací 124, Ukázkov",
                "Testovací 123, Ukázkov",
            )
        )

    def test_configured_coordinate_distance(self) -> None:
        """Use the supplied DMS-derived point for a nearby parking position."""
        distance = MODULE.coordinate_distance_m(
            50.0001,
            14.0002,
            50.0,
            14.0,
        )

        self.assertIsNotNone(distance)
        self.assertLess(distance, 25)

    def test_missing_gps_has_no_distance(self) -> None:
        """Allow address fallback only when usable GPS is absent."""
        self.assertIsNone(
            MODULE.coordinate_distance_m(None, None, 50.0, 14.0)
        )

    def test_gps_inside_configured_radius_wins(self) -> None:
        """Recognize parking near home even when the address text differs."""
        match = MODULE.configured_place_match(
            50.0001,
            14.0002,
            ["Jiný text adresy"],
            "Testovací 123, Ukázkov",
            50.0,
            14.0,
            1000,
        )

        self.assertEqual(match["method"], "gps")

    def test_distant_gps_blocks_stale_matching_address(self) -> None:
        """Never let stale Companion text override authoritative distant GPS."""
        match = MODULE.configured_place_match(
            51.0,
            15.0,
            ["Testovací 123, Ukázkov"],
            "Testovací 123, Ukázkov",
            50.0,
            14.0,
            1000,
        )

        self.assertIsNone(match)

    def test_address_is_fallback_without_gps(self) -> None:
        """Keep recognition available if the phone temporarily has no GPS."""
        match = MODULE.configured_place_match(
            None,
            None,
            ["Testovací 123, 123 45 Ukázkov, Česko"],
            "Testovací 123, Ukázkov",
            50.0,
            14.0,
            1000,
        )

        self.assertEqual(match["method"], "address")

    def test_czech_address_is_shortened(self) -> None:
        """Drop domestic postcode, country and administrative region."""
        shortened = MODULE.shorten_address(
            "Testovací 123, 123 45 Ukázkov, okres Ukázkov, Zkušební kraj, Česko"
        )

        self.assertEqual(shortened, "Testovací 123, Ukázkov")

    def test_foreign_address_stays_complete(self) -> None:
        """Keep country and postcode when they identify a foreign destination."""
        address = "Universitätsstraße 1, 1010 Wien, Österreich"

        self.assertEqual(MODULE.shorten_address(address), address)


if __name__ == "__main__":
    unittest.main()
