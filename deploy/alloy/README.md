# Grafana Alloy host agent

This directory ships ready-to-use [Grafana Alloy](https://grafana.com/docs/alloy/)
configuration for Screenkeeper hosts. Alloy is **host-level infrastructure**,
installed and run independently of Screenkeeper itself:

```text
host
├── screenkeeper (systemd user units, packaging/*.service)
├── mpv
└── alloy (systemd system unit -- installed separately, see below)
```

Screenkeeper never depends on Alloy to start, and never knows where Alloy
sends data. See `docs/architecture/observability.md` at the repository root
for the full architecture and the "Screenkeeper knows X, does not know Y"
boundary.

## Files

| File | For | What it does |
|---|---|---|
| `edge.alloy` | a Screenkeeper Edge (player) host | scrapes local `/metrics`, host metrics, OTLP receiver, remote_write |
| `control-plane.alloy` | a Screenkeeper Control (central server) host | same shape, scrapes the control server's local metrics port instead |
| `render-identity-env.sh` | edge hosts only | reads `device.json`, writes an env file for player/location labels |
| `dev/compose.yaml` | local development | Alloy + Prometheus + Grafana, no production credentials needed |

These normally run on **different hosts** in production (a player is edge
infrastructure; the control server is central infrastructure), which is why
there are two separate files rather than one parameterized one.

## 1. Install Alloy

Use Grafana's own official package for your distribution -- this repository
does not reinvent Alloy packaging or ship a binary. See
https://grafana.com/docs/alloy/latest/get-started/install/ for the current
apt/rpm/binary instructions. Installing it registers `alloy` as its own
systemd **system** service (not a Screenkeeper `signage` user unit).

## 2. Place the config

```bash
# On an edge player host:
sudo cp edge.alloy /etc/alloy/config.alloy

# On the control server host:
sudo cp control-plane.alloy /etc/alloy/config.alloy
```

## 3. Configure identity and the remote-write destination

Alloy reads these from **its own** environment, via
`EnvironmentFile=/etc/alloy/screenkeeper.env` on the `alloy` systemd unit
(add this line with `systemctl edit alloy` if the packaged unit doesn't
already include it). Screenkeeper never sees this file or its values.

```bash
# /etc/alloy/screenkeeper.env -- 0600, root-owned, never committed anywhere
METRICS_REMOTE_WRITE_URL=https://prometheus.example.com/api/v1/write
METRICS_REMOTE_WRITE_USERNAME=your-username
METRICS_REMOTE_WRITE_PASSWORD=your-password
SCREENKEEPER_ENVIRONMENT=production
```

On an edge host, also wire the identity bridge so `screenkeeper_player_id`/
`screenkeeper_location_id`/`screenkeeper_organization_id` are attached once
this player is enrolled:

```bash
sudo cp render-identity-env.sh /usr/local/bin/screenkeeper-render-identity-env
sudo chmod +x /usr/local/bin/screenkeeper-render-identity-env
```

Then either run it from a small timer that restarts `alloy` after enrollment
changes, or add it as an `ExecStartPre=-` line on the **Alloy** unit (the
leading `-` makes a failure non-fatal to Alloy's own startup):

```ini
# /etc/systemd/system/alloy.service.d/override.conf
[Service]
ExecStartPre=-/usr/local/bin/screenkeeper-render-identity-env
EnvironmentFile=-/etc/alloy/screenkeeper-identity.env
EnvironmentFile=/etc/alloy/screenkeeper.env
```

A host that has never enrolled (`device.json` absent) still starts Alloy
normally; the `screenkeeper_*` labels are simply empty until it is.

## 4. Start Alloy

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now alloy
```

Screenkeeper's own units (`screenkeeper.service`, `screenkeeper-playback.service`,
`screenkeeper-agent.service`) have no dependency on `alloy` and start/stop
independently of it, in either order.

## 5. Validate

```bash
# The config itself
alloy validate /etc/alloy/config.alloy

# Alloy's own component health, and whether the scrape target is up
curl -s http://127.0.0.1:12345/api/v0/web/components | jq
sudo journalctl -u alloy -f
```

See "Troubleshooting" in `docs/architecture/observability.md` for the full
checklist (scrape failing, OTLP receiver, remote-write failures, WAN outage
recovery).

## Local development

`dev/compose.yaml` runs Alloy, a local Prometheus, and Grafana in containers
so a developer can see the full pipeline without any production credentials.
The Screenkeeper player itself is never containerized (see the repository's
`AGENTS.md`); it keeps running natively on the host, and the dev Alloy config
reaches it via `host.docker.internal`. See `dev/compose.yaml`'s own comments
for usage.
