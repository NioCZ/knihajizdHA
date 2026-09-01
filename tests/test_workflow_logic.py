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

    def test_expired_short_stop_stays_in_panel_without_phone_interruption(self) -> None:
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

        self.assertFalse(allowed)
        self.assertEqual(reason, "short_stop_panel_only")

    def test_weak_or_missing_map_guess_does_not_notify_phone(self) -> None:
        missing = WORKFLOW.mobile_notification_policy({})
        weak = WORKFLOW.mobile_notification_policy(
            {
                "map_candidates": [
                    {"name": "Nejasné místo", "distance_m": 1400, "score": 5}
                ]
            }
        )

        self.assertEqual(missing, (False, "no_actionable_map_candidate"))
        self.assertEqual(weak, (False, "low_confidence_map_candidate"))

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
        self.assertEqual(reason, "confident_map_candidate")

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
            ["return", "business", "new", "private"],
        )

    def test_panel_candidate_actions_keep_explicit_candidate_indexes(self) -> None:
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
            [
                (action["id"], action.get("candidate_index"))
                for action in question["actions"]
            ],
            [
                ("confirm", 1),
                ("confirm", 2),
                ("business", None),
                ("new", None),
                ("private", None),
            ],
        )

    def test_resolved_rows_have_no_question(self) -> None:
        self.assertIsNone(
            WORKFLOW.panel_question(
                {"classification_ready": True}, "waiting_classification"
            )
        )


if __name__ == "__main__":
    unittest.main()
