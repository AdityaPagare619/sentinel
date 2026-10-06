"""Shared fixtures for the platform-server tests (stdlib unittest)."""

import json
import os
import sqlite3
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVER = os.path.dirname(_HERE)          # platform/server
_REPO = os.path.dirname(os.path.dirname(_SERVER))
for _p in (_SERVER, _REPO, os.path.join(_REPO, "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _pkg  # noqa: F401  (documents the sentinel_platform alias)

from sentinel.audit import AuditLog
from sentinel.client import Answer
from sentinel.correlator import fingerprint_for
from sentinel.models import Alert, DecisionRecord, Disposition

SEV_PROBS = {
    "p1_critical": {"p1_critical": 0.90, "p2_high": 0.06, "p3_medium": 0.02,
                    "p4_low": 0.01, "known_noise": 0.005,
                    "cannot_determine": 0.005},
    "known_noise": {"p1_critical": 0.001, "p2_high": 0.004, "p3_medium": 0.02,
                    "p4_low": 0.04, "known_noise": 0.93,
                    "cannot_determine": 0.005},
    "p3_medium": {"p1_critical": 0.01, "p2_high": 0.08, "p3_medium": 0.80,
                  "p4_low": 0.07, "known_noise": 0.03,
                  "cannot_determine": 0.01},
}


def make_decision(audit, *, alert_id, service="web", check="http_5xx",
                  severity_in="critical", region="us-east", team="platform",
                  action="page_now", reason="p1p2-mass", sev="p1_critical",
                  confidence=0.9, disp_choice=None, jev_model="system-one",
                  input_sha256=None, latency_ms=800.0, mode="live"):
    fp = fingerprint_for(service, check, severity_in, region,
                       env="test", cluster="test")
    alert = Alert(alert_id=alert_id, received_at="2026-10-03T10:00:00+00:00",
                  fingerprint=fp, service=service, check=check,
                  severity_in=severity_in,
                  title=f"{check} {severity_in} on {service} ({region})",
                  source="pagerduty", labels={"region": region})
    probs = dict(SEV_PROBS[sev])
    q1 = Answer(qid="severity", qtype="choice", choice=sev, noul=None,
                probabilities=probs, confidence=max(probs.values()))
    q3 = Answer(qid="disposition", qtype="choice",
                choice=disp_choice or action, noul=None,
                probabilities={action: confidence}, confidence=confidence)
    q2 = Answer(qid="team", qtype="choice", choice=team, noul=None,
                probabilities={team: 0.95}, confidence=0.95)
    disp = Disposition(action=action, reason=reason, team=team,
                       confidence=confidence, latency_ms=latency_ms,
                       mode=mode)
    rec = DecisionRecord(alert=alert,
                         input_sha256=input_sha256 or f"sha-{alert_id}",
                         jev_model=jev_model, q_severity=q1, q_team=q2,
                         q_disposition=q3, disposition=disp)
    return audit.record(rec)


def make_store_db(n_per_team: int = 4):
    """Temp engine DB with decisions + outcomes. Returns (db_path, audit)."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    audit = AuditLog(tmp.name)
    teams = ["platform", "data", "network"]
    actions = [("suppress", "triple-lock", "known_noise", 0.96, "live"),
               ("page_now", "p1p2-mass", "p1_critical", 0.9, "live"),
               ("page_business_hours", "queue-policy", "p3_medium", 0.8,
                "live"),
               ("passthrough", "known_noise", "known_noise", 0.95,
                "shadow")]
    i = 0
    for team in teams:
        for action, reason, sev, conf, mode in actions[:n_per_team]:
            i += 1
            make_decision(audit, alert_id=f"alt-{i:04d}", team=team,
                          action=action, reason=reason, sev=sev,
                          confidence=conf, service=f"svc-{team}", mode=mode)
    # Outcomes for half the alerts (became_sev12 alternates).
    conn = sqlite3.connect(tmp.name)
    for j in range(1, i + 1, 2):
        conn.execute(
            "INSERT INTO outcomes (alert_id, fingerprint, became_sev12,"
            " auto_cleared, mttr_min, labeled_at)"
            " VALUES (?,?,?,?,?,?)",
            (f"alt-{j:04d}", f"fp{j:04d}", j % 4 == 1, 1, 12.5,
             "2026-10-03T11:00:00Z"))
    conn.commit()
    conn.close()
    audit.close()
    return tmp.name
