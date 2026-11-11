"""Tests for plan_refine — 9 tests, stdlib unittest only."""

import copy
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from plan_refine import (  # noqa: E402
    RefinementRefused,
    refine,
)

BASE = {
    "id": "summarise-paper",
    "version": 1,
    "goal": "Summarise this paper and email the summary to me by 6 PM today",
    "assumptions": ["The goal is complete."],
    "constraints": ["Nothing leaves the system before approval."],
    "risks": ["Wrong recipient."],
    "steps": [
        {"id": "step-1", "action": "Restate the goal.",
         "why": "Avoid acting on a misread goal.", "needs_permission": False,
         "failure_points": ["Restatement is wrong"],
         "alternatives_considered": []},
        {"id": "step-2", "action": "Send the summary immediately.",
         "why": "Deadline is 6 PM.", "needs_permission": False,
         "failure_points": [], "alternatives_considered": []},
    ],
}

SENT_BACK = [{
    "plan_id": "summarise-paper", "plan_version": "abc",
    "decision": "request-changes", "reviewer": "Anubhav",
    "reason": "Step 2 must be gated.", "changes": ["Gate step 2."],
}]


def plan():
    return copy.deepcopy(BASE)


class TestRefine(unittest.TestCase):
    def test_gate_step_sets_permission(self):
        refined, _ = refine(plan(), [{"op": "gate-step", "step": "step-2"}],
                            "Anubhav", "Ungated send is unsafe.")
        self.assertTrue(refined["steps"][1]["needs_permission"])

    def test_add_failure_point_appends(self):
        refined, _ = refine(
            plan(), [{"op": "add-failure-point", "step": "step-2",
                      "failure_point": "Wrong recipient"}],
            "Anubhav", "Name the failure mode.")
        self.assertIn("Wrong recipient", refined["steps"][1]["failure_points"])

    def test_reword_keeps_id_and_bumps_version(self):
        refined, record = refine(
            plan(), [{"op": "reword-step", "step": "step-2",
                      "action": "Confirm the recipient, then send."}],
            "Anubhav", "Reviewer asked for a recipient check.")
        self.assertEqual(refined["id"], "summarise-paper")
        self.assertEqual(refined["version"], 2)
        self.assertEqual(record["from_version"], 1)
        self.assertEqual(record["to_version"], 2)
        self.assertEqual(refined["steps"][1]["action"],
                         "Confirm the recipient, then send.")

    def test_add_step_and_duplicate_id_refused(self):
        new_step = {"id": "step-3", "action": "Verify the delivery receipt.",
                    "why": "A sent message is not a delivered one.",
                    "needs_permission": False, "failure_points": [],
                    "alternatives_considered": []}
        refined, _ = refine(plan(), [{"op": "add-step", "step": new_step,
                                      "after": "step-2"}],
                            "Anubhav", "Add a delivery check.")
        self.assertEqual([s["id"] for s in refined["steps"]],
                         ["step-1", "step-2", "step-3"])
        with self.assertRaises(RefinementRefused):
            refine(plan(), [{"op": "add-step", "step": new_step},
                            {"op": "add-step", "step": new_step}],
                   "Anubhav", "Duplicate step in one refinement.")

    def test_remove_step_and_last_step_refused(self):
        refined, _ = refine(plan(), [{"op": "remove-step", "step": "step-1"}],
                            "Anubhav", "Drop the restatement.")
        self.assertEqual([s["id"] for s in refined["steps"]], ["step-2"])
        with self.assertRaises(RefinementRefused):
            refine(plan(), [{"op": "remove-step", "step": "step-1"},
                            {"op": "remove-step", "step": "step-2"}],
                   "Anubhav", "Cannot empty the plan.")

    def test_unknown_op_and_missing_step_refused(self):
        with self.assertRaises(RefinementRefused):
            refine(plan(), [{"op": "teleport-step", "step": "step-1"}],
                   "Anubhav", "No such op.")
        with self.assertRaises(RefinementRefused):
            refine(plan(), [{"op": "gate-step", "step": "step-99"}],
                   "Anubhav", "No such step.")

    def test_empty_changes_and_missing_reason_refused(self):
        with self.assertRaises(RefinementRefused):
            refine(plan(), [], "Anubhav", "Nothing to apply.")
        with self.assertRaises(RefinementRefused):
            refine(plan(), [{"op": "gate-step", "step": "step-2"}],
                   "Anubhav", "")

    def test_decisions_check_enforced_when_supplied(self):
        approved = [dict(SENT_BACK[0], decision="approve")]
        with self.assertRaises(RefinementRefused):
            refine(plan(), [{"op": "gate-step", "step": "step-2"}],
                   "Anubhav", "Already approved.", prior_decisions=approved)
        refined, _ = refine(plan(), [{"op": "gate-step", "step": "step-2"}],
                            "Anubhav", "Sent back for changes.",
                            prior_decisions=SENT_BACK)
        self.assertTrue(refined["steps"][1]["needs_permission"])

    def test_original_plan_is_not_mutated(self):
        original = plan()
        refine(original, [{"op": "gate-step", "step": "step-2"},
                          {"op": "add-constraint", "text": "New rule."}],
               "Anubhav", "Apply the reviewer's notes.")
        self.assertFalse(original["steps"][1]["needs_permission"])
        self.assertEqual(original["version"], 1)
        self.assertNotIn("New rule.", original["constraints"])


if __name__ == "__main__":
    unittest.main()
