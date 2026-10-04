# Deploying Sentinel to production (self-hosted)

Sentinel is middleware that runs **in your infrastructure** — the engine never
leaves your network. This guide takes a fresh Ubuntu 22.04/24.04 box to a
running production deployment: the paging pipeline (receiver) + the read API
(platform server) + the static console (GitHub Pages prod, or served locally).

**What you need:** a Linux host (2 vCPU / 4 GB is plenty), Python 3.12+,
a domain name pointing at the host (for TLS), and your own API keys
(PagerDuty routing key, Jev key) — bring-your-own-keys, we hold nothing.

**What you don't need:** Docker, Kubernetes, any pip package except
`cryptography` (see step 1), a database server (SQLite), or us.

Time: ~20 minutes. Every command below is copy-pasteable; nothing is
aspirational.

---

## 0. Architecture (what runs where)

```
your alert sources ──POST /hook──▶ sentinel-receiver :8080 (paging pipeline)
                                       │  writes sentinel.db + sentinel-state/
                                       ▼
                                  sentinel-platform :8081 (read-only API + UI)
                                       ▲
GitHub Pages prod console ─────────────┘  (static; you paste this URL below)
```

Two separate processes, two separate ports — dashboard load can never starve
the pager (process isolation first). The console makes zero Jev calls on read
paths; the UI sheds load before the paging path does.

---

## 1. Install

```bash
# system deps
sudo apt update && sudo apt install -y python3 python3-venv caddy git

# the one non-stdlib dependency (Ed25519 attestor identity; see
# ops/decision_log.md 2026-10-04 for the ratification)
sudo apt install -y python3-cryptography   # or: pip install cryptography==44.0.3

# layout
sudo mkdir -p /opt/sentinel /var/lib/sentinel /etc/sentinel
sudo git clone https://github.com/AdityaPagare619/sentinel /opt/sentinel/repo
cd /opt/sentinel/repo && git checkout main

# state + config (owned by the service user)
sudo useradd -r -s /usr/sbin/nologin sentinel || true
sudo mkdir -p /var/lib/sentinel/state /var/lib/sentinel/config
sudo chown -R sentinel:sentinel /var/lib/sentinel
```

Seed the config dir with the policy defaults:

```bash
sudo cp /opt/sentinel/repo/config/thresholds.json /var/lib/sentinel/config/ 2>/dev/null || \
  echo '{"generation":1}' | sudo tee /var/lib/sentinel/config/thresholds.json
sudo chown -R sentinel:sentinel /var/lib/sentinel/config
```

## 2. Environment file

`/etc/sentinel/sentinel.env` (root-readable only — it holds secrets):

```bash
sudo tee /etc/sentinel/sentinel.env > /dev/null <<'EOF'
# --- paging pipeline ---
SENTINEL_STATE_DIR=/var/lib/sentinel/state
SENTINEL_DB=/var/lib/sentinel/sentinel.db
SENTINEL_CONFIG_DIR=/var/lib/sentinel/config
# PagerDuty: prefer the console KEYS screen (BYOK); env is the fallback.
# PD_ROUTING_KEY=<your 32-hex Events API v2 key>
# Jev: the platform key ships with the build; override only to bill to yourself.
# TYPESAFE_API_KEY=<your jev key>
# Health endpoint bearer (pick a random 32-char string):
SENTINEL_HEALTH_TOKEN=<random>
# --- platform read API ---
SENTINEL_DATA_SOURCE=production
EOF
sudo chmod 600 /etc/sentinel/sentinel.env
```

## 3. systemd units

`/etc/systemd/system/sentinel-receiver.service`:

```ini
[Unit]
Description=Sentinel paging pipeline (receiver)
After=network-online.target
Wants=network-online.target

[Service]
User=sentinel
Group=sentinel
EnvironmentFile=/etc/sentinel/sentinel.env
WorkingDirectory=/opt/sentinel/repo
ExecStart=/usr/bin/python3 -m sentinel.receiver --port 8080 --bind 127.0.0.1 \
  --config-dir /var/lib/sentinel/config --state-dir /var/lib/sentinel/state
# src/ layout: the package lives in src/sentinel
Environment=PYTHONPATH=/opt/sentinel/repo/src
Restart=always
RestartSec=5
# fail-closed: never start unsupervised with a bad policy
# (the receiver refuses to start itself on invalid config — exit 2)

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/sentinel-platform.service`:

```ini
[Unit]
Description=Sentinel platform read API + console backend
After=network-online.target sentinel-receiver.service
Wants=network-online.target

[Service]
User=sentinel
Group=sentinel
EnvironmentFile=/etc/sentinel/sentinel.env
WorkingDirectory=/opt/sentinel/repo
ExecStart=/usr/bin/python3 platform/server/__main__.py --port 8081 \
  --host 127.0.0.1 --db /var/lib/sentinel/sentinel.db \
  --state-dir /var/lib/sentinel/state --data-source production
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

> **Note:** `python3 -m sentinel.receiver` needs `src/` on the path — the
> `PYTHONPATH` line above handles it. The platform server self-bootstraps
> its path (see `platform/server/__main__.py`).

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now sentinel-receiver sentinel-platform
```

## 4. Reverse proxy (Caddy — TLS in one block)

`/etc/caddy/Caddyfile`:

```
sentinel.example.com {
    # read API + console backend
    handle /api/* {
        # CORS for the hosted prod console (GitHub Pages). The platform
        # server also emits these itself (--cors-origins); set them here
        # instead if you prefer the proxy to own the policy, and then run
        # the platform with SENTINEL_CORS_ORIGINS="" (empty = same-origin).
        header Access-Control-Allow-Origin "https://AdityaPagare619.github.io"
        header Vary "Origin"
        reverse_proxy 127.0.0.1:8081
    }
    # the receiver's webhook ingress stays OFF the public internet:
    # point your alert sources at it over your private network instead.
    # If you must expose it, put it behind mTLS or IP allowlisting first.
    respond "not found" 404
}
```

```bash
sudo systemctl reload caddy
```

The static prod console (GitHub Pages, or any static host) points at
`https://sentinel.example.com` — enter it on the console's setup screen.
The console fetches the API **cross-origin**, so the browser requires CORS:
the platform server answers preflights and sets
`Access-Control-Allow-Origin` itself. Out of the box it allows `*`
(`SENTINEL_CORS_ORIGINS`, or `--cors-origins`); lock it down to just your
console origin in production:

```bash
# /etc/sentinel/platform.env — replace the default
SENTINEL_CORS_ORIGINS=https://AdityaPagare619.github.io
```

## 5. BYOK: add your PagerDuty key

Open the console → **KEYS** screen → paste your PagerDuty Events API v2
routing key (your service → Integrations → Events API v2). The key is
stored 600-perm on the host, never echoed back, never in logs. Then **Send
a test page** — a clearly-labeled `[SENTINEL TEST]` trigger through the real
PagerDuty API. Safe to acknowledge.

Simulated paging is **off** by default in production. If you enable it, the
console shows the in-band SIMULATED banner — pages are logged, not sent.

## 6. Health checks

```bash
# receiver: shallow liveness (no auth) + deep readiness (bearer)
curl -sf http://127.0.0.1:8080/livez && echo "receiver alive"
curl -sf -H "Authorization: Bearer $SENTINEL_HEALTH_TOKEN" \
  http://127.0.0.1:8080/healthz | python3 -m json.tool | head -20

# platform: read path
curl -sf http://127.0.0.1:8081/api/decisions?limit=1 | head -c 200; echo
```

Watch for: `webhook_auth_fail_open` in `/healthz` — if true, the webhook
secret is missing and signatures are not enforced (fail-open is loud, not
silent — fix it before trusting the pipeline).

## 7. Logs

```bash
journalctl -u sentinel-receiver -f     # paging pipeline
journalctl -u sentinel-platform -f     # read API
```

Key lines: `[sentinel] FORWARD FAILED` (page relay failed — the decision
already happened; investigate the relay, not the gate), `BROKEN: checkpoint`
(event-log tamper — treat as an incident).

## 8. Upgrading

```bash
cd /opt/sentinel/repo && git pull --ff-only
sudo systemctl restart sentinel-receiver sentinel-platform
```

The receiver refuses to start on invalid policy config (exit 2) — systemd
will retry; fix the config, don't force it. The platform server is stateless
and read-only: restart it freely.

## 9. What the console needs from you

The GitHub Pages **prod** console (`https://AdityaPagare619.github.io/sentinel/`)
is a static client. On first load it asks for your backend URL — enter
`https://sentinel.example.com`. That's the only configuration. Everything
else (decisions, evidence, simulator, audit, shadow, KEYS) talks to your
backend. No data of yours ever touches GitHub's servers beyond serving the
static files.

---

## Troubleshooting

| Symptom | Check |
|---|---|
| Console shows "Couldn't reach the backend" | `curl http://127.0.0.1:8081/api/decisions?limit=1` on the host; then Caddy (`journalctl -u caddy`) |
| Receiver exits with code 2 | Invalid policy config — `journalctl -u sentinel-receiver` shows the rejection; fix `/var/lib/sentinel/config/` |
| `/healthz` shows `webhook_auth_fail_open: true` | Set the webhook secret (see receiver config); signatures aren't enforced until you do |
| Test page not received | KEYS screen → check the key's last4; PagerDuty service → Integrations → confirm Events API v2 integration exists |
| `import sentinel.receiver` fails | `PYTHONPATH=/opt/sentinel/repo/src` — the systemd unit sets this; for manual runs export it yourself |
