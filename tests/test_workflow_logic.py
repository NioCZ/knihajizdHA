"""Regression tests for phone and panel trip-decision policy."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]
WORKFLOW_SPEC = importlib.util.spec_from_file_location(
    "kniha_jizd_workflow_logic",
    ROOT / "custom_components/kniha_jizd/workflow_logic.py",
)
assert WORKFLOW_SPEC is not None and WORKFLOW_SPEC.loader is not None
WORKFLOW = importlib.util.module_from_spec(WORKFLOW_SPEC)
WORKFLOW_SPEC.loader.exec_module(WORKFLOW)


class WorkflowLogicTest(unittest.TestCase):
    """Keep interruption policy and panel questions deterministic."""

    def test_expired_stop_gets_the_same_single_classification_question(self) -> None:
        allowed, reason = WORKFLOW.mobile_notification_policy(
            {
                "transient_stop": {"expired": True},
                "map_candidates": [
                    {
                        "name": "Čerpací stanice",
                        "distance_m": 20,
                        "score": 100,
                    }
                ],
            }
        )

        self.assertTrue(allowed)
        self.assertEqual(reason, "classification_question")

    def test_map_confidence_does_not_change_the_type_question(self) -> None:
        missing = WORKFLOW.mobile_notification_policy({})
        weak = WORKFLOW.mobile_notification_policy(
            {
                "map_candidates": [
                    {"name": "Nejasné místo", "distance_m": 1400, "score": 5}
                ]
            }
        )

        self.assertEqual(missing, (True, "classification_question"))
        self.assertEqual(weak, (True, "classification_question"))

    def test_confident_nearby_candidate_can_notify_phone(self) -> None:
        allowed, reason = WORKFLOW.mobile_notification_policy(
            {
                "map_candidates": [
                    {
                        "name": "Fakultní nemocnice",
                        "distance_m": 110,
                        "score": 32,
                        "keyword_matches": ["nemocnice"],
                    }
                ]
            }
        )

        self.assertTrue(allowed)
        self.assertEqual(reason, "classification_question")

    def test_panel_offers_return_and_manual_fallbacks(self) -> None:
        question = WORKFLOW.panel_question(
            {
                "map_estimate": "Na Jetelce 69, Praha 9",
                "return_context": {
                    "suggested": True,
                    "previous_segment_id": "outbound",
                    "previous_purpose": "Thomayerova nemocnice",
                },
                "notification_suppressed_reason": "panel_only",
            },
            "waiting_classification",
        )

        self.assertIsNotNone(question)
        assert question is not None
        self.assertEqual(question["kind"], "return")
        self.assertEqual(question["phone_state"], "panel_only")
        self.assertEqual(
            [action["id"] for action in question["actions"]],
            ["return", "business", "private"],
        )

    def test_home_without_return_evidence_asks_a_plain_type_question(self) -> None:
        """Do not describe a normal arrival home as a detected return chain."""
        question = WORKFLOW.panel_question(
            {
                "map_estimate": "Domov",
                "return_context": {
                    "suggested": True,
                    "reason": "configured_home_destination",
                    "previous_segment_id": None,
                    "previous_purpose": None,
                },
            },
            "waiting_classification",
        )

        self.assertEqual(question["kind"], "home")
        self.assertEqual(question["title"], "Jak zařadit cestu domů?")
        self.assertEqual(
            [action["id"] for action in question["actions"]],
            ["business", "private"],
        )

    def test_panel_candidates_are_suggestions_not_decisions(self) -> None:
        question = WORKFLOW.panel_question(
            {
                "map_estimate": "Brno",
                "map_candidates": [
                    {"name": "Klient A", "distance_m": 80, "score": 40},
                    {"name": "Klient B", "distance_m": 120, "score": 30},
                ],
            },
            "waiting_classification",
        )

        assert question is not None
        self.assertEqual(
            [action["id"] for action in question["actions"]],
            ["business", "private"],
        )
        self.assertEqual(
            [candidate["name"] for candidate in question["candidates"]],
            ["Klient A", "Klient B"],
        )
        self.assertTrue(question["purpose_optional"])

    def test_resolved_rows_have_no_question(self) -> None:
        self.assertIsNone(
            WORKFLOW.panel_question(
                {"classification_ready": True}, "waiting_classification"
            )
        )

    def test_explicit_unknown_destination_gets_separate_place_question(self) -> None:
        self.assertTrue(
            WORKFLOW.should_offer_place_save(
                {
                    "end_address": "Na Jetelce 69, Praha 9",
                    "visit_role": "destination",
                },
                "notification",
                "business",
            )
        )

    def test_known_or_waypoint_destination_does_not_get_place_question(self) -> None:
        for segment in (
            {
                "end_latitude": 50.1,
                "end_longitude": 14.4,
                "matched_place_id": "known-client",
            },
            {
                "end_address": "D1 odpočívka",
                "visit_role": "waypoint",
            },
            {
                "end_address": "Firma",
                "configured_place": "company",
            },
        ):
            with self.subTest(segment=segment):
                self.assertFalse(
                    WORKFLOW.should_offer_place_save(
                        segment, "manual_panel", "business"
                    )
                )

    def test_automatic_or_return_classification_never_asks_to_save_place(self) -> None:
        destination = {"end_address": "Brno", "visit_role": "destination"}

        self.assertFalse(
            WORKFLOW.should_offer_place_save(
                destination, "learned_business", "business"
            )
        )
        self.assertFalse(
            WORKFLOW.should_offer_place_save(
                {**destination, "journey_role": "return"},
                "notification_return",
                "business",
            )
        )

    def test_poor_gps_without_real_address_does_not_offer_place_save(self) -> None:
        """Do not invite the user to persist a coordinate known to be unreliable."""
        segment = {
            "end_latitude": 50.1,
            "end_longitude": 14.4,
            "end_accuracy_m": 900,
            "end_address": "50.100000, 14.400000",
            "visit_role": "destination",
        }

        self.assertFalse(
            WORKFLOW.should_offer_place_save(
                segment, "manual_panel", "business", 500
            )
        )
        self.assertTrue(
            WORKFLOW.should_offer_place_save(
                {**segment, "end_address": "Na Příkopě 1, Praha"},
                "manual_panel",
                "business",
                500,
            )
        )

    def test_known_gps_accuracy_must_fit_the_decision_radius(self) -> None:
        """Use the same conservative accuracy gate for places and waypoints."""
        self.assertTrue(WORKFLOW.gps_accuracy_suitable({}, 200))
        self.assertTrue(
            WORKFLOW.gps_accuracy_suitable({"end_accuracy_m": 200}, 200)
        )
        self.assertFalse(
            WORKFLOW.gps_accuracy_suitable({"end_accuracy_m": 201}, 200)
        )
        self.assertTrue(
            WORKFLOW.gps_accuracy_suitable({"end_accuracy_m": -1}, 200)
        )

    def test_place_label_is_recomputed_when_trip_type_changes(self) -> None:
        """A former business purpose must not leak into a private place label."""
        prompt = {
            "map_estimate": "Parkoviště Výstaviště",
            "end_address": "Výstaviště 1, Brno",
        }

        self.assertEqual(
            WORKFLOW.place_label_suggestion(prompt, "Klient Alfa", "business"),
            "Klient Alfa",
        )
        self.assertEqual(
            WORKFLOW.place_label_suggestion(prompt, "Klient Alfa", "private"),
            "Parkoviště Výstaviště",
        )


if __name__ == "__main__":
    unittest.main()
