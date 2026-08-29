# Observability architecture

This document explains how Screenkeeper produces telemetry, why the app
never transports it anywhere, and how a host-level agent (Grafana Alloy,
initially) picks it up. It complements `docs/architecture/control-plane.md`,
which covers the edge/control network shape and identity hierarchy this
document assumes.

## The boundary

```text
SCREENKEEPER (edge and control)          HOST AGENT (Grafana Alloy)
        |                                          |
   produces metrics                         collects + enriches
   at a local endpoint                       + batches + buffers
        |                                          |
   STOP -- no outbound                       exports over outbound
   telemetry call, ever                       HTTPS to a central backend
```

Screenkeeper's responsibility ends at exposing local Prometheus metrics. It
never holds a central Prometheus/Grafana URL, a remote-write credential, or
any knowledge that a central observability system exists at all. The host
agent -- infrastructure belonging to the machine, not to any one
application -- is solely responsible for deciding where telemetry goes. This
mirrors the edge/control boundary in `control-plane.md`: the appliance
initiates every request outward and holds no inbound listener reachable from
the WAN; the same is now true of telemetry.

| Screenkeeper knows | Screenkeeper does NOT know |
|---|---|
| `metrics.enabled` | Grafana URL |
| `metrics.host` / `metrics.port` | Prometheus / Mimir URL |
| (nothing else) | remote-write credentials |
| | Mimir tenant |
| | that a central backend exists at all |

## Why this changed

Screenkeeper players are edge signage appliances that end up on unrelated
physical networks: NAT, CGNAT, consumer routers, corporate firewalls. A
player must never require inbound WAN connectivity for anything, telemetry
included. The previous design (a hand-rolled Prometheus Remote Write pusher
and a Loki log pusher, both in `src/signage_controller/observability.py`)
put transport, retries, and central-service bearer tokens inside the
application itself -- the wrong architectural layer for something that has
to keep controlling TVs and playing signage even when a WAN link, a
central Prometheus, or a telemetry agent is completely absent.

## Target shape

```text
SCREENKEEPER PLAYER HOST
┌───────────────────────────────────────────┐
│ signage-controller run       :9464/metrics │
│ signage-controller playback  :9465/metrics │──┐
│ (mpv, OS, host)                            │  │
│                                             │  ▼
│                                    Grafana Alloy (systemd, host-level)
│                                    ├── prometheus.scrape (15s)
│                                    ├── prometheus.exporter.unix (30s)
│                                    ├── otelcol.receiver.otlp (4317/4318)
│                                    ├── identity enrichment
│                                    └── prometheus.remote_write
└───────────────────────────────────────────┘
                        │ outbound HTTPS
                        ▼
              CENTRAL OBSERVABILITY SYSTEM
```

The same shape applies to a Screenkeeper Control host, scraping its own
local `/metrics` instead (see `control/README.md`'s Observability section --
that server's public API port is reachable over WAN from every enrolled
player's heartbeat, so its `/metrics` is deliberately served on a *second*,
separate, localhost-only listener, never the public one).

## What Screenkeeper produces

### Edge (`src/signage_controller/observability.py`)

Each long-running process gets its own local Prometheus endpoint (they are
separate OS processes, so they cannot share one in-process registry):

| Process | Default endpoint |
|---|---|
| `signage-controller run` | `http://127.0.0.1:9464/metrics` |
| `signage-controller playback run` | `http://127.0.0.1:<metrics.port + 1>/metrics` (`9465` by default) |
| `signage-controller agent run` | `http://127.0.0.1:<metrics.port + 2>/metrics` (`9466` by default) |

Metrics published (all local-only; no `job`/`instance` label is set by the
app -- Alloy's scrape config supplies those):

- `signage_controller_tv_connection_up{tv_id}`
- `signage_controller_tv_power_on{tv_id}`
- `signage_controller_tv_volume{tv_id}`
- `signage_controller_tv_input{tv_id,input_id}`
- `signage_controller_tv_status_updated_timestamp_seconds{tv_id}`
- `signage_controller_tv_commands_total{tv_id,command,result}`
- `signage_controller_tv_command_duration_seconds{tv_id,command}`
- `signage_controller_player_up{player_id}`
- `signage_controller_player_restarts_total{player_id}`
- `signage_controller_player_status_updated_timestamp_seconds{player_id}`
- `signage_controller_control_plane_reachable`: whether the last heartbeat
  attempt reached the control plane (published by `agent run` only).
- `signage_controller_control_plane_auth_rejected`: whether the control plane
  is currently rejecting this device's credential -- distinct from ordinary
  unreachability, since it means an operator must re-enroll.
- `signage_controller_control_plane_heartbeats_total{result}`
- `signage_controller_control_plane_last_success_timestamp_seconds`

Plus `prometheus_client`'s default process collectors
(`process_start_time_seconds`, `process_resident_memory_bytes`, etc.) with
no extra code -- that's the answer to "when did this process last start,"
rather than a bespoke uptime gauge.

There is no content-synchronization metric: that subsystem does not exist
yet in this repository. Don't invent one ahead of the subsystem it would
describe.

### Control (`control/`)

`GET /metrics` on `SCREENKEEPER_CONTROL_METRICS_HOST:SCREENKEEPER_CONTROL_METRICS_PORT`
(default `127.0.0.1:9464`), Micrometer-backed, currently exposing
`http.server.requests` (method/status only -- see the cardinality note
below) for the public API, plus Micrometer's standard JVM/process binders
(`JvmMemoryMetrics`, `JvmGcMetrics`, `ProcessorMetrics`, `UptimeMetrics`) --
the same "uptime via a standard collector" answer as the edge side's
`prometheus_client` process collectors, not a bespoke gauge.

## Adding a new metric

Follow the pattern already established by the existing gauges/counters:

1. Register it once, at reporter construction (Python: against the
   reporter's private `CollectorRegistry`; Kotlin: against the shared
   `PrometheusMeterRegistry`).
2. Update it from the exact call site that already has the fact to report --
   `converge()`/`TvManager` for TV state, `PlayerSupervisor` for playback
   state, an http4k filter or service call site for control-plane facts.
3. Label only by a stable, bounded entity id (`tv_id`, `player_id`).

### Cardinality

Never label by: a URL, a filename, an arbitrary error message, a
per-operation UUID, a timestamp, a content hash, or a request id. Each
distinct label value becomes a permanent time series in whatever backend
Alloy forwards to. `http.server.requests` on the control server deliberately
tags by method/status only, not the raw request path -- several routes embed
an id (`/api/v1/enrollments/{enrollment_id}`), and tagging by the literal
path would be one series per id ever seen.

Player/location identity (`screenkeeper_player_id`, `screenkeeper_location_id`,
`screenkeeper_organization_id`) is deliberately *not* a label Screenkeeper
attaches to any metric series itself -- Alloy attaches it once, via
`external_labels`/relabeling, sourced from `device.json` (see below). Adding
it per-series inside the app would duplicate a stable fact onto every sample
for no benefit.

## OTLP readiness

Both `edge.alloy` and `control-plane.alloy` (`deploy/alloy/`) run a local
OTLP receiver on `127.0.0.1:4317` (gRPC) and `127.0.0.1:4318` (HTTP), wired
through to the same central Prometheus destination for OTLP *metrics*.
Nothing in this repository emits OTLP today -- there's no tracing use case
yet, and OTEL SDK integration is explicitly deferred (see below). The
contract exists so a future application, or a future auto-instrumentation
agent, can start exporting to `OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318`
without any Alloy reconfiguration. When that happens, wiring OTLP traces/logs
to a real backend (Tempo/Loki/etc.) is a deployment concern for the `.alloy`
config, not an application change.

## Player/location identity enrichment

Screenkeeper already has a canonical identity: `DeviceIdentity` /
`DeviceStore` (`src/signage_controller/control_plane/identity.py`),
persisted at `$STATE_DIR/device.json` after enrollment. Telemetry reuses it
rather than inventing a second identity system:

```text
device.json (player_id, location_id, organization_id)
        │
        │ read-only, by deploy/alloy/render-identity-env.sh
        ▼
/etc/alloy/screenkeeper-identity.env
        │
        │ EnvironmentFile=- on the alloy systemd unit
        ▼
sys.env("SCREENKEEPER_PLAYER_ID") in edge.alloy's relabel rules
```

Before enrollment (`device.json` absent), Alloy still starts, still scrapes,
still exports -- the `screenkeeper_*` labels are simply empty until this
host is enrolled. Alloy never blocks on Screenkeeper's enrollment state, and
Screenkeeper's systemd units carry no dependency on Alloy in either
direction. See `deploy/alloy/README.md` for the operator-facing setup.

## Grafana Alloy as host infrastructure

One Alloy process per host, started as its own systemd **system** service,
installed via Grafana's official package -- not by `scripts/install.sh`, and
not per-application. See `deploy/alloy/README.md` for install/config steps
and `deploy/alloy/edge.alloy` / `deploy/alloy/control-plane.alloy` for the
ready-to-use configs (scrape targets, a curated `prometheus.exporter.unix`
collector set, the OTLP receiver, and `prometheus.remote_write`).

```text
host
├── screenkeeper (systemd user units)
├── mpv
└── alloy (systemd system unit, installed separately)
```

Alloy's only relationship to Screenkeeper's units is as a scrape target --
never a startup dependency in either direction.

## Health and heartbeat semantics

These are different failure conditions, and a fleet dashboard/alerting setup
needs to be able to tell them apart:

| Condition | How to tell |
|---|---|
| Host unreachable | Alloy's own target/agent is absent from the central backend entirely |
| Alloy down, host up | host metrics (`node_*`/`prometheus_exporter_unix_*`) stop, Screenkeeper's own metrics also stop (same scrape path) |
| Screenkeeper process down, host+Alloy up | that process's `up{job="signage_controller"}` (Prometheus's own per-scrape metric) goes to `0`; other jobs on the same host keep reporting |
| WAN down | Alloy's local scrapes keep succeeding (visible in its own `/api/v0/web/components` health, or its local logs); its `prometheus.remote_write` component's WAL grows until connectivity returns -- nothing in Screenkeeper's own metrics reflects this, because Screenkeeper never touches the WAN for telemetry |
| Central backend down | same as WAN down, from Alloy's perspective: buffered locally, retried automatically |

`process_start_time_seconds` (free, from `prometheus_client`'s default
collectors) answers "did this process restart recently," independent of
`signage_controller_player_restarts_total`/`tv_connection_up`, which answer
"is the thing this process manages actually healthy."
`signage_controller_control_plane_reachable` and
`_last_success_timestamp_seconds` (from `agent run`) answer a related but
different question -- "is this player still phoning home to the control
plane" -- which can go stale even while TV control and playback are fully
healthy, since the three processes are architecturally independent.

No alerting platform is implemented as part of this work -- the metrics
above are what a central Prometheus-compatible alerting layer would query.

## Logs

The Remote Write refactor also removed `LokiHandler`, the push-based log
shipper. Python's stdlib `logging` itself is untouched; only the network
transport is gone. The replacement path is journald: every Screenkeeper
process already runs under a systemd unit, so Alloy's `loki.source.journal`
component (or equivalent) can tail these logs with zero new Screenkeeper
code. Wiring that up is left to an operator who wants centralized logs --
it's explicitly not implemented or tested as part of this pass, since a
working local logging story (systemd journal + `journalctl`) already exists
and nothing here should replace it.

## Traces

No custom spans exist anywhere in this repository, and none are added by
this work. Good future candidates, if tracing is ever adopted, are
operations with real business meaning -- content synchronization, a
control-plane request, TV discovery, a TV command, playback initialization
-- not an indiscriminate span-per-function sweep. OpenTelemetry
auto-instrumentation (e.g. the OTEL Java agent for `control/`) is also
deferred; enabling it later needs no application code change, just
`-javaagent:opentelemetry-javaagent.jar` plus the standard
`OTEL_EXPORTER_OTLP_ENDPOINT`/`OTEL_SERVICE_NAME`/`OTEL_RESOURCE_ATTRIBUTES`
environment variables pointed at the local Alloy instance.

## Local development

`deploy/alloy/dev/compose.yaml` runs Alloy + Prometheus + Grafana in
containers, provisioned with `grafana/signage-controller-dashboard.json`.
The Screenkeeper player itself is never containerized (`AGENTS.md`); it runs
natively as usual, and the dev Alloy config reaches it via
`host.docker.internal`. See `deploy/alloy/README.md`.

```bash
docker compose -f deploy/alloy/dev/compose.yaml up
# Screenkeeper raw metrics:
curl http://127.0.0.1:9464/metrics
# Alloy health / component graph:
curl -s http://127.0.0.1:12345/api/v0/web/components | jq
# Prometheus targets:
open http://127.0.0.1:9090/targets
# Grafana (anonymous admin in this dev stack only):
open http://127.0.0.1:3000
```

## Troubleshooting

| Check | How |
|---|---|
| Screenkeeper `/metrics` works | `curl http://127.0.0.1:9464/metrics` on the host (`:9465` for playback, `:9466` for the agent); confirm `metrics.enabled: true` in `config.yaml` if it refuses the connection |
| Alloy can scrape Screenkeeper | `curl -s http://127.0.0.1:12345/api/v0/web/components \| jq '.[] \| select(.localID=="prometheus.scrape.screenkeeper")'` -- `health.state` should be `healthy` |
| OTLP receiver is listening | `curl -v http://127.0.0.1:4318/v1/metrics` should get a response (even a 4xx from an empty POST proves the port is open); or check `journalctl -u alloy \| grep otelcol.receiver.otlp` |
| Host metrics are being collected | `curl -s http://127.0.0.1:12345/api/v0/web/components \| jq '.[] \| select(.localID=="prometheus.exporter.unix.host")'` |
| Remote-write succeeds | `journalctl -u alloy \| grep prometheus.remote_write` for WAL/send errors; a healthy Alloy with an unreachable backend logs retries here, never in Screenkeeper's own logs |
| WAN outage doesn't break Screenkeeper | stop Alloy (`systemctl stop alloy`) or block outbound network for it; confirm `systemctl --user status screenkeeper.service screenkeeper-playback.service` stay active the whole time |
| Telemetry resumes after connectivity returns | restore Alloy/WAN; its WAL replays buffered samples automatically -- no Screenkeeper-side action needed |

## Implemented now vs. deferred

**Implemented:** local `/metrics` on both edge processes and the control
server (on a separate listener there); removal of the old Remote Write
push, its protobuf encoding, and the Loki push handler; new playback and
TV-command metrics; `deploy/alloy/*.alloy` (scrape, curated host metrics,
inert-but-ready OTLP receiver, remote_write); the identity-bridge script;
a light-touch Grafana dashboard update; this document; local dev compose
stack; `signage-controller agent run` heartbeat metrics
(`signage_controller_control_plane_*`); `scripts/install.sh --install-alloy`,
which installs and configures Alloy on apt-based edge hosts (credential
population in `/etc/alloy/screenkeeper.env` stays manual by design -- see
`deploy/alloy/README.md`).

**Deferred:** OTEL tracing spans in either component; OTEL auto-
instrumentation wiring for `control/`; an actual Alloy→Loki log pipeline
(journald tailing is documented, not implemented/tested here); content-sync
metrics (no such subsystem exists yet); domain-level control-plane counters
beyond the one HTTP request timer; an expanded/redesigned Grafana dashboard
beyond the two panels added here; any alerting platform.
