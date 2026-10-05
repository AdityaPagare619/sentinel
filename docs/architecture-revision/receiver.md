# Phase-2 Domain Review — RECEIVER (ingest / auth / validation)

**Reviewer:** Phase-2 domain reviewer (fresh eyes; did not build this).
**Scope:** `src/sentinel/receiver.py` (entire, ~1300 lines), `src/sentinel/health.py`,
`tests/test_receiver.py`, `tests/test_signature_auth.py`.
**Code read at:** branch `program-arch-rev-receiver` (from `program/architecture-revision` @ b831d3c).
**Research cited:** `research/{swe-discipline,architecture-patterns,testing-at-scale,operating-without-vendor-apis}.md`,
`~/workspace/skills/{principal-systems,principal-governance,principal-mindset,execution-doctrine}/SKILL.md`.
**Posture:** brutal honesty, evidence-first. "It's fine" appears nowhere below without a mechanism behind it.

---

## 1. WHAT EXISTS

Grounded in the code — file paths, behaviors, line refs. Nothing from memory.

### 1.1 Ingress routes (`src/sentinel/receiver.py`, `SentinelHandler`)

| Route | Handler | Auth | Failure semantics |
|---|---|---|---|
| `POST /v2/enqueue` | `_handle_alert_post` → `Pipeline.handle_pd` | **NONE on triggers** — headers only passed through for resolve/ack claim verification (receiver.py:652) | fail-open: unparseable → forward raw bytes to PD, 200 |
| `POST /webhook/generic` | `_handle_alert_post` → `_signature_ok` → `Pipeline.handle_generic` | HMAC ADR-005, fail-closed (receiver.py:655, 803) | 403 on bad auth; fail-open on unparseable |
| `POST /episodes/resolve` | `_handle_episode_resolve` | Bearer `SENTINEL_HEALTH_TOKEN`, **required even when unset** — 401 otherwise (receiver.py:677–712) | idempotent no-op on unknown fp |
| `POST /-/reload` | `_handle_reload` | Bearer token, but **open when token unset** (receiver.py:714) | 422 on rejected config, live generation untouched |
| `GET /livez` | shallow | none (deliberate) | pid/uptime/version |
| `GET /healthz` | deep, 5 predicates | Bearer token, but **open when token unset** (receiver.py:629–643) | 503 + `{"ok": false, "failed": [...]}` |
| `POST /shadow/*` | `_do_shadow_post` | per-vendor secrets in shadow.py | 5xx + vendor retry, never paging fail-open |

### 1.2 Authentication (ADR-005, receiver.py:52–78, 101–137)

- Canonical scheme: `X-Sentinel-Timestamp: <unix seconds>` + `X-Sentinel-Signature: sha256=<hex>`,
  `hex = HMAC-SHA256(secret, b"<timestamp>.<raw body>")`, `|now − ts| ≤ 300s` (`SIGNATURE_MAX_SKEW_S`).
- The check is extracted as a **pure function** `_webhook_sig_failure_reason(secret, onboarding, headers, body)`
  (receiver.py:121–162), shared by the generic route and the PD resolve-claim path — one scheme, two call sites.
  This is genuinely good: a single canonical check, constant-time `hmac.compare_digest`, terse reason codes,
  no secret/signature values in logs.
- Production refuses (403) on: empty secret, absent/malformed signature, absent/malformed/stale/future-skewed
  timestamp, bad MAC, legacy timestamp-less signatures. **Startup refuses** (`SystemExit`) on empty secret
  (receiver.py:1176–1183) and on secrets shorter than 16 chars.
- Onboarding mode (`SENTINEL_WEBHOOK_ONBOARDING=1`) fails open loudly: CRITICAL boot warning, per-request WARNING,
  `webhook_auth_bypassed` metric, `webhook_auth_fail_open=true` on `/healthz`.
- The code **itself documents the residual replay hole**: "Tripwire's caveat stands: 5 minutes without nonces
  is a 5-minute replay window — idempotent ingest dedupes identical alerts but cannot tell a replay from a genuine
  resend inside the window" (receiver.py:113–117).

### 1.3 Payload validation and fail-open (`Pipeline.handle_pd` :237, `handle_generic` :420)

- `_normalize_pd` requires `routing_key`, `payload.summary/source/severity`; `_normalize_generic` requires `service`.
  Missing fields raise `Unparseable` → metric `unparseable` + forward original bytes to PD (never drop).
- Non-`trigger` PD actions (ack/resolve) are relayed **unchanged, untriaged**; a *signed* resolve/ack claim additionally
  closes the episode (`resolve_episode`, reason `verified_resolve`); unsigned claims are refused the close — the silence
  direction is fail-closed in every mode (receiver.py:267–284, 302–355).
- The receiver's never-5xx contract: the handler wraps everything; last resort forwards the original bytes and
  returns 200 (receiver.py:663–681).
- **Exception-path miswiring (verified):** `handle_pd`/`handle_generic` catch only `except Unparseable` (:266, :421),
  but `json.loads` raises `json.JSONDecodeError` and `body.decode("utf-8")` raises `UnicodeDecodeError` — neither is an
  `Unparseable`. Malformed JSON therefore bypasses the unparseable path and lands in the handler's `except Exception`
  panic path: it still fails open (200 + forward — `test_invalid_json_fails_open` passes), but it increments
  `handler_panics` instead of `unparseable`, and forwards as `alert_id="receiver-error"` instead of `dedup_key=_body_key(body)`.
  The suite cannot see this because no test asserts which metric increments. The code's *intent* and its *behavior* differ.

### 1.4 Admission control (receiver.py:628–661)

- `DEFAULT_MAX_INFLIGHT = 64` semaphore; overload → **503 + `Retry-After: 1`**, never 429 — the comment correctly notes
  Alertmanager drops on 429 and retries on 503, so this is release-blocking-correct.
- `MAX_BODY_BYTES = 8 MiB` → 413. No socket/read timeout is set anywhere in the module (grep: no `settimeout`).

### 1.5 Liveness / readiness (`src/sentinel/health.py`)

- `/livez`: shallow (alive, uptime, version, pid), unauthenticated by design.
- `/healthz`: deep — five named predicates (`config_current`, `gate_constructed` with a synthetic gate self-test,
  `forwarder_draining` error tripwire, `evidence_flowing` audit round-trip, `no_crashloop_signature` restart count),
  200-with-evidence or 503-with-`failed` list, plus the `webhook_auth_fail_open` mode flag. Startup self-test then
  every 5 minutes. Honest docstrings about what is *not* measured (outbox lag, WAL, canary).
- The honest-evidence style is strong. The auth posture is not (see §2.8).

### 1.6 Tests (`tests/test_receiver.py`, `tests/test_signature_auth.py`)

- 17 receiver tests + full auth matrix test: production fail-closed matrix (absent/empty secret, stale/future/malformed
  timestamp, bad MAC, legacy signature), onboarding matrix (unsigned accepted loudly, bad MAC still refused), startup gate
  tests (empty secret refuses, weak secret refuses even in onboarding). All run against a **real loopback ThreadingHTTPServer**
  plus a `CaptureServer` fake PD — HTTP-boundary testing, the right layer per `research/operating-without-vendor-apis.md` §5.
- The auth matrix is the best-tested part of the receiver. The gaps in §2 are what it does *not* cover.

---

## 2. WHAT'S MISSING

Each item names the enterprise standard it violates and cites the Phase-1 research. Severity is my judgment as a
fresh reviewer, stated plainly.

### M-1. The `/v2/enqueue` trigger path has NO authentication. (SEVERE)

**Fact:** `_handle_alert_post` calls `_signature_ok(body)` **only** for `/webhook/generic` (receiver.py:655).
The `/v2/enqueue` branch (receiver.py:652) calls `self.pipeline.handle_pd(body, headers=...)` with no auth check —
headers ride along solely so *resolve claims* can authenticate. Any host that can open a TCP connection to the receiver
can inject arbitrary `trigger` alerts with arbitrary severity, dedup keys, and summaries. The only defense is the default
`--bind 127.0.0.1` — a network-layer hope, not a trust boundary.

**Standard violated:** principal-systems law 2 (eternal friction — "architect for the hostile world, not the demo world");
principal-governance "unforgiving API design" / explicit trust boundaries. The two alert-ingest routes have **asymmetric
trust postures with no written rationale** — exactly the "implicit contracts" anti-pattern inventoried in
`research/architecture-patterns.md` §5 (checklist item 2: contracts declared per boundary, owned, versioned).
Worse, the asymmetry is invisible: the module docstring advertises the webhook-auth contract as if it covered the
receiver's ingress, but it covers one of two alert routes.

**Why it matters for this product:** Sentinel is page-or-suppress middleware. An unauthenticated trigger route lets an
attacker (or a misconfigured internal sender) inject `critical` pages at will — noise injection against a noise-reduction
product — or, via crafted dedup_keys, manipulate correlator state. The resolve direction is admirably fail-closed (D10);
the trigger direction is fail-open to *anyone*.

### M-2. No socket/read timeout — slowloris exhausts the only admission bound. (SEVERE)

**Fact:** `make_server` sets no timeout on the listening socket or per-connection sockets (grep finds no `settimeout`
in receiver.py). `_read_body` (receiver.py:746–762) does `self.rfile.read(length)` — a blocking read of up to
`MAX_BODY_BYTES` with no deadline. The single admission control is the 64-slot semaphore. An attacker or wedged sender
opens 64 connections and trickles bytes: all inflight slots are held **forever**, every legitimate sender gets 503
forever. Combined with M-1 (open route), this is a trivially executable, unauthenticated DoS on the alerting path.

**Standard violated:** principal-systems law 2 (eternal friction); law 4 (five whys: the 503 admission control *looks*
like DoS protection but protects only against *fast* senders — the root protection, bounded read time, is absent).
SEDA (`research/architecture-patterns.md` §3.2): queues give load conditioning; here the queue's bound is circumventable
by slowness.

### M-3. 8 MiB body cap vs the 512 KiB contract it mirrors. (MODERATE)

**Fact:** `MAX_BODY_BYTES = 8 * 1024 * 1024` (receiver.py:81). PagerDuty's documented Events API v2 limit is 512 KB
(`research/operating-without-vendor-apis.md` §4, PD rate-limits page). The receiver mirrors the PD ingress shape
(§4.3 of architecture-patterns calls this a sound Postel move) but accepts **16×** the vendor's own cap, synchronously,
per thread. This amplifies M-2 (memory pressure per held connection) and contradicts Postel's-law discipline
(principal-governance): know what you accept because you know what you send.

### M-4. No request/correlation ID — observe is not a stage. (MODERATE)

**Fact:** No ID is generated at ingress; `Pipeline.handle_pd`/`handle_generic` return only `{status, dedup_key}`.
`log_message` writes method+path to stderr with no status code, no latency, no correlation key. The `_pd_ok` response
echoes `dedup_key`, which is **absent** on many deliveries (falls back to a body hash). Tracing one alert through
receiver → correlator → gate → forwarder → event log is impossible by construction.

**Standard violated:** principal-governance law 3 ("one Trace-ID per user interaction, injected from the browser through
gateway, microservices, and database"); `research/architecture-patterns.md` §5 item 8 (checklist: "One trace/correlation
ID across every hop … with per-stage latency attribution" — scored **missing**); SRE constitution ("observability over
monitoring"). The receiver is the one place a correlation ID *can* be born. It doesn't.

### M-5. Metrics are a bare in-memory dict — no scrapeable telemetry. (MODERATE)

**Fact:** `self.metrics: dict[str, int]` (receiver.py:208–226). No labels (can't split by route/source), no
Prometheus exposition, counters reset on restart, surfaced only inside the `/healthz` body. There is no `/metrics` route.

**Standard violated:** principal-governance "unified telemetry"; SRE constitution (observability over monitoring).
`research/testing-at-scale.md` Q3: what gets measured gates releases — here nothing at the edge is measurable by an
external system. The health predicates measure *internal* state well; the *ingress* is a black box to the operator's
monitoring stack.

### M-6. Ingress contract is code, not data — no checked-in schema, no contract tests. (MODERATE)

**Fact:** The accepted shapes of both routes are defined only by `_normalize_pd`/`_normalize_generic` code and prose
docstrings. There is no JSON Schema artifact, no versioned contract file, no test that fails when a sender's payload
drifts. `research/testing-at-scale.md` Q5 per-layer checklist scores Sentinel's contract/schema layer **MISSING**;
`research/operating-without-vendor-apis.md` §5.1: "The vendor contract is data, versioned in the repo … with a test that
fails when the code drifts from the contract. (This is the strongest thing Sentinel already does [for PD_RETRY_TABLE])"
— the receiver's *inbound* contract is the un-done twin of that done work.

**Postel angle:** principal-governance demands "liberal in what you accept" — the receiver *claims* liberal ingest
(fail-open on unparseable), but unknown-field handling, extra-top-level-keys tolerance, and Unicode edge cases have no
tests proving the liberality. `test_invalid_json_fails_open` proves the 200, not the liberality.

### M-7. Replay window acknowledged, unmitigated — no nonce, no seen-signature cache. (MODERATE)

**Fact:** The code's own comment (receiver.py:113–117) admits a captured signed body replays for 5 minutes and "cannot
[tell] a replay from a genuine resend." For a suppress-the-page product the sharp edge is **re-page by replay**: a
captured `trigger` re-fires a page within the window, and the correlator's dedup treats it as a resend. Resolve-claim
replay is harmless (idempotent no-op), which is exactly why the trigger side is the one to fix.

**Standard violated:** principal-systems law 2; `research/architecture-patterns.md` §5 item 9 (idempotency keys on
critical writes — retries across boundaries must be safe; here a *replayed* write is indistinguishable from a retried one).

### M-8. Control-plane auth is inconsistent; no graceful drain. (LOW–MODERATE)

**Facts:** (a) `/episodes/resolve` requires the bearer token *even when unset* (fail-closed, threat-modeled — the
docstring cites SECURITY.md T1 false-silence). `/-/reload` and `/healthz` are **open when the token is unset**
(`_health_auth_ok`, receiver.py:848). Reload re-validates on-disk config — an unauthenticated caller can force live
threshold swaps (only from on-disk files, so severity is moderate, not severe), but the asymmetry has no written
rationale while the resolve asymmetry does. (b) Shutdown handles only `KeyboardInterrupt` (receiver.py:1289);
SIGTERM — what systemd/k8s actually send — kills in-flight triage mid-flight. For a "never drop a page" system there
is no drain: no in-flight counter surfaced, no `/drain` or SIGTERM handler.

**Standard violated:** principal-governance (unforgiving API design — control surfaces need the same rigor as data
surfaces); SRE constitution (graceful degradation).

### M-9. Malformed-JSON takes the panic path, not the unparseable path. (LOW, but symptomatic)

**Fact:** §1.3 above. `except Unparseable` never catches `json.JSONDecodeError`/`UnicodeDecodeError`; malformed bodies
increment `handler_panics` and forward as `alert_id="receiver-error"` instead of `unparseable` / body-hash dedup key.
The never-5xx contract holds (the outer catch saves it), but the *metrics lie about what happened* — and metrics are
the supervisor's only window into ingress health (M-5). principal-mindset proxy-trap audit: a metric that misclassifies
is a proxy that will mislead the next incident review.

---

## 3. CONCRETE REVISION PROPOSALS

Ranked by value (security × blast radius × reversibility). Each is Type-2 (reversible config/code change) except R-1,
which changes the ingress contract and is Type-1 for senders — flagged as such. No building in this phase; each item
names what would verify it.

### R-1. Authenticate `/v2/enqueue` with the same ADR-005 HMAC (Type-1 contract change)

**Rationale:** M-1 is the single most damning finding: the highest-value ingress route is unauthenticated while its
sibling route is HMAC-gated. The fix is mechanical — apply `_webhook_sig_failure_reason` in the `/v2/enqueue` branch
before `handle_pd` — reusing the existing pure function, so the two routes share one scheme (the D10 extraction already
paid for this). Migration path exists: `SENTINEL_WEBHOOK_ONBOARDING=1` accepts unsigned loudly during sender cutover;
the resolve-claim path already proves senders can carry the headers. The PD-mirror shape is preserved; only the trust
posture changes. This is the "gateway got too smart vs. gateway got too trusting" correction from
`research/architecture-patterns.md` §2.4.

**Verify:** extend `tests/test_signature_auth.py` with a `/v2/enqueue` matrix mirroring the generic matrix
(absent/bad/stale/future/malformed signature → 403, nothing forwarded; valid → 200); a contract doc stating the
ingress auth scheme per route; onboarding-mode acceptance test for the cutover window.

### R-2. Slowloris hardening: socket timeouts + 512 KiB cap on `/v2/enqueue` + chunked handling

**Rationale:** M-2/M-3. The 503 admission control is defeated by slowness; the bound that matters is *time held per
connection*, not just *count*. Set `server.socket.settimeout` / per-connection timeout (e.g. 10s read deadline),
reduce `MAX_BODY_BYTES` for the PD-mirror route to 512 KiB (the contract it mirrors — `research/operating-without-vendor-apis.md` §4),
keep 8 MiB only where a documented sender needs it, and either read chunked bodies or reject with 411/501 explicitly
instead of silently treating them as empty→unparseable (today a chunked sender becomes invisible passthrough).

**Verify:** a slow-drip test (64 connections trickling 1 byte/2s) — legit requests still get 200s, tricklers get
timeouts; a 600 KiB PD body gets 413 on `/v2/enqueue`; a chunked-encoded body is either parsed or explicitly rejected,
never silently empty.

### R-3. Ingress request ID + Prometheus `/metrics` + structured access log

**Rationale:** M-4/M-5. Generate a request ID at ingress (`X-Request-ID` honored if present, generated otherwise),
thread it through `Pipeline.handle_*` into audit events and the `_pd_ok`/generic responses, and emit a `/metrics`
endpoint in Prometheus text format with labeled counters (`route`, `disposition`, `auth_outcome`) — replacing the
unlabeled in-memory dict as the external surface. Structured access log: ID, route, status, latency_ms, auth reason.
This is the principal-governance unified-telemetry law and the architecture-patterns §5 item 8, implemented at the one
place it can originate.

**Verify:** end-to-end test: one POST → the same ID appears in stderr access log, the response body, and the audit event;
`GET /metrics` scrapes with correct labels; a dashboard-less `curl` check in CI.

### R-4. Per-sender rate limiting ahead of the global semaphore

**Rationale:** M-1/M-2's fairness twin. The 64-slot global semaphore is one pool with no fairness: one aggressive sender
starves all others. A token-bucket per source IP (and per routing_key on the PD route) *before* the semaphore, shedding
with 503 + `Retry-After` (the already-correct overload code), gives per-tenant bulkheads — the Envoy lesson in
`research/architecture-patterns.md` §2.1 ("the middleware never trusts its own dependencies" — here the dependency is
the sender).

**Verify:** flood test — sender A at 10× the bucket rate gets 503s while sender B's requests still 200; bucket params
from env with sane defaults; metric `rate_limited_total{route, key}`.

### R-5. Bounded seen-signature replay cache (nonce-less replay mitigation)

**Rationale:** M-7. Without nonces, the 300s replay window is structural; a bounded in-memory set of seen
`(timestamp, signature)` pairs (cap ~tens of thousands, TTL = skew window + margin) turns replays into 403/409s at
ingress, before triage. The code already names the alternative (nonces) and the cost; this is the cheap deterministic
guardrail — principal-mindset §1.4 ("inference-time/deterministic guardrail … for free").

**Verify:** sign a body, POST twice — first 200, second refused with a distinct reason code + metric
`replay_rejected_total`; cache eviction test (old entries expire, memory bounded under flood).

### R-6. Checked-in ingress schemas + Postel liberality suite

**Rationale:** M-6. Write the two accepted shapes as versioned JSON Schema files (`contracts/ingress/pd-trigger.v1.json`,
`contracts/ingress/generic-webhook.v1.json`), validate at ingress *before* normalization, and add a liberality test
suite: unknown top-level fields ignored, extra custom_details pass through, Unicode summaries accepted, wrong types on
optional fields degrade gracefully. This is the inbound twin of the `PD_RETRY_TABLE` contract discipline
(`research/operating-without-vendor-apis.md` §5.1) and the testing-at-scale Q5 contract layer, currently scored missing.

**Verify:** schema files in-repo; a test asserting code accepts exactly the schema's required set and tolerates the
schema's documented extras; CI job that fails when `_normalize_*` code and schema disagree (generate one from the other
or cross-check).

### R-7. Control-plane consistency + graceful drain

**Rationale:** M-8. Require the bearer token for `/-/reload` even when unset (same fail-closed reasoning as
`/episodes/resolve`, documented), and handle SIGTERM: stop accepting, let in-flight triage finish within a bounded
drain window, then exit. The never-drop-a-page promise currently ends at the process boundary.

**Verify:** reload without token → 401 when token set, 401-with-`not configured` when unset; SIGTERM test: in-flight
request completes, new connections refused during drain.

### R-8. Fix the JSON-decode exception path (correctness nit with metric integrity)

**Rationale:** M-9. Catch `(Unparseable, ValueError)` — `json.JSONDecodeError` and `UnicodeDecodeError` both subclass
`ValueError` — in `handle_pd`/`handle_generic` so malformed bodies take the documented unparseable path (metric
`unparseable`, `dedup_key=_body_key(body)`). Two-line change; the value is metric honesty, which the next incident
review will depend on.

**Verify:** malformed JSON → `unparseable` increments, `handler_panics` does not; `test_invalid_json_fails_open`
extended to assert the metric.

---

## Appendix: what the receiver gets right (for the record)

The review is adversarial by brief, but the record should show the strengths the revision must not regress:
the canonical pure-function signature check shared by two call sites; the onboarding mode's loudness (metric + health
flag + CRITICAL warning, not a quiet env var); the resolve direction's fail-closed design with idempotent no-ops;
the never-5xx contract with last-resort raw forwarding; the 503-not-429 Alertmanager-correct overload code; the honest
`/healthz` (predicates with evidence, "not yet measured" stated in prose); and the best-tested auth matrix in the repo.
The revision program's job is to raise the *rest* of the receiver to the standard its auth code already sets.

## Appendix: alternatives considered and rejected

- **mTLS for sender auth instead of HMAC on /v2/enqueue (R-1):** stronger, but stdlib-only TLS client-cert handling
  plus operator cert management is a large UX cost for a bring-your-own-sender product; HMAC reuses the existing,
  tested ADR-005 scheme. Revisit if the threat model grows to mutual distrust between Sentinel and senders.
- **Full SEDA queues between receiver and gate:** `research/architecture-patterns.md` §1.3's sharpest finding recommends
  the concept at the receiver↔gate boundary, but that is the correlator/gate domain's decision, not the receiver's —
  this memo scopes to what the receiver owns (admission, auth, validation, observability at the edge).
- **Nonces (jti) to close the replay window fully (R-5):** the complete fix, but requires sender cooperation (stateful
  senders); the seen-signature cache closes the window for the stateless-sender case with zero sender changes. Do the
  cache now, nonces as a v1.1 contract option.
