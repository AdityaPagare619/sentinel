"""Tests for questions.py — the three Jev questions (contract §3.3).

The option lists + descriptions below are copied verbatim from the spec;
these tests fail loudly if the implementation drifts.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sentinel.questions import build_questions  # noqa: E402

EXPECTED_SEVERITY = {
    "p1_critical": (
        "Customer-facing outage or data-loss risk in progress; revenue/SLA "
        "actively burning; needs a human in under 5 minutes."
    ),
    "p2_high": (
        "Core function degraded or at imminent risk of full outage; needs a "
        "human within about 30 minutes."
    ),
    "p3_medium": (
        "Non-critical degradation or early warning; safe to handle in "
        "business hours; no page needed."
    ),
    "p4_low": "Informational; no action required unless it recurs; never pages.",
    "known_noise": (
        "Matches a recurring benign pattern (flap, self-clearing spike, "
        "planned-work artifact); historically never became an incident."
    ),
    "cannot_determine": (
        "The alert context is insufficient or contradictory to assign a "
        "severity; do not guess."
    ),
}

EXPECTED_TEAMS = {
    "platform": "Core infra, kubernetes, CI/CD runners, deploy pipeline.",
    "network": "DNS, CDN, load balancers, VPC, transit — connectivity and edge.",
    "data": "Databases, caches, queues, pipelines — persistence and streaming.",
    "product_backend": "Application services and APIs owned by product engineering.",
    "security": "Auth, WAF, intrusion, certificate expiry — the security on-call.",
    "cannot_determine": (
        "No clear owner from the alert context; route to the default "
        "escalation policy."
    ),
}

EXPECTED_DISPOSITION = {
    "page_now": (
        "A human must be woken or paged immediately; this is or may be a "
        "real customer-impacting event."
    ),
    "page_business_hours": (
        "Route to the queue for next-business-hours handling; do not wake "
        "anyone."
    ),
    "suppress": (
        "Safe to drop: confirmed known noise with a clean historical record; "
        "no human needs to see it. Only choose with very high certainty."
    ),
    "cannot_determine": (
        "Not enough evidence to act; default to the existing pipeline — "
        "page as before, drop nothing."
    ),
}


class TestQuestions(unittest.TestCase):
    def setUp(self):
        self.q = build_questions()

    def test_three_questions_present(self):
        self.assertEqual(set(self.q), {"severity", "owning_team", "disposition"})

    def test_question_wire_shape(self):
        for qid, question in self.q.items():
            with self.subTest(qid=qid):
                self.assertEqual(question["type"], "choice")
                self.assertIsInstance(question["instructions"], str)
                self.assertTrue(question["instructions"].strip())
                self.assertIsInstance(question["criteria"], dict)

    def test_severity_options_match_spec_exactly(self):
        self.assertEqual(self.q["severity"]["criteria"], EXPECTED_SEVERITY)

    def test_default_team_options_match_spec_exactly(self):
        self.assertEqual(self.q["owning_team"]["criteria"], EXPECTED_TEAMS)

    def test_disposition_options_match_spec_exactly(self):
        self.assertEqual(self.q["disposition"]["criteria"], EXPECTED_DISPOSITION)

    def test_cannot_determine_mandatory_everywhere(self):
        for qid, question in self.q.items():
            self.assertIn("cannot_determine", question["criteria"], qid)

    def test_no_bare_labels_every_option_has_description(self):
        for qid, question in self.q.items():
            for option, desc in question["criteria"].items():
                with self.subTest(qid=qid, option=option):
                    self.assertIsInstance(desc, str)
                    self.assertTrue(desc.strip(), "bare label banned by design")

    def test_custom_team_options(self):
        custom = [("sre", "Site reliability on-call."), ("ml", "ML platform team.")]
        q = build_questions(team_options=custom)
        criteria = q["owning_team"]["criteria"]
        self.assertEqual(criteria["sre"], "Site reliability on-call.")
        self.assertEqual(criteria["ml"], "ML platform team.")
        self.assertIn("cannot_determine", criteria)
        self.assertNotIn("platform", criteria)  # overrides replace defaults

    def test_custom_team_options_capped_at_8(self):
        too_many = [(f"team{i}", f"Team {i}.") for i in range(9)]
        with self.assertRaises(ValueError):
            build_questions(team_options=too_many)

    def test_custom_team_options_reject_bare_labels(self):
        with self.assertRaises(ValueError):
            build_questions(team_options=[("sre", "")])

    def test_callers_cannot_mutate_module_state(self):
        q1 = build_questions()
        q1["severity"]["criteria"]["hacked"] = "nope"
        q2 = build_questions()
        self.assertNotIn("hacked", q2["severity"]["criteria"])


if __name__ == "__main__":
    unittest.main()
