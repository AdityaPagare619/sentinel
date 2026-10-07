"""Sentinel platform tier — read API + SSE + static UI (Forge's lane).

Run (from the repo root):
    python3 platform/server/__main__.py [--port 8080] [--db ./sentinel.db]
        [--state-dir ./sentinel-state] [--ui platform/ui]
        [--data-source shadow] [--labels-version labels-v3]
        [--cors-origins https://adityapagare619.github.io]
        [--token-file ./sentinel-state/operator_token.json]

(Note: `python -m platform.server` cannot work — the repo's `platform/`
dir shadows stdlib `platform`; _pkg.py loads us as `sentinel_platform`.)

SEPARATE process, SEPARATE port from the paging receiver. The receiver
must run on a different port (its default is also 8080 — pass it
--port explicitly). Dashboard load can never starve the pager: process
isolation first, the shed.py bulkhead second.

Read-only: the engine DB is opened mode=ro. Zero Jev calls on every
read path (the server never imports the Jev client module).
"""

from __future__ import annotations

import argparse
import os
import sys

# --- path bootstrap -----------------------------------------------------
# The repo's `platform/` dir shadows stdlib `platform`, so `import
# platform.server` can never work. _pkg loads this package under the
# alias `sentinel_platform` (proper package semantics; relative imports
# keep working). `src/` is added for `sentinel.*` (event log, tuner).
_HERE = os.path.dirname(os.path.abspath(__file__))          # platform/server
_REPO = os.path.dirname(os.path.dirname(_HERE))             # repo root
_SRC = os.path.join(_REPO, "src")
for _p in (_HERE, _SRC):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _pkg  # noqa: E402

app_mod = _pkg.load("app")           # noqa: E402
auth_mod = _pkg.load("auth")         # noqa: E402
datasets_mod = _pkg.load("datasets")  # noqa: E402
shed_mod = _pkg.load("shed")         # noqa: E402
store_mod = _pkg.load("store")       # noqa: E402
keystore_mod = _pkg.load("keystore")  # noqa: E402

PlatformApp = app_mod.PlatformApp
OperatorTokenStore = auth_mod.OperatorTokenStore
DatasetRegistry = datasets_mod.DatasetRegistry
AdmissionGate = shed_mod.AdmissionGate
DegradePolicy = shed_mod.DegradePolicy
ReadStore = store_mod.ReadStore
DEFAULT_MAX_INFLIGHT = shed_mod.DEFAULT_MAX_INFLIGHT
DEFAULT_SHED_LOAD = shed_mod.DEFAULT_SHED_LOAD


def build_app(args) -> PlatformApp:
    state_platform = os.path.join(args.state_dir, "platform")
    ctx_path = args.context_json or os.path.join(state_platform,
                                                 "context.jsonl")
    context = store_mod.load_context_jsonl(ctx_path)
    if context:
        print(f"[platform] alert context: {len(context)} alerts from "
              f"{ctx_path}", flush=True)
    store = ReadStore(args.db, context=context)
    registry = DatasetRegistry(os.path.join(state_platform, "datasets"))
    gate = AdmissionGate(max_inflight=args.max_inflight)
    degrade = DegradePolicy(shed_load=args.shed_load)
    ui_dir = args.ui
    if ui_dir and not os.path.isabs(ui_dir):
        ui_dir = os.path.join(_REPO, ui_dir)
    token_file = (args.token_file
                  or os.environ.get("SENTINEL_OPERATOR_TOKEN_FILE")
                  or os.path.join(args.state_dir, "operator_token.json"))
    operator_tokens = OperatorTokenStore(token_file)
    # C3 (Track 3): the kill switch behind /api/v1/safety/*. The platform
    # tier opens the engine DB read-only, so this KillSwitch carries no
    # audit log of its own here — the flip is shared with the engine /
    # forwarder process through the state file (0600, atomic writes), and
    # the forwarder audits its own forwarder_halted observation. Drill and
    # single-process topologies wire a log-backed KillSwitch directly.
    from sentinel import safety as _safety
    kill_switch = _safety.KillSwitch(
        state_path=os.path.join(args.state_dir, "kill-switch.json"))
    drill_dir = os.path.join(_REPO, "ops", "drills")
    return PlatformApp(store=store, registry=registry, gate=gate,
                       degrade=degrade, data_source=args.data_source,
                       labels_version=args.labels_version, ui_dir=ui_dir,
                       cors_origins=args.cors_origins,
                       operator_token_store=operator_tokens,
                       kill_switch=kill_switch, drill_dir=drill_dir)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--db", default=os.environ.get("SENTINEL_DB",
                                                   "./sentinel.db"))
    ap.add_argument("--state-dir",
                    default=keystore_mod.default_state_dir())
    ap.add_argument("--ui", default="platform/ui",
                    help="static UI dir served at / (Prism's files)")
    ap.add_argument("--data-source", default=os.environ.get(
        "SENTINEL_DATA_SOURCE", "shadow"),
        choices=["synthetic", "shadow", "production"])
    ap.add_argument("--labels-version", default="labels-v3")
    ap.add_argument("--context-json", default=None,
                    help="harness-provided alert-context JSONL "
                         "(default: <state-dir>/platform/context.jsonl "
                         "if present)")
    ap.add_argument("--max-inflight", type=int,
                    default=DEFAULT_MAX_INFLIGHT)
    ap.add_argument("--shed-load", type=float, default=DEFAULT_SHED_LOAD)
    ap.add_argument("--cors-origins",
                    default=os.environ.get("SENTINEL_CORS_ORIGINS",
                                           "https://adityapagare619.github.io"),
                    help="comma-separated origins allowed to fetch /api/* "
                         "cross-origin. Default: the hosted prod console "
                         "only. \"*\" allows any origin — restrict it in "
                         "production; passing * explicitly prints a loud "
                         "startup warning. Empty string = CORS off "
                         "(reverse proxy owns the policy)")
    ap.add_argument("--token-file", default=None,
                    help="operator bearer-token file (default: "
                         "<state-dir>/operator_token.json, or "
                         "SENTINEL_OPERATOR_TOKEN_FILE). Generated once "
                         "at first boot (0600), shown ONCE on stdout, "
                         "never logged again.")
    args = ap.parse_args(argv)

    from socketserver import ThreadingMixIn
    from wsgiref.simple_server import (WSGIRequestHandler, WSGIServer,
                                       make_server)

    class QuietHandler(WSGIRequestHandler):
        def log_message(self, *args):  # keep the demo console clean
            pass

    class ThreadedWSGIServer(ThreadingMixIn, WSGIServer):
        daemon_threads = True

    app = build_app(args)

    # --- first-boot operator token: shown ONCE, never logged again --------
    boot_token = app.operator_tokens.first_boot_token
    if app.operator_tokens.ephemeral:
        print("!" * 70, flush=True)
        print("[platform] WARNING: operator token store is EPHEMERAL "
              "(state dir not writable)", flush=True)
        print("[platform] WARNING: the token below dies with this "
              "process — set SENTINEL_OPERATOR_TOKEN", flush=True)
        print("!" * 70, flush=True)
    if boot_token:
        print("=" * 70, flush=True)
        print("[platform] FIRST BOOT — operator token generated "
              "(shown ONCE, never again)", flush=True)
        print("[platform]", flush=True)
        print("[platform]   Paste this token into the console sign-in "
              "field:", flush=True)
        print("[platform]", flush=True)
        print(f"[platform]     {boot_token}", flush=True)
        print("[platform]", flush=True)
        print(f"[platform]   Stored (0600) at: "
              f"{app.operator_tokens.path}", flush=True)
        print("[platform]   Every /api/* request needs: "
              "Authorization: Bearer <token>", flush=True)
        print("[platform]   Health probes stay open: "
              "/api/v1/health/live, /api/v1/health/ready", flush=True)
        print("=" * 70, flush=True)

    # --- loud CORS warning: "*" is never silent ---------------------------
    if (args.cors_origins or "").strip() == "*":
        print("!" * 70, flush=True)
        print("[platform] WARNING: --cors-origins=* — ANY website the "
              "operator visits", flush=True)
        print("[platform] WARNING: can make their browser SEND requests to "
              "this API.", flush=True)
        print("[platform] WARNING: Restrict to your console origin, e.g. "
              "--cors-origins=https://adityapagare619.github.io", flush=True)
        print("!" * 70, flush=True)

    server = make_server(args.host, args.port, app,
                         server_class=ThreadedWSGIServer,
                         handler_class=QuietHandler)
    print(f"[platform] read API on http://{args.host}:{args.port} "
          f"(db={args.db} mode=ro, data_source={args.data_source}, "
          f"ui={'on' if app.ui_dir else 'off'}, "
          f"cors_origins={args.cors_origins}, "
          f"max_inflight={args.max_inflight}, "
          f"shed_load={args.shed_load})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
