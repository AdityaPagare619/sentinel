"""Tests for state.py — state shaping + token budget (contract §3.4)."""

import json
import os
import sys
import unittest
import unittest.mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sentinel.models import Alert  # noqa: E402
from sentinel.state import (  # noqa: E402
    STATE_TOKEN_BUDGET,
    build_state,
    estimate_tokens,
    input_sha256,
)


def make_alert(**overrides):
    fields = dict(
        alert_id="a-1",
        received_at="2026-10-02T12:00:00+05:30",
        fingerprint="deadbeef12345678",
        service="checkout-api",
        check="http_5xx_rate",
        severity_in="critical",
        title="5xx spike on checkout-api",
        source="pagerduty",
        labels={"env": "prod", "region": "ap-south-1", "cluster": "c1", "owner": "x"},
        metric_value=12.5,
        metric_threshold=5.0,
        breach_duration_s=420,
        raw={"summary": "5xx spike"},
    )
    fields.update(overrides)
    return Alert(**fields)


def state_tokens(state):
    return estimate_tokens(
        json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    )


class TestEstimateTokens(unittest.TestCase):
    def test_chars_div_4(self):
        self.assertEqual(estimate_tokens("abcd"), 1)
        self.assertEqual(estimate_tokens("a" * 8), 2)
        self.assertEqual(estimate_tokens(""), 0)
        self.assertEqual(estimate_tokens("abc"), 0)  # floor


class TestInputSha256(unittest.TestCase):
    def test_stable(self):
        state = {"b": 1, "a": [1, 2]}
        self.assertEqual(input_sha256(state), input_sha256(state))

    def test_canonical_key_order(self):
        self.assertEqual(input_sha256({"a": 1, "b": 2}), input_sha256({"b": 2, "a": 1}))

    def test_changes_with_content(self):
        self.assertNotEqual(input_sha256({"a": 1}), input_sha256({"a": 2}))

    def test_hex_64(self):
        h = input_sha256({})
        self.assertEqual(len(h), 64)
        int(h, 16)  # valid hex


class TestBuildState(unittest.TestCase):
    def test_priority_fields_present(self):
        alert = make_alert()
        history = {"alerts_30d": 40, "paged": 3, "became_sev12": 1, "median_auto_clear_min": 12.0}
        context = {
            "recent_deploys": [{"sha": "abc"}, {"sha": "def"}, {"sha": "ghi"}, {"sha": "jkl"}],
            "sibling_alerts": [{"t": "x"}],
            "oncall": {"platform": {"primary": "ana", "tz": "IST"}},
            "time": {"holiday": True},
            "runbook_title": "checkout 5xx runbook",
            "annotations": "redis spikes on this cluster self-clear",
        }
        state = build_state(alert, history, context)

        self.assertEqual(state["title"], "5xx spike on checkout-api")
        self.assertEqual(state["source"], "pagerduty")
        self.assertEqual(state["check"], "http_5xx_rate")
        self.assertEqual(state["service"], "checkout-api")
        # labels: only env/region/cluster survive
        self.assertEqual(
            state["labels"], {"env": "prod", "region": "ap-south-1", "cluster": "c1"}
        )
        self.assertEqual(
            state["metric"],
            {"value": 12.5, "threshold": 5.0, "breach_duration_s": 420},
        )
        self.assertEqual(state["outcome_history"]["alerts_30d"], 40)
        self.assertEqual(state["outcome_history"]["became_sev12"], 1)
        # top 3 deploys only
        self.assertEqual(len(state["recent_deploys"]), 3)
        self.assertEqual(state["oncall"]["platform"]["primary"], "ana")
        self.assertEqual(state["time"]["dow"], "Friday")
        self.assertTrue(state["time"]["holiday"])
        self.assertEqual(state["runbook_title"], "checkout 5xx runbook")
        self.assertIn("self-clear", state["annotations"])
        self.assertLessEqual(state_tokens(state), STATE_TOKEN_BUDGET)

    def test_none_history_context_ok(self):
        state = build_state(make_alert(), None, None)
        self.assertEqual(state["title"], "5xx spike on checkout-api")
        self.assertLessEqual(state_tokens(state), STATE_TOKEN_BUDGET)

    def test_budget_enforced_truncates_lowest_priority_first(self):
        # annotations is priority #10 (lowest) and must be truncated before
        # any higher-priority field is touched.
        alert = make_alert()
        big_annotations = "noise note " * 5000  # ~55k chars
        context = {
            "recent_deploys": [{"sha": "abc"}],
            "runbook_title": "checkout 5xx runbook",
            "annotations": big_annotations,
        }
        state = build_state(alert, {}, context)

        self.assertLessEqual(state_tokens(state), STATE_TOKEN_BUDGET)
        # top-priority fields intact
        self.assertEqual(state["title"], "5xx spike on checkout-api")
        self.assertEqual(state["source"], "pagerduty")
        self.assertEqual(state["check"], "http_5xx_rate")
        # higher-priority fields untouched by truncation
        self.assertEqual(state["runbook_title"], "checkout 5xx runbook")
        self.assertEqual(state["recent_deploys"], [{"sha": "abc"}])
        # lowest-priority field was truncated, not dropped, and marked
        self.assertIn("annotations", state)
        self.assertLess(len(state["annotations"]), len(big_annotations))
        self.assertTrue(state["annotations"].endswith("\u2026"))

    def test_budget_drops_nonstring_fields_whole(self):
        # A huge non-string field cannot be truncated — it is dropped whole,
        # while protected top-priority fields survive.
        alert = make_alert()
        context = {
            "sibling_alerts": [{"title": "sib " * 2000} for _ in range(10)],
            "runbook_title": "checkout 5xx runbook",
        }
        state = build_state(alert, {}, context)

        self.assertLessEqual(state_tokens(state), STATE_TOKEN_BUDGET)
        self.assertNotIn("sibling_alerts", state)
        self.assertEqual(state["title"], "5xx spike on checkout-api")
        self.assertEqual(state["source"], "pagerduty")
        self.assertEqual(state["check"], "http_5xx_rate")

    def test_budget_walk_drops_in_priority_order(self):
        # With a tight budget, fields are dropped strictly lowest-priority
        # first (annotations → runbook_title → time → oncall → sibling_alerts
        # → …): a huge priority-6 field is dropped while the higher-priority
        # outcome_history (priority 4) survives. Budget is derived from the
        # real size so the scenario is deterministic.
        alert = make_alert()
        history = {"alerts_30d": 5}
        context = {"sibling_alerts": [{"x": "y" * 1200}]}  # ~300 tok, priority 6
        full_tok = state_tokens(build_state(alert, history, context))
        with unittest.mock.patch(
            "sentinel.state.STATE_TOKEN_BUDGET", full_tok - 40
        ):
            state = build_state(alert, history, context)
        self.assertLessEqual(state_tokens(state), full_tok - 40)
        self.assertIn("title", state)  # protected: never dropped
        self.assertIn("source", state)
        self.assertIn("check", state)
        self.assertNotIn("sibling_alerts", state)  # priority 6: dropped
        self.assertNotIn("time", state)  # priority 8: dropped (walked first)
        self.assertIn("outcome_history", state)  # priority 4: outranks 6, kept

    def test_last_resort_truncates_protected_fields_never_drops_them(self):
        # Budget below what title/source/check alone need: they are
        # truncated (hard cap wins) but never removed. The irreducible
        # floor is the three key names + markers (~13 tokens), so the
        # test uses 15.
        with unittest.mock.patch("sentinel.state.STATE_TOKEN_BUDGET", 15):
            state = build_state(make_alert(), {}, {})
        self.assertLessEqual(state_tokens(state), 15)
        for key in ("title", "source", "check"):
            self.assertIn(key, state, key)
        self.assertLess(len(state["title"]), len("5xx spike on checkout-api"))

    def test_protected_fields_never_removed(self):
        # Even a pathological alert keeps title/source/check.
        alert = make_alert(
            title="t " * 2000, source="s " * 2000, check="c " * 2000,
        )
        state = build_state(alert, {}, {"annotations": "n " * 20000})
        for key in ("title", "source", "check"):
            self.assertIn(key, state, key)
        self.assertLessEqual(state_tokens(state), STATE_TOKEN_BUDGET)


if __name__ == "__main__":
    unittest.main()
