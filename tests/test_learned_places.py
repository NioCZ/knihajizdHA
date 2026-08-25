"""Tests for independent learned physical locations."""

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
    """Verify migration, deduplication and matching of physical points."""

    def test_history_aggregates_calendar_days_and_selected_rows(self) -> None:
        """Provide colored month totals and only the chosen day's table rows."""
        history = STORAGE_MODULE.calculate_history(
            [
                {
                    "id": "business-one",
                    "date": "2026-08-04",
                    "started_at": "2026-08-04T08:00:00+00:00",
                    "trip_type": "business",
                    "distance_km": 12.4,
                },
                {
                    "id": "private-one",
                    "date": "2026-08-04",
                    "started_at": "2026-08-04T18:00:00+00:00",
                    "trip_type": "private",
                    "distance_km": 3.4,
                },
                {
                    "id": "business-two",
                    "date": "2026-08-10",
                    "started_at": "2026-08-10T09:00:00+00:00",
                    "trip_type": "business",
                    "distance_km": 5.7,
                },
                {
                    "id": "other-month",
                    "date": "2026-07-31",
                    "trip_type": "business",
                    "distance_km": 100,
                },
            ],
            "2026-08",
            "2026-08-04",
        )

        self.assertEqual(history["month_business_km"], 18)
        self.assertEqual(history["month_private_km"], 3)
        self.assertEqual(history["month_trips"], 3)
        self.assertEqual(len(history["days"]), 2)
        self.assertEqual(history["days"][0]["business_trips"], 1)
        self.assertEqual(history["days"][0]["private_trips"], 1)
        self.assertEqual(history["days"][0]["business_km"], 12)
        self.assertEqual(history["days"][0]["private_km"], 3)
        self.assertEqual(
            [row["id"] for row in history["rows"]],
            ["business-one", "private-one"],
        )

    def test_unanswered_short_stop_is_visible_for_review_without_km_type(self) -> None:
        """Count an auto-closed stop separately from private and business totals."""
        segment = {
            "id": "review",
            "date": "2026-08-04",
            "started_at": "2026-08-04T10:00:00+00:00",
            "trip_type": "unclassified",
            "distance_km": 8,
            "needs_review": True,
        }

        history = STORAGE_MODULE.calculate_history(
            [segment], "2026-08", "2026-08-04"
        )
        statistics = STORAGE_MODULE.calculate_statistics([segment], "2026-08-04")

        self.assertEqual(history["month_business_km"], 0)
        self.assertEqual(history["month_private_km"], 0)
        self.assertEqual(history["month_review_trips"], 1)
        self.assertEqual(history["days"][0]["review_trips"], 1)
        self.assertEqual(statistics["review_count_total"], 1)
        self.assertEqual(statistics["today_review_count"], 1)

    def test_known_place_behavior_matches_journey_rules(self) -> None:
        """Auto-classify normal places and hold a private shop on business return."""
        hospital = {
            "trip_type": "business",
            "trip_types": ["business"],
            "place_role": "client",
        }
        shop = {
            "trip_type": "private",
            "trip_types": ["private"],
            "place_role": "private",
        }
        exception = {
            "trip_type": "contextual",
            "trip_types": ["business", "private"],
            "place_role": "mixed",
        }
        business_shop = {
            "trip_type": "business",
            "trip_types": ["business"],
            "place_role": "client",
            "transient_capable": True,
        }

        self.assertEqual(
            STORAGE_MODULE.learned_place_behavior(hospital, False), "business"
        )
        self.assertEqual(
            STORAGE_MODULE.learned_place_behavior(shop, False), "private"
        )
        self.assertEqual(
            STORAGE_MODULE.learned_place_behavior(shop, True), "transient"
        )
        self.assertEqual(
            STORAGE_MODULE.learned_place_behavior(exception, False), "confirm"
        )
        self.assertEqual(
            STORAGE_MODULE.learned_place_behavior(business_shop, True), "business"
        )

    def test_map_uses_private_zones_and_omits_transient_places(self) -> None:
        """Keep private zones conservative and short stops off the map."""
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
                        "id": "return",
                        "label": "Staré návratové místo",
                        "trip_type": "contextual",
                        "place_role": "return",
                        "anchors": [{"latitude": 50.15, "longitude": 14.15}],
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
        self.assertEqual(by_id["private"]["anchor_index"], 0)
        self.assertNotIn("fuel", by_id)
        self.assertNotIn("return", by_id)
        self.assertEqual(by_id["client"]["radius_m"], 1000)

    def test_configured_home_suppresses_old_learned_duplicates(self) -> None:
        """Prefer one configured home marker over private/business learned copies."""
        markers = [
            {
                "id": "old-private",
                "label": "Soukromá",
                "latitude": 50.0002,
                "longitude": 14.0,
            },
            {
                "id": "old-return",
                "label": "Domov",
                "latitude": 50.004,
                "longitude": 14.0,
            },
            {
                "id": "neighbour",
                "label": "Klient vedle",
                "latitude": 50.012,
                "longitude": 14.0,
            },
            {
                "id": "inside-home-zone",
                "label": "Stará služební duplicita",
                "latitude": 50.001,
                "longitude": 14.0,
            },
        ]
        configured = [
            {
                "id": "configured:home",
                "label": "Domov",
                "latitude": 50.0,
                "longitude": 14.0,
                "radius_m": 1000,
            }
        ]

        visible = STORAGE_MODULE.suppress_configured_place_duplicates(
            markers, configured
        )

        self.assertEqual([marker["id"] for marker in visible], ["neighbour"])

    def test_same_label_keeps_two_distant_physical_places(self) -> None:
        """A shared label must never group geographically distant points."""
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
                    "id": "first",
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
            self.assertEqual(document["version"], 6)
            self.assertEqual(len(document["places"]), 2)
            self.assertTrue(
                all(len(place["anchors"]) == 1 for place in document["places"])
            )
            self.assertNotEqual(
                document["places"][0]["id"], document["places"][1]["id"]
            )

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

    def test_return_is_not_learned_over_an_existing_private_place(self) -> None:
        """Keep return on the trip and preserve the destination's real place type."""
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
            self.assertEqual(document["version"], 6)
            self.assertEqual(len(document["places"]), 1)
            self.assertNotEqual(match.get("place_role"), "return")
            self.assertEqual(match["trip_type"], "private")

    def test_migration_removes_pure_return_and_preserves_real_type(self) -> None:
        """Convert legacy return records without inventing a new map category."""
        document = {
            "version": 4,
            "places": [
                {
                    "id": "pure-return",
                    "trip_type": "contextual",
                    "trip_types": [],
                    "place_role": "return",
                },
                {
                    "id": "private-return",
                    "trip_type": "contextual",
                    "trip_types": ["private"],
                    "place_role": "return",
                },
            ],
        }

        changed = STORAGE_MODULE.migrate_return_places(document)

        self.assertTrue(changed)
        self.assertEqual([place["id"] for place in document["places"]], ["private-return"])
        self.assertEqual(document["places"][0]["place_role"], "private")
        self.assertEqual(document["places"][0]["trip_type"], "private")

    def test_colocated_private_and_business_labels_form_one_exception(self) -> None:
        """Keep both classifications in one record and draw only one map point."""
        test_output = ROOT / "test-output"
        test_output.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=test_output) as temporary_directory:
            repository = STORAGE_MODULE.KnihaJizdRepository.__new__(
                STORAGE_MODULE.KnihaJizdRepository
            )
            repository.places_path = Path(temporary_directory) / "learned_places.json"
            repository._learn_place_sync(
                {
                    "id": "private",
                    "latitude": 50.0,
                    "longitude": 14.0,
                    "address": "Společné parkoviště",
                    "label": "Soukromý cíl",
                    "trip_type": "private",
                    "place_role": "private",
                }
            )
            repository._learn_place_sync(
                {
                    "id": "business",
                    "latitude": 50.0001,
                    "longitude": 14.0,
                    "address": "Společné parkoviště",
                    "label": "Služební cíl",
                    "trip_type": "business",
                    "place_role": "client",
                }
            )

            document = json.loads(repository.places_path.read_text(encoding="utf-8"))
            markers = STORAGE_MODULE.places_for_map(document, 1000)

            self.assertEqual(len(document["places"]), 1)
            self.assertEqual(
                document["places"][0]["trip_types"], ["business", "private"]
            )
            self.assertEqual(document["places"][0]["place_role"], "mixed")
            self.assertEqual(len(markers), 1)
            self.assertEqual(markers[0]["place_role"], "mixed")

    def test_migration_merges_legacy_colocated_duplicates(self) -> None:
        """Collapse old private/business records into one physical exception."""
        document = {
            "version": 3,
            "places": [
                {
                    "id": "old-private",
                    "label": "Soukromé místo",
                    "trip_type": "private",
                    "place_role": "private",
                    "latitude": 50.0,
                    "longitude": 14.0,
                    "address": "Stejné parkoviště",
                },
                {
                    "id": "old-business",
                    "label": "Služební místo",
                    "trip_type": "business",
                    "place_role": "client",
                    "latitude": 50.0001,
                    "longitude": 14.0,
                    "address": "Stejné parkoviště",
                },
            ],
        }

        changed = STORAGE_MODULE.consolidate_learned_places(document)

        self.assertTrue(changed)
        self.assertEqual(document["version"], 6)
        self.assertEqual(len(document["places"]), 1)
        self.assertEqual(
            document["places"][0]["trip_types"], ["business", "private"]
        )
        self.assertEqual(document["places"][0]["place_role"], "mixed")
        self.assertEqual(len(document["places"][0]["anchors"]), 1)

    def test_expired_short_stop_can_become_a_known_private_destination(self) -> None:
        """Reclassify one transient record without creating a duplicate point."""
        test_output = ROOT / "test-output"
        test_output.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=test_output) as temporary_directory:
            repository = STORAGE_MODULE.KnihaJizdRepository.__new__(
                STORAGE_MODULE.KnihaJizdRepository
            )
            repository.places_path = Path(temporary_directory) / "learned_places.json"
            repository._learn_place_sync(
                {
                    "id": "shop",
                    "latitude": 50.0,
                    "longitude": 14.0,
                    "label": "Obchod",
                    "trip_type": "contextual",
                    "place_role": "transient",
                }
            )
            repository._learn_place_sync(
                {
                    "id": "shop",
                    "latitude": 50.0,
                    "longitude": 14.0,
                    "label": "Obchod",
                    "trip_type": "private",
                    "place_role": "private",
                    "transient_capable": True,
                    "transient_kind": "shop",
                }
            )

            place = json.loads(
                repository.places_path.read_text(encoding="utf-8")
            )["places"][0]

            self.assertEqual(place["trip_types"], ["private"])
            self.assertEqual(place["place_role"], "private")
            self.assertTrue(place["transient_capable"])
            self.assertEqual(place["transient_kind"], "shop")

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

    def test_place_management_updates_merges_and_deletes_records(self) -> None:
        """Expose all requested place-management operations over stable IDs."""
        test_output = ROOT / "test-output"
        test_output.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=test_output) as temporary_directory:
            repository = STORAGE_MODULE.KnihaJizdRepository.__new__(
                STORAGE_MODULE.KnihaJizdRepository
            )
            repository.places_path = Path(temporary_directory) / "learned_places.json"
            repository.places_path.write_text(
                json.dumps(
                    {
                        "version": 5,
                        "places": [
                            {
                                "id": "one",
                                "label": "První",
                                "trip_type": "business",
                                "place_role": "client",
                                "anchors": [{"latitude": 50.0, "longitude": 14.0}],
                            },
                            {
                                "id": "two",
                                "label": "Druhý",
                                "trip_type": "private",
                                "place_role": "private",
                                "anchors": [
                                    {"latitude": 50.0001, "longitude": 14.0}
                                ],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            repository._update_place_sync("one", "Klient A", "mixed", 325)
            repository._merge_places_sync(
                ["one", "two"], "Společné místo", "mixed", 300
            )
            managed = repository._get_managed_places_sync(500, 250, 200)

            self.assertEqual(len(managed), 1)
            self.assertEqual(managed[0]["label"], "Společné místo")
            self.assertEqual(managed[0]["classification"], "mixed")
            self.assertEqual(managed[0]["radius_m"], 300)
            self.assertEqual(managed[0]["anchor_count"], 1)

            repository._delete_place_sync("one")
            self.assertEqual(repository._get_managed_places_sync(500, 250, 200), [])

    def test_migration_splits_distant_anchors_into_independent_places(self) -> None:
        """Upgrade old grouped data without losing any physical point."""
        document = {
            "version": 5,
            "places": [
                {
                    "id": "private-group",
                    "label": "Soukromé místo",
                    "trip_type": "private",
                    "place_role": "private",
                    "anchors": [
                        {
                            "latitude": 49.29442,
                            "longitude": 17.39996,
                            "address": "Kroměříž",
                        },
                        {
                            "latitude": 49.20441,
                            "longitude": 17.57316,
                            "address": "Zlín",
                        },
                        {
                            "latitude": 49.78772,
                            "longitude": 18.41292,
                            "address": "Havířov",
                        },
                    ],
                }
            ],
        }

        changed = STORAGE_MODULE.consolidate_learned_places(document)

        self.assertTrue(changed)
        self.assertEqual(document["version"], 6)
        self.assertEqual(len(document["places"]), 3)
        self.assertEqual(
            {place["anchors"][0]["address"] for place in document["places"]},
            {"Kroměříž", "Zlín", "Havířov"},
        )
        self.assertEqual(len({place["id"] for place in document["places"]}), 3)
        self.assertTrue(
            all(len(place["anchors"]) == 1 for place in document["places"])
        )
        migrated_ids = [place["id"] for place in document["places"]]

        changed_again = STORAGE_MODULE.consolidate_learned_places(document)

        self.assertFalse(changed_again)
        self.assertEqual(
            [place["id"] for place in document["places"]], migrated_ids
        )

    def test_manual_merge_rejects_distant_points(self) -> None:
        """The management API cannot recreate a geographically mixed record."""
        test_output = ROOT / "test-output"
        test_output.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=test_output) as temporary_directory:
            repository = STORAGE_MODULE.KnihaJizdRepository.__new__(
                STORAGE_MODULE.KnihaJizdRepository
            )
            repository.places_path = Path(temporary_directory) / "learned_places.json"
            repository.places_path.write_text(
                json.dumps(
                    {
                        "version": 6,
                        "places": [
                            {
                                "id": "kromeriz",
                                "label": "Soukromé místo",
                                "trip_type": "private",
                                "place_role": "private",
                                "anchors": [{"latitude": 49.3, "longitude": 17.4}],
                            },
                            {
                                "id": "haviřov",
                                "label": "Soukromé místo",
                                "trip_type": "private",
                                "place_role": "private",
                                "anchors": [{"latitude": 49.8, "longitude": 18.4}],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "maximum je 25 m"):
                repository._merge_places_sync(
                    ["kromeriz", "haviřov"], None, None, None
                )

    def test_place_management_can_delete_only_one_physical_anchor(self) -> None:
        """Removing a duplicate map point must preserve the logical place."""
        test_output = ROOT / "test-output"
        test_output.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=test_output) as temporary_directory:
            repository = STORAGE_MODULE.KnihaJizdRepository.__new__(
                STORAGE_MODULE.KnihaJizdRepository
            )
            repository.places_path = Path(temporary_directory) / "learned_places.json"
            repository.places_path.write_text(
                json.dumps(
                    {
                        "version": 5,
                        "places": [
                            {
                                "id": "one",
                                "label": "Klient",
                                "trip_type": "business",
                                "place_role": "client",
                                "anchors": [
                                    {
                                        "latitude": 50.0,
                                        "longitude": 14.0,
                                        "address": "Přední vjezd",
                                    },
                                    {
                                        "latitude": 50.01,
                                        "longitude": 14.0,
                                        "address": "Chybný bod",
                                    },
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            markers = STORAGE_MODULE.places_for_map(
                json.loads(repository.places_path.read_text(encoding="utf-8")),
                500,
                250,
                200,
            )
            self.assertEqual(
                [(marker["id"], marker["anchor_index"]) for marker in markers],
                [("one:0", 0), ("one:1", 1)],
            )

            result = repository._delete_place_anchor_sync("one", 1)
            managed = repository._get_managed_places_sync(500, 250, 200)

            self.assertFalse(result["place_deleted"])
            self.assertEqual(len(managed), 1)
            self.assertEqual(managed[0]["anchor_count"], 1)
            self.assertEqual(managed[0]["anchors"][0]["address"], "Přední vjezd")

            result = repository._delete_place_anchor_sync("one", 0)

            self.assertTrue(result["place_deleted"])
            self.assertEqual(repository._get_managed_places_sync(500, 250, 200), [])

    def test_manual_historical_correction_retrains_place_exactly(self) -> None:
        """Replace a wrong learned default instead of creating a mixed duplicate."""
        test_output = ROOT / "test-output"
        test_output.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=test_output) as temporary_directory:
            repository = STORAGE_MODULE.KnihaJizdRepository.__new__(
                STORAGE_MODULE.KnihaJizdRepository
            )
            repository.raw_path = Path(temporary_directory) / "raw.json"
            repository.places_path = Path(temporary_directory) / "learned_places.json"
            repository.raw_path.write_text(
                json.dumps(
                    {
                        "version": 5,
                        "segments": [
                            {
                                "id": "trip",
                                "journey_id": "journey",
                                "journey_role": "destination",
                                "ended_at": "2026-08-24T10:00:00+00:00",
                                "end_latitude": 50.0,
                                "end_longitude": 14.0,
                                "end_address": "Albert Kroměříž",
                                "map_estimate": "Albert Kroměříž",
                                "matched_place_id": "place",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            repository.places_path.write_text(
                json.dumps(
                    {
                        "version": 5,
                        "places": [
                            {
                                "id": "place",
                                "label": "Chybný klient",
                                "trip_type": "business",
                                "trip_types": ["business"],
                                "place_role": "client",
                                "anchors": [{"latitude": 50.0, "longitude": 14.0}],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = repository._sync_place_from_trip_sync(
                "trip", "Soukromá", "private", 500, 250
            )
            place = json.loads(
                repository.places_path.read_text(encoding="utf-8")
            )["places"][0]

            self.assertTrue(result["place_updated"])
            self.assertEqual(place["trip_type"], "private")
            self.assertEqual(place["trip_types"], ["private"])
            self.assertEqual(place["place_role"], "private")
            self.assertEqual(place["label"], "Albert Kroměříž")
            self.assertEqual(place["radius_m"], 250)

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

    def test_unchanged_panel_values_do_not_lock_automatic_distance(self) -> None:
        """Saving only classification must not turn displayed zero into an override."""
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
                                "date": "2026-08-25",
                                "purpose": "Soukromá",
                                "trip_type": "private",
                                "start_address": "Brno",
                                "end_address": "Kroměříž",
                                "distance_km": 0,
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            repository._update_trip_sync(
                "segment", "", "business", "Brno", "Kroměříž", 0
            )
            segment = json.loads(
                repository.raw_path.read_text(encoding="utf-8")
            )["segments"][0]

            self.assertNotIn("manual_distance_override", segment)
            self.assertNotIn("start_address_manual", segment)
            self.assertNotIn("end_address_manual", segment)

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
