"""Read model for the Sentinel platform tier (Forge's lane).

A pure READ projection over the engine's append-only event log. Design rules:

* Read-only by construction: every query opens the SQLite file with
  ``mode=ro`` (URI). The serving path can never write — not to the event
  log, not to the outbox, not anywhere. (Proven by
  tests/test_no_writes.py's write-attempt test.)
* No hot-path coupling: a FRESH connection per query; nothing is shared
  with the paging receiver's process. Dashboard stampedes cannot starve
  the pager — the pager is a different process entirely.
* Zero Jev calls: this module imports sentinel.eventlog (hash chain +
  flip detection) only. the Jev client module is never imported — not directly,
  not transitively. (Proven by the grep-guard test.)
* Honest reconstruction, labeled: the engine audit persists the full Q1
  probability map but only choice+confidence for Q2/Q3 (see
  src/sentinel/audit.py — v01_compat). The residual mass on Q2/Q3 is
  distributed uniformly and this is documented in README.md (engine gap
  E1). Timer-win rows (demo W1): when jev_model is null, no Jev answer
  exists on that path at all — the uniform bars in the prob_map are
  labeled with "reconstruction": True so the screen can show a dead
  state instead of silently rendering invented probabilities. The same holds for alert context (service/check/region/title):
  the engine does not persist it; the resolver below tries raw_payloads
  first and falls back to honest "unknown" markers (engine gap E2).

The store answers in contract-shaped dicts (openapi.yaml §components),
so app.py is a thin HTTP skin.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

from sentinel.eventlog import detect_flips

# ---------------------------------------------------------------- constants

TEAM_OPTIONS = ("platform", "network", "data", "product_backend",
                "security", "cannot_determine")
SEV_OPTIONS = ("p1_critical", "p2_high", "p3_medium", "p4_low",
               "known_noise", "cannot_determine")
DISP_OPTIONS = ("page_now", "page_business_hours", "suppress", "passthrough")

RANK_DECILES = 10               # equal-count rank deciles over ordinal Q1 p1_critical
MAX_REPLAY = 10_000            # SSE reconnect replay bound (gap policy)


# ------------------------------------------------------------------ helpers

def _clamp(value, options, default="cannot_determine"):
    return value if value in options else default


def _triple(choice, confidence, options, full_probs=None):
    """AnswerTriple. full_probs (the real Q1 map) wins; otherwise the
    recorded choice/confidence is kept and the residual mass is spread
    uniformly over the other options — documented reconstruction, not data."""
    confidence = float(confidence or 0.0)
    if full_probs:
        probs = {str(k): float(v) for k, v in full_probs.items()}
    else:
        others = [o for o in options if o != choice]
        rest = max(0.0, 1.0 - confidence)
        share = rest / len(others) if others else 0.0
        probs = {choice: confidence}
        probs.update({o: share for o in others})
    return {"choice": choice, "confidence": confidence, "probs": probs}


def _norm_ts(value: str) -> str:
    """Validate ISO-8601, normalize to fixed-width UTC for string compare."""
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _wilson(p: float, n: int, z: float = 1.96):
    """Wilson 95% interval (Oracle §7 — same formula as shadow_report)."""
    if n == 0:
        return 0.0, 0.0
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * (p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5 / denom
    return max(0.0, center - half), min(1.0, center + half)


def _rank_auc(items) -> float | None:
    """Concordance of ordinal p1 ranks vs SEV1/2 outcomes (Mann-Whitney U).

    Consumes order only: P(p1[pos] > p1[neg]) + 0.5·P(ties).
    0.5 = chance, 1.0 = perfect ranking. None when unscorable
    (fewer than 2 items, or no positives/negatives).
    """
    pos = [it["p1"] for it in items if it["sev12"]]
    neg = [it["p1"] for it in items if not it["sev12"]]
    if not pos or not neg:
        return None
    conc = tied = 0
    for p in pos:
        for q in neg:
            if p > q:
                conc += 1
            elif p == q:
                tied += 1
    return (conc + 0.5 * tied) / (len(pos) * len(neg))


def load_context_jsonl(path: str) -> dict[str, dict]:
    """Load a harness-provided alert-context map (engine gap E2).

    JSONL, one object per line:
      {"alert_id": "...", "title": "...", "service": "...",
       "check": "...", "severity_in": "...", "region": "...",
       "labels": {...}, "metric_value": 1.2, ...}

    Written by the demo harness (it knows the alerts it fired); read by
    the platform tier. Missing file -> {} (the "unknown" fallback holds).
    Malformed lines are skipped, never fatal.
    """
    out: dict[str, dict] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                aid = obj.get("alert_id")
                if isinstance(aid, str):
                    out[aid] = obj
    except OSError:
        pass
    return out


# ------------------------------------------------------------------- store

class ReadStore:
    """Read-only projection of the event log. One instance per server.

    db_path: the engine's SQLite file (SENTINEL_DB). Opened per-query in
    read-only mode; the file may not exist yet (engine not started) — the
    store then serves empty state and logs once.
    """

    def __init__(self, db_path: str, context: dict[str, dict] | None = None):
        self.db_path = os.path.abspath(db_path)
        self.context = context or {}
        self._warned = False
        self._lock = threading.Lock()

    # ------------------------------------------------------------ plumbing

    @property
    def available(self) -> bool:
        return os.path.exists(self.db_path)

    def _connect(self) -> sqlite3.Connection:
        # mode=ro: the OS + SQLite both refuse writes. WAL-mode databases
        # stay readable (the writer holds the WAL; we never take its lock).
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True,
                               check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=2000;")
        conn.execute("PRAGMA query_only=ON;")
        return conn

    def _query(self, sql: str, params=()):
        if not self.available:
            with self._lock:
                if not self._warned:
                    print(f"[platform] engine DB not present yet: "
                          f"{self.db_path} — serving empty state",
                          flush=True)
                    self._warned = True
            return []
        conn = self._connect()
        try:
            cur = conn.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
        except sqlite3.OperationalError as e:
            # A DB file exists but the engine schema isn't there yet
            # (engine not started / foreign file): serve empty state.
            if "no such table" in str(e):
                return []
            raise
        finally:
            conn.close()

    # ------------------------------------------------- decision projection

    def _context(self, body: dict, alert_id: str) -> dict:
        """Alert context for title/service/check/region (engine gap E2).

        Resolution order (first hit wins):
          1. raw_payloads — engine-persisted (populated when the engine
             wires I3 + store_raw_payload); the system of record.
          2. The harness-provided context file (--context-json), keyed by
             alert_id — the demo harness knows the alerts it fired.
          3. Honest "unknown" markers — never invented service names.
        """
        sha = body.get("input_sha256")
        if sha:
            rows = self._query(
                "SELECT payload FROM raw_payloads WHERE input_sha256 = ?",
                (sha,))
            if rows:
                try:
                    payload = json.loads(rows[0]["payload"])
                    return {
                        "title": payload.get("title") or alert_id,
                        "service": payload.get("service") or "unknown",
                        "check": payload.get("check") or "unknown",
                        "region": (payload.get("labels") or {}).get("region")
                                  or "unknown",
                        "severity_in": payload.get("severity_in") or "unknown",
                        "labels": payload.get("labels") or {},
                        "metric_value": payload.get("metric_value"),
                        "metric_threshold": payload.get("metric_threshold"),
                        "breach_seconds": payload.get("breach_duration_s"),
                    }
                except (ValueError, AttributeError):
                    pass
        ctx = self.context.get(alert_id)
        if ctx:
            labels = ctx.get("labels") or {}
            return {
                "title": ctx.get("title") or alert_id,
                "service": ctx.get("service") or "unknown",
                "check": ctx.get("check") or "unknown",
                "region": ctx.get("region") or labels.get("region")
                          or "unknown",
                "severity_in": ctx.get("severity_in") or "unknown",
                "labels": labels,
                "metric_value": ctx.get("metric_value"),
                "metric_threshold": ctx.get("metric_threshold"),
                "breach_seconds": ctx.get("breach_seconds"),
            }
        return {
            "title": alert_id, "service": "unknown", "check": "unknown",
            "region": "unknown", "severity_in": "unknown", "labels": {},
            "metric_value": None, "metric_threshold": None,
            "breach_seconds": None,
        }

    def project(self, row: dict) -> dict:
        """One decisions-VIEW row -> contract DecisionSummary (real state)."""
        body = json.loads(row["body"])
        compat = body.get("v01_compat") or {}
        alert_id = row["alert_id"]
        ctx = self._context(body, alert_id)

        disposition = body.get("disposition") or "passthrough"
        disposition = _clamp(disposition, DISP_OPTIONS, "passthrough")
        reason = (compat.get("reason")
                  or self._reason_fallback(body, disposition))

        q1_probs = None
        if compat.get("q1_probs"):
            try:
                q1_probs = json.loads(compat["q1_probs"])
            except ValueError:
                q1_probs = None
        severity = _clamp(compat.get("q1_choice"), SEV_OPTIONS)
        team = _clamp(body.get("q2_team"), TEAM_OPTIONS)
        q3_choice = body.get("q3_disposition") or (
            "passthrough" if disposition == "passthrough"
            else "cannot_determine")
        confidence = body.get("q3_confidence")
        confidence = float(confidence) if confidence is not None else 0.0
        jev_model = body.get("jev_model")

        prob_map = {
            "severity": _triple(severity,
                                compat.get("q1_confidence"), SEV_OPTIONS,
                                q1_probs),
            "owning_team": _triple(team, confidence, TEAM_OPTIONS),
            "disposition": _triple(_clamp(q3_choice, DISP_OPTIONS,
                                         "passthrough"),
                                  confidence, DISP_OPTIONS),
        }
        if jev_model is None:
            # Demo W1: the timer won (or a deterministic pre-Jev path) — no
            # Jev answer exists, so the bars above are a uniform-spread
            # reconstruction, never measured data. The contract (frozen)
            # forbids prob_map: null, so the reconstruction is labeled
            # in-band instead. UI ignores the extra key; it renders the
            # drawer dead state from the missing triples / nulls, not
            # from invented bars.
            prob_map["reconstruction"] = True

        summary = {
            "id": row["id"],
            "alert_id": alert_id,
            "time": row["received_at"],
            "title": ctx["title"],
            "service": ctx["service"],
            "severity": severity,
            "team": team,
            "disposition": disposition,
            "confidence": confidence,
            "reason": reason,
            "prob_map": prob_map,
            "fingerprint": row["fingerprint"],
            "input_sha256": body.get("input_sha256"),
            "jev_model": jev_model,
            "latency_ms": body.get("latency_ms"),
            # The race's authoritative verdict (audit P1/C2, RFC
            # aiml-winner-heuristic): the console derives judge-vs-timer
            # winner from this, never from jev_model presence (which
            # mislabels error_passthrough as a judge win).
            "budget_outcome": body.get("budget_outcome"),
            # C3: shadow is a MODE, never a reason. mode rides top-level in
            # the decision_made body (and as a VIEW column); the
            # reason == "shadow" fallback covers pre-C3 rows only.
            "mode": body.get("mode") or row.get("mode") or "live",
            "shadow": (body.get("mode") or row.get("mode")) == "shadow"
                      or reason == "shadow",
        }
        summary["_ctx"] = ctx  # internal; stripped before serving
        return summary

    @staticmethod
    def _reason_fallback(body: dict, disposition: str) -> str:
        budget = body.get("budget_outcome") or ""
        if budget.startswith("error") or "error" in budget:
            return f"error:{budget}"
        if budget == "reaper_redrive":
            return "error:reaper_redrive"
        if disposition == "passthrough":
            return "uncertain-default"
        return "unknown"

    # ------------------------------------------------------- river / search

    _RIVER_SQL = ("SELECT * FROM decisions WHERE type = 'decision_made'")

    def decisions(self, *, limit=50, since_id=0, fingerprint=None,
                  team=None, action=None, since=None, until=None):
        """Newest-first page of decision_made events (river or explorer)."""
        where = ["type = 'decision_made'", "id > ?"]
        params: list = [since_id]
        if fingerprint:
            where.append("fingerprint = ?")
            params.append(fingerprint)
        if team:
            where.append("json_extract(body, '$.q2_team') = ?")
            params.append(team)
        if action:
            where.append("json_extract(body, '$.disposition') = ?")
            params.append(action)
        if since:
            where.append("received_at >= ?")
            params.append(_norm_ts(since))
        if until:
            where.append("received_at <= ?")
            params.append(_norm_ts(until))
        params.append(limit)
        rows = self._query(
            f"SELECT * FROM decisions WHERE {' AND '.join(where)} "
            f"ORDER BY id DESC LIMIT ?", params)
        items = [self.project(r) for r in rows]
        for it in items:
            it.pop("_ctx", None)
        return items

    def has_older(self, min_id: int) -> bool:
        rows = self._query(
            "SELECT 1 FROM decisions WHERE type = 'decision_made' "
            "AND id < ? LIMIT 1", (min_id,))
        return bool(rows)

    def decision(self, decision_id: int) -> dict | None:
        """Full DecisionDetail (contract §explorer) or None."""
        rows = self._query(
            "SELECT * FROM decisions WHERE type = 'decision_made' "
            "AND id = ?", (decision_id,))
        if not rows:
            return None
        summary = self.project(rows[0])
        ctx = summary.pop("_ctx")
        outcome = self._query(
            "SELECT became_sev12, auto_cleared, mttr_min, labeled_at "
            "FROM outcomes WHERE alert_id = ?", (summary["alert_id"],))
        out = None
        if outcome:
            o = outcome[0]
            out = {
                "became_sev12": bool(o["became_sev12"])
                                if o["became_sev12"] is not None else None,
                "auto_cleared": bool(o["auto_cleared"])
                                if o["auto_cleared"] is not None else None,
                "mttr_min": o["mttr_min"],
                "labeled_at": o["labeled_at"],
            }
        summary["alert"] = {
            "check": ctx["check"],
            "severity_in": ctx["severity_in"],
            "region": ctx["region"],
            "labels": ctx["labels"],
            "metric_value": ctx["metric_value"],
            "metric_threshold": ctx["metric_threshold"],
            "breach_seconds": ctx["breach_seconds"],
        }
        summary["outcome"] = out
        summary["audit"] = {
            "received_at": rows[0]["received_at"],
            "created_at": rows[0]["created_at"],
        }
        return summary

    # ------------------------------------------------------------- SSE tail

    def tail(self, since_id: int, limit: int = 500) -> list[dict]:
        """Oldest-first rows after a cursor (SSE replay + live poll)."""
        rows = self._query(
            "SELECT * FROM decisions WHERE type = 'decision_made' "
            "AND id > ? ORDER BY id ASC LIMIT ?", (since_id, limit))
        items = [self.project(r) for r in rows]
        for it in items:
            it.pop("_ctx", None)
        return items

    def head_id(self) -> int:
        rows = self._query(
            "SELECT MAX(id) AS m FROM decisions "
            "WHERE type = 'decision_made'")
        return int(rows[0]["m"] or 0) if rows else 0

    # -------------------------------------------------------- calibration

    def labeled(self):
        """decision_made ⨝ outcomes — every labeled decision (denominator)."""
        return self._query(
            "SELECT d.* FROM decisions d JOIN outcomes o "
            "ON o.alert_id = d.alert_id "
            "WHERE d.type = 'decision_made' AND o.became_sev12 IS NOT NULL "
            "ORDER BY d.id")

    def calibration(self, team: str | None = None,
                    dataset_version: str = "labels-v3") -> dict:
        rows = self.labeled()
        if team:
            filtered = []
            for r in rows:
                try:
                    row_team = _clamp(json.loads(r["body"]).get("q2_team"),
                                      TEAM_OPTIONS)
                except ValueError:
                    continue
                if row_team == team:
                    filtered.append(r)
            rows = filtered
        # outcomes join values (became_sev12) per alert
        out_map = {o["alert_id"]: o["became_sev12"] for o in self._query(
            "SELECT alert_id, became_sev12 FROM outcomes "
            "WHERE became_sev12 IS NOT NULL")}
        items = []
        for r in rows:
            body = json.loads(r["body"])
            compat = body.get("v01_compat") or {}
            q1 = None
            if compat.get("q1_probs"):
                try:
                    q1 = json.loads(compat["q1_probs"])
                except ValueError:
                    q1 = None
            items.append({
                "p1": float((q1 or {}).get("p1_critical", 0.0)) if q1 else None,
                "conf": body.get("q3_confidence"),
                "sev12": bool(out_map.get(r["alert_id"])),
            })

        n_labeled = len(items)
        binnable = [it for it in items if it["p1"] is not None]
        # Ordinality law (AC-8c): p1 is an ORDINAL severity score in [0,1],
        # never a probability. We therefore measure only RANK-ORDER fidelity:
        # do higher p1 ranks correspond to more SEV1/2 outcomes? AUC over
        # (p1 rank, sev12) is the honest instrument — it consumes order only.
        # Equal-count rank deciles replace equal-width probability bins; the
        # observed rates are descriptive outcome fractions, never predictions.
        ranked = sorted(binnable, key=lambda it: it["p1"])
        n_b = len(ranked)
        deciles = []
        for d in range(RANK_DECILES):
            start = d * n_b // RANK_DECILES
            end = (d + 1) * n_b // RANK_DECILES
            members = ranked[start:end]
            n = len(members)
            lo_pct = round(start / n_b, 4) if n_b else 0.0
            hi_pct = round(end / n_b, 4) if n_b else 0.0
            if n == 0:
                deciles.append({"decile": d + 1, "rank_lo": lo_pct,
                                "rank_hi": hi_pct, "n": n,
                                "observed_sev12_rate": 0.0,
                                "ci95_lo": 0.0, "ci95_hi": 0.0})
                continue
            obs = sum(1 for m in members if m["sev12"]) / n
            ci_lo, ci_hi = _wilson(obs, n)
            deciles.append({"decile": d + 1,
                            "rank_lo": lo_pct, "rank_hi": hi_pct,
                            "n": n,
                            "observed_sev12_rate": round(obs, 4),
                            "ci95_lo": round(ci_lo, 4),
                            "ci95_hi": round(ci_hi, 4)})
        auc = _rank_auc(ranked)
        auc_ci = self._auc_ci(ranked)

        def coverage(tau):
            if n_labeled == 0:
                return 0.0
            return round(sum(1 for it in items
                             if it["conf"] is not None
                             and it["conf"] >= tau) / n_labeled, 4)

        flips = self.flip_stats()
        return {
            "team": team or "all",
            "window": "7d",
            "dataset_version": dataset_version,
            "n_decisions": len(rows),
            "n_labeled": n_labeled,
            "rank_fidelity": {
                "auc": round(auc, 4) if auc is not None else None,
                "auc_ci95": auc_ci,
                "n": n_b,
            },
            "deciles": deciles,
            "coverage": {"0.7": coverage(0.7), "0.8": coverage(0.8),
                         "0.9": coverage(0.9)},
            "flip_rate": flips["flip_rate"],
            "flips_n": flips["flips_n"],
            "interpretation": {
                "p1_semantics": "ordinal",
                "statement": (
                    "p1 is an ORDINAL severity score in [0,1], never a "
                    "probability. auc measures rank-order agreement between "
                    "p1 ranks and SEV1/2 outcomes only (0.5 = chance, "
                    "1.0 = perfect ranking). deciles are equal-count rank "
                    "groups, not probability bins; observed_sev12_rate is a "
                    "descriptive outcome fraction, not a predicted "
                    "probability. Nothing here says P(SEV | p1 = x)."
                ),
            },
        }

    def _auc_ci(self, ranked, resamples: int = 200):
        """Deterministic bootstrap CI for rank AUC (seeded — replay-stable)."""
        if _rank_auc(ranked) is None:
            return [None, None]
        import random
        rng = random.Random(20261004)
        n = len(ranked)
        vals = []
        for _ in range(resamples):
            sample = [ranked[rng.randrange(n)] for _ in range(n)]
            a = _rank_auc(sample)
            vals.append(a if a is not None else 0.5)
        vals.sort()
        lo = vals[int(0.025 * resamples)]
        hi = vals[min(int(0.975 * resamples), resamples - 1)]
        return [round(lo, 4), round(hi, 4)]

    # -------------------------------------------------------------- flips

    def flip_stats(self) -> dict:
        """Repeatability over decision_made bodies (engine's detect_flips)."""
        rows = self._query(
            "SELECT seq, body FROM events WHERE type = 'decision_made' "
            "ORDER BY seq")
        events = [{"seq": r["seq"], "type": "decision_made",
                   "body": r["body"]} for r in rows]
        by_input: dict[str, list] = {}
        for e in events:
            try:
                key = json.loads(e["body"]).get("input_sha256")
            except ValueError:
                continue
            if key:
                by_input.setdefault(key, []).append(e)
        repeats = {k: v for k, v in by_input.items() if len(v) > 1}
        flipped_inputs = set()
        for f in detect_flips(events):
            flipped_inputs.add(f["input_sha256"])
        n_repeats = len(repeats)
        return {
            "repeats_total": n_repeats,
            "flips_n": len(flipped_inputs),
            "flip_rate": round(len(flipped_inputs) / n_repeats, 4)
                         if n_repeats else 0.0,
        }

    def flip_records(self, window_s: int) -> list[dict]:
        """Flip-audit records (the honesty panel) — all repeats, flips first."""
        cutoff = datetime.now(timezone.utc).timestamp() - window_s
        rows = self._query(
            "SELECT * FROM decisions WHERE type = 'decision_made' "
            "ORDER BY id")
        by_input: dict[str, list[dict]] = {}
        for r in rows:
            try:
                body = json.loads(r["body"])
            except ValueError:
                continue
            key = body.get("input_sha256")
            if not key:
                continue
            try:
                ts = datetime.fromisoformat(
                    r["received_at"].replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
            if ts < cutoff:
                continue
            by_input.setdefault(key, []).append((r, body))
        repeats = {k: v for k, v in by_input.items() if len(v) > 1}
        events = [{"seq": r["id"], "type": "decision_made",
                   "body": r["body"]} for r, _ in
                  [p for v in repeats.values() for p in v]]
        flipped_inputs = {f["input_sha256"] for f in detect_flips(events)}
        records = []
        for key, pairs in repeats.items():
            pairs.sort(key=lambda p: p[0]["id"])
            first, last = pairs[0][0], pairs[-1][0]
            decs = []
            for r, body in pairs:
                decs.append({
                    "decision_id": r["id"],
                    "time": r["received_at"],
                    "disposition": _clamp(body.get("disposition"),
                                         DISP_OPTIONS, "passthrough"),
                    "confidence": (float(body["q3_confidence"])
                                   if body.get("q3_confidence") is not None
                                   else 0.0),
                    "q3_choice": body.get("q3_disposition"),
                })
            records.append({
                "input_sha256": key,
                "fingerprint": first["fingerprint"],
                "alert_id": first["alert_id"],
                "repeats": len(pairs),
                "flipped": key in flipped_inputs,
                "first_seen": first["received_at"],
                "last_seen": last["received_at"],
                "decisions": decs,
            })
        records.sort(key=lambda r: (not r["flipped"], r["last_seen"]),
                     reverse=False)
        return records

    # -------------------------------------------------------------- noise

    def noise(self, window_s: int) -> dict:
        rows = self._query(
            "SELECT * FROM decisions WHERE type = 'decision_made' "
            "ORDER BY id")
        cutoff = datetime.now(timezone.utc).timestamp() - window_s
        in_window = []
        for r in rows:
            try:
                ts = datetime.fromisoformat(
                    r["received_at"].replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
            if ts >= cutoff:
                in_window.append(r)
        nights = max(window_s / 86400.0, 1 / 1440)  # >= 1 minute
        checks: dict[str, dict] = {}
        teams: dict[str, dict] = {}
        reasons: dict[str, int] = {}
        for r in in_window:
            body = json.loads(r["body"])
            disp = _clamp(body.get("disposition"), DISP_OPTIONS,
                          "passthrough")
            fp = r["fingerprint"]
            ctx = self._context(body, r["alert_id"])
            c = checks.setdefault(fp, {
                "fingerprint": fp, "service": ctx["service"],
                "check": ctx["check"], "region": ctx["region"],
                "volume": 0, "suppressed": 0})
            c["volume"] += 1
            if disp == "suppress":
                c["suppressed"] += 1
            team = _clamp(body.get("q2_team"), TEAM_OPTIONS)
            t = teams.setdefault(team, {"before": 0.0, "after": 0.0})
            # Before Sentinel the legacy stack paged everything that reached
            # a human; after, only page_now pages at night. Documented
            # estimate (README §analytics).
            if disp in ("page_now", "page_business_hours", "suppress"):
                t["before"] += 1
            if disp == "page_now":
                t["after"] += 1
            reason = (body.get("v01_compat") or {}).get("reason") \
                or self._reason_fallback(body, disp)
            reasons[reason] = reasons.get(reason, 0) + 1
        top_checks = sorted(checks.values(),
                            key=lambda c: (-c["volume"], c["fingerprint"]))
        for c in top_checks:
            c["suppress_rate"] = round(c["suppressed"] / c["volume"], 4) \
                if c["volume"] else 0.0
        team_load = []
        for team, t in sorted(teams.items()):
            before = round(t["before"] / nights, 2)
            after = round(t["after"] / nights, 2)
            delta = round(100.0 * (after - before) / before, 1) \
                if before else 0.0
            team_load.append({
                "team": team,
                "pages_per_night_before": before,
                "pages_per_night_after": after,
                "delta_pct": delta,
            })
        total_r = sum(reasons.values())
        breakdown = [{"reason": k, "count": v,
                      "pct": round(100.0 * v / total_r, 1) if total_r else 0.0}
                     for k, v in sorted(reasons.items(),
                                        key=lambda kv: -kv[1])]
        return {"top_checks": top_checks[:50], "team_load": team_load,
                "suppression_breakdown": breakdown}
