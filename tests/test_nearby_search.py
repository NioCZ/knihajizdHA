"""Tests for domain-aware nearby institution scoring."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).parents[1]
if "aiohttp" not in sys.modules:
    aiohttp_stub = types.ModuleType("aiohttp")
    aiohttp_stub.ClientError = type("ClientError", (Exception,), {})
    aiohttp_stub.ClientSession = object
    sys.modules["aiohttp"] = aiohttp_stub
SPEC = importlib.util.spec_from_file_location(
    "kniha_jizd_nearby_search",
    ROOT / "custom_components/kniha_jizd/nearby_search.py",
)
assert SPEC is not None and SPEC.loader is not None
SEARCH_MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SEARCH_MODULE)


class NearbyInstitutionScoringTest(unittest.TestCase):
    """Verify that domain fit outranks raw proximity."""

    def test_genetics_research_center_outranks_nearer_hospital(self) -> None:
        """Prefer a genetics institute despite its greater distance."""
        payload = {
            "elements": [
                {
                    "type": "node",
                    "id": 1,
                    "lat": 50.0018,
                    "lon": 14.0,
                    "tags": {
                        "name": "Fakultní nemocnice",
                        "amenity": "hospital",
                    },
                },
                {
                    "type": "node",
                    "id": 2,
                    "lat": 50.0090,
                    "lon": 14.0,
                    "tags": {
                        "name": "Ústav molekulární genetiky AV ČR",
                        "amenity": "research_institute",
                        "research": "genomics",
                    },
                },
                {
                    "type": "node",
                    "id": 3,
                    "lat": 50.0045,
                    "lon": 14.0,
                    "tags": {
                        "name": "Univerzita Karlova",
                        "amenity": "university",
                    },
                },
            ]
        }
        keywords = SEARCH_MODULE.parse_keywords(
            "genet, genom, dna, molekul, biomed, laborato"
        )

        candidates = SEARCH_MODULE.rank_overpass_candidates(
            payload, 50.0, 14.0, keywords
        )

        self.assertEqual(candidates[0]["name"], "Ústav molekulární genetiky AV ČR")
        self.assertGreater(candidates[0]["score"], candidates[1]["score"])
        self.assertIn("genet", candidates[0]["keyword_matches"])
        self.assertIn("molekul", candidates[0]["keyword_matches"])

    def test_query_uses_one_radius_for_all_relevant_tags(self) -> None:
        """Build one bounded Overpass request rather than many API calls."""
        query = SEARCH_MODULE.build_overpass_query(50.1, 14.4, 3000)

        self.assertIn("around:3000,50.1000000,14.4000000", query)
        self.assertIn('"amenity"', query)
        self.assertIn('"healthcare"', query)
        self.assertIn('"healthcare:speciality"', query)
        self.assertIn('"office"="research"', query)
        self.assertIn("out bb", query)
        self.assertEqual(query.count("[out:json]"), 1)

    def test_blood_center_is_searched_and_ranked_as_specialist(self) -> None:
        """Recognize a blood center even when OSM does not tag it as a hospital."""
        query = SEARCH_MODULE.build_overpass_query(49.3, 17.4, 3000)
        self.assertIn("blood_bank", query)
        self.assertIn("transfu", query)

        candidates = SEARCH_MODULE.rank_overpass_candidates(
            {
                "elements": [
                    {
                        "type": "node",
                        "id": 30,
                        "lat": 49.3,
                        "lon": 17.4,
                        "tags": {
                            "name": "Krevní centrum",
                            "healthcare": "blood_donation",
                            "healthcare:speciality": "haematology",
                        },
                    },
                    {
                        "type": "node",
                        "id": 31,
                        "lat": 49.3001,
                        "lon": 17.4,
                        "tags": {"name": "Nemocnice", "amenity": "hospital"},
                    },
                ]
            },
            49.3,
            17.4,
            SEARCH_MODULE.parse_keywords("krev, hematol, transfuz, blood"),
        )

        self.assertEqual(candidates[0]["name"], "Krevní centrum")
        self.assertIn("krev", candidates[0]["keyword_matches"])

    def test_parking_inside_institution_bounds_gets_bonus(self) -> None:
        """A large campus surrounding the parking point is recognized."""
        payload = {
            "elements": [
                {
                    "type": "way",
                    "id": 10,
                    "bounds": {
                        "minlat": 49.999,
                        "maxlat": 50.001,
                        "minlon": 13.999,
                        "maxlon": 14.001,
                    },
                    "tags": {
                        "name": "Biomedicínské centrum",
                        "amenity": "research_institute",
                    },
                }
            ]
        }

        candidate = SEARCH_MODULE.rank_overpass_candidates(
            payload, 50.0, 14.0, ()
        )[0]

        self.assertTrue(candidate["contains_parking_point"])
        self.assertIn("parkování uvnitř areálu +30", candidate["score_reasons"])

    def test_short_dna_keyword_requires_a_whole_word(self) -> None:
        """Do not find the abbreviation DNA inside an unrelated word."""
        false_match = {
            "elements": [
                {
                    "type": "node",
                    "id": 20,
                    "lat": 50.0,
                    "lon": 14.0,
                    "tags": {"name": "Jedna klinika", "amenity": "clinic"},
                }
            ]
        }
        true_match = {
            "elements": [
                {
                    "type": "node",
                    "id": 21,
                    "lat": 50.0,
                    "lon": 14.0,
                    "tags": {"name": "DNA laboratoř", "amenity": "laboratory"},
                }
            ]
        }

        false_candidate = SEARCH_MODULE.rank_overpass_candidates(
            false_match, 50.0, 14.0, ("dna",)
        )[0]
        true_candidate = SEARCH_MODULE.rank_overpass_candidates(
            true_match, 50.0, 14.0, ("dna",)
        )[0]

        self.assertEqual(false_candidate["keyword_matches"], [])
        self.assertEqual(true_candidate["keyword_matches"], ["dna"])

    def test_successful_search_is_cached_with_visible_diagnostics(self) -> None:
        """Avoid repeated Overpass calls and distinguish a cache result."""
        searcher = SEARCH_MODULE.NearbyInstitutionSearcher(
            object(), "https://example.invalid", "test", "krev"
        )
        calls = 0

        async def fake_request(_query: str) -> dict[str, object]:
            nonlocal calls
            calls += 1
            return {
                "elements": [
                    {
                        "type": "node",
                        "id": 1,
                        "lat": 49.3,
                        "lon": 17.4,
                        "tags": {"name": "Krevní centrum", "amenity": "blood_bank"},
                    }
                ]
            }

        searcher._async_request = fake_request

        async def scenario() -> None:
            first = await searcher.async_search(49.3, 17.4, 3000)
            self.assertEqual(first[0]["name"], "Krevní centrum")
            self.assertEqual(searcher.last_result["status"], "ok")
            second = await searcher.async_search(49.3, 17.4, 3000)
            self.assertEqual(second, first)
            self.assertEqual(searcher.last_result["status"], "cached")
            self.assertTrue(searcher.last_result["cache_hit"])

        asyncio.run(scenario())
        self.assertEqual(calls, 1)


if __name__ == "__main__":
    unittest.main()
