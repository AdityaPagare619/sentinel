"""Track 7 (re-run) — shared in-process harness for AC-1 / AC-5.

Builds the REAL platform WSGI app (Track 1 bearer auth, Track 3 safety
endpoints, Track 4 canonical keystore) in-process with a fresh per-test
state dir, so the acceptance tests execute the attack chain and the
kill-switch protocol against production code paths — no sockets, no
mocks of the thing under test.

T1's real token issuance is used: ``OperatorTokenStore(path=<tmp>)``
issues the plaintext once via ``first_boot_token`` (exactly what the
first-boot banner hands the operator); the tests then present it as
``Authorization: Bearer <token>``.
"""

import io
import json
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.normpath(os.path.join(_HERE, "..", ".."))
for _p in (os.path.join(_REPO, "src"),
           os.path.join(_REPO, "platform", "server")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _pkg  # noqa: E402

_authmod = _pkg.load("auth")
_appmod = _pkg.load("app")


class _AdmissionStub:
    """Admission control that never sheds — the validation lane measures
    auth/safety behaviour, never admission."""

    def try_acquire(self):
        return True

    def release(self):
        pass


class _DegradeStub:
    def should_shed(self, path):
        return False, ""


def make_platform_app(kill_switch=None, cors_origins=None):
    """Build (app, operator_token, token_store, kill_switch, tmpdir).

    The tmpdir must be removed by the caller (use tempfile.TemporaryDirectory).
    """
    tmpdir = tempfile.mkdtemp(prefix="t7-validation-")
    tokens = _authmod.OperatorTokenStore(
        path=os.path.join(tmpdir, "operator_token.json"))
    token = tokens.first_boot_token
    if token is None:
        # Should not happen for a fresh dir, but be honest if it does.
        raise RuntimeError("OperatorTokenStore issued no first-boot token "
                           "for a fresh state dir")
    kwargs = dict(store=None, registry=None, gate=_AdmissionStub(),
                  degrade=_DegradeStub(),
                  operator_token_store=tokens,
                  kill_switch=kill_switch)
    if cors_origins is not None:
        kwargs["cors_origins"] = cors_origins
    app = _appmod.PlatformApp(**kwargs)
    return app, token, tokens, tmpdir


def wsgi_call(app, method, path, headers=None, body=None):
    """Call the WSGI app in-process; return (status_int, headers, body_bytes)."""
    raw = json.dumps(body).encode("utf-8") if body is not None else b""
    environ = {
        "REQUEST_METHOD": method.upper(),
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "CONTENT_LENGTH": str(len(raw)),
        "CONTENT_TYPE": "application/json" if body is not None else "",
        "wsgi.input": io.BytesIO(raw),
        "wsgi.errors": sys.stderr,
        "wsgi.url_scheme": "http",
        "SERVER_NAME": "127.0.0.1",
        "SERVER_PORT": "80",
    }
    for name, value in (headers or {}).items():
        environ["HTTP_" + name.upper().replace("-", "_")] = value
    captured = {}

    def start_response(status, response_headers, exc_info=None):
        captured["status"] = status
        captured["headers"] = dict(response_headers)

    chunks = app(environ, start_response)
    payload = b"".join(chunks)
    status_int = int(captured["status"].split()[0])
    return status_int, captured["headers"], payload


def auth_headers(token, **extra):
    hdrs = {"Authorization": f"Bearer {token}"}
    hdrs.update(extra)
    return hdrs


def json_body(payload):
    try:
        return json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
