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
| `metrics.host` / `metrics.port` | Prometheus / Mimir / Tempo / Loki URL |
| `tracing.enabled` | remote-write / OTLP / Loki-push credentials (bearer tokens) |
| `tracing.endpoint` (always local: `http://127.0.0.1:4318`) | Mimir tenant |
| (nothing else) | that a central backend exists at all |

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
│ signage-controller playback  :9465/metrics │
│ signage-controller agent     :9466/metrics │
│   (all three optionally push OTLP spans    │
│    to 127.0.0.1:4318 -- tracing.enabled)   │
│ journald (screenkeeper*.service units)     │
│ (mpv, OS, host)                            │
└──────────────────────┬──────────────────────┘
                        │ scrape / OTLP push / journal read (all local)
                        ▼
              Grafana Alloy (systemd, host-level)
              ├── prometheus.scrape (15s)
              ├── prometheus.exporter.unix (30s)
              ├── otelcol.receiver.otlp (4317/4318)  -- metrics, traces
              ├── loki.source.journal                -- filtered to screenkeeper*.service
              ├── identity enrichment (metrics: relabel: traces: span attributes)
              ├── prometheus.remote_write  -- bearer token
              ├── otelcol.exporter.otlp    -- bearer token
              └── loki.write               -- bearer token
                        │
                        │ outbound HTTPS
                        ▼
                 CENTRAL OBSERVABILITY SYSTEM
                 (metrics / traces / logs backends)
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

Optionally, when `tracing.enabled` (see below), the same three processes
also emit OTel spans over OTLP to the local Alloy receiver -- see "Traces".

### Control (`control/`)

`GET /metrics` on `SCREENKEEPER_CONTROL_METRICS_HOST:SCREENKEEPER_CONTROL_METRICS_PORT`
(default `127.0.0.1:9464`), Micrometer-backed, currently exposing
`http.server.requests` (method/status only -- see the cardinality note
below) for the public API, plus Micrometer's standard JVM/process binders
(`JvmMemoryMetrics`, `JvmGcMetrics`, `ProcessorMetrics`, `UptimeMetrics`) --
the same "uptime via a standard collector" answer as the edge side's
`prometheus_client` process collectors, not a bespoke gauge.

Optionally, an operator-attached OTel Java agent auto-instruments this
process's HTTP/JDBC layers, plus a couple of manual spans -- see "Traces".

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
OTLP receiver on `127.0.0.1:4317` (gRPC) and `127.0.0.1:4318` (HTTP). Traces
are now wired end to end: the edge process's optional `tracing:` config and
`control/`'s optional OTel Java agent both push spans to this local receiver,
which forwards them (with span-level identity attributes attached, see
"Traces" below) to a central OTLP backend via `otelcol.exporter.otlp`, bearer
token from `sys.env("TRACES_OTLP_TOKEN")` -- deployment configuration only,
never known to Screenkeeper. Verified locally: a span emitted by
`configure_tracing()` reaches a local Tempo instance through exactly this
path (`deploy/alloy/dev/compose.yaml`'s dev stack).

Metrics-over-OTLP remains the one genuinely inert part of this contract:
nothing in this repository emits OTLP *metrics* today (both processes emit
Prometheus metrics via the pull-based `/metrics` exposition instead), so that
half of the receiver's `output` block forwards to `otelcol.exporter.prometheus`
but never receives anything. It stays wired for whenever a future OTLP
metrics producer wants it, needing no Alloy reconfiguration when that happens.

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
                                     (metrics) and otelcol.processor.attributes
                                     (traces) -- same env vars, two components
```

Before enrollment (`device.json` absent), Alloy still starts, still scrapes,
still exports -- the `screenkeeper_*` labels/attributes are simply empty
until this host is enrolled. Alloy never blocks on Screenkeeper's enrollment state, and
Screenkeeper's systemd units carry no dependency on Alloy in either
direction. See `deploy/alloy/README.md` for the operator-facing setup.

## Grafana Alloy as host infrastructure

One Alloy process per host, started as its own systemd **system** service,
installed via Grafana's official package -- not by `scripts/install.sh`, and
not per-application. See `deploy/alloy/README.md` for install/config steps
and `deploy/alloy/edge.alloy` / `deploy/alloy/control-plane.alloy` for the
ready-to-use configs (scrape targets, a curated `prometheus.exporter.unix`
collector set, the OTLP receiver, journald tailing, and the three bearer-
token-authenticated export components: `prometheus.remote_write`,
`otelcol.exporter.otlp`, `loki.write`).

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

A handful of example PromQL expressions, for whoever configures that central
alerting layer -- these are illustrations, not a shipped ruleset:

| Condition | Example PromQL |
|---|---|
| Screenkeeper process down | `up{job="signage_controller"} == 0` |
| TV connection lost | `signage_controller_tv_connection_up == 0` |
| Playback not running | `signage_controller_player_up == 0` |
| Player crash-looping | `increase(signage_controller_player_restarts_total[15m]) > 3` |
| Control-plane heartbeat stale | `time() - signage_controller_control_plane_last_success_timestamp_seconds > 300` |
| Control-plane credential rejected | `signage_controller_control_plane_auth_rejected == 1` |
| Enrollment claims failing | `increase(screenkeeper_enrollment_claim_total{result!="success"}[15m]) > 5` |
| Enrollment reaper stalled | `increase(screenkeeper_enrollment_reap_runs_total[15m]) == 0` |

(The reaper's default `interval` is 5 minutes, so a 15-minute window with
zero runs is a real stall signal, not noise.)

## Logs

The Remote Write refactor also removed `LokiHandler`, the push-based log
shipper. Python's stdlib `logging` itself is untouched; only the network
transport changed. The replacement path is journald, now wired: every
Screenkeeper edge process runs under a systemd **user** unit, so
`loki.source.journal` in `edge.alloy` tails the host journal and a relabel
rule keeps only entries from `screenkeeper*.service` units (matched on
`__journal__systemd_user_unit`, since these are user units, not
`__journal__systemd_unit`), then ships them to a central Loki-compatible
backend via `loki.write`, bearer token from `sys.env("LOGS_REMOTE_WRITE_TOKEN")`.

`control-plane.alloy` carries the same block for parity, but it is only
meaningful for Alloy's own logs today: `control/` deploys via Docker and has
no systemd unit of its own to filter for. It becomes useful automatically if
a host-native control deployment ever exists.

## Traces

Both components now emit spans around a small set of operations with real
business meaning, not an indiscriminate span-per-function sweep:

- **Edge** (`src/signage_controller/`, manual OTel Python SDK spans, no
  auto-instrumentation available for a non-web Python daemon): TV command
  dispatch (`signage_controller.tv_command`, `controller.py::_run_command`),
  playback initialization (`signage_controller.playback.start`,
  `playback/supervisor.py::PlayerSupervisor._run_loop`), and the
  control-plane heartbeat (`signage_controller.control_plane.heartbeat`,
  `control_plane/agent.py::HeartbeatAgent._beat_once`). Gated by the optional
  `tracing:` config block (see "What Screenkeeper produces" below); when
  disabled, every span is a true OpenTelemetry no-op (no SDK objects
  constructed, zero cost) via dependency injection through the same
  `tracer` parameter pattern the existing `status_reporter` objects use --
  never a bare global lookup that tests would have to fight over.
- **Control** (`control/`): the OTel Java agent, opt-in via `-javaagent`
  (see `control/README.md`'s "Tracing" section), auto-instruments http4k's
  Jetty server and the JDBC driver with zero application code -- **confirmed
  locally**: a request produces a proper `SERVER` span
  (`io.opentelemetry.jetty-12.0` instrumentation) that correctly parents the
  JDBC client spans and this codebase's own manual spans in the same trace.
  Two manual spans hedge against relying on auto-instrumentation alone for
  the operations with the most business meaning: `EnrollmentService`'s
  `screenkeeper.enrollment.create_or_reuse`/`.claim` (tagged with
  `error.code` from `DomainError` on the failure paths), and
  `EnrollmentReaper`'s `screenkeeper.enrollment.reap` (the one background,
  non-HTTP-triggered job, so the only class here with no auto-instrumented
  parent span to lean on regardless). These use `GlobalOpenTelemetry.getTracer(...)`
  (`opentelemetry-api` only, no SDK dependency in application code) -- a true
  no-op whenever the agent isn't attached.
- Per OpenTelemetry's own status convention, a successful span is left
  `UNSET`, not explicitly marked `OK` -- only failures set `ERROR` (with
  `record_exception`). Span attributes are not subject to the Prometheus
  cardinality rule above (each span is already a unique event, not a
  persistent time series), but must never carry secrets or bearer tokens.

## Local development

`deploy/alloy/dev/compose.yaml` runs Alloy + Prometheus + Loki + Tempo +
Grafana in containers, provisioned with
`grafana/signage-controller-dashboard.json` and Loki/Tempo/Prometheus
datasources. The Screenkeeper player itself is never containerized
(`AGENTS.md`); it runs natively as usual, and the dev Alloy config reaches it
via `host.docker.internal`. See `deploy/alloy/README.md`. The journald bind
mount only has anything to tail on a native Linux dev host -- Docker Desktop
(Windows/Mac) starts the log pipeline cleanly but tails nothing there, which
is expected, not a bug.

```bash
docker compose -f deploy/alloy/dev/compose.yaml up
# Screenkeeper raw metrics:
curl http://127.0.0.1:9464/metrics
# Alloy health / component graph:
curl -s http://127.0.0.1:12345/api/v0/web/components | jq
# Prometheus targets:
open http://127.0.0.1:9090/targets
# Grafana (anonymous admin in this dev stack only) -- Explore against the
# Loki or Tempo datasource to see log lines / spans once something emits them:
open http://127.0.0.1:3000
# Or query Tempo directly for a trace ID printed by an application/test:
curl -s http://127.0.0.1:3200/api/traces/<trace_id> | jq
```

## Troubleshooting

| Check | How |
|---|---|
| Screenkeeper `/metrics` works | `curl http://127.0.0.1:9464/metrics` on the host (`:9465` for playback, `:9466` for the agent); confirm `metrics.enabled: true` in `config.yaml` if it refuses the connection |
| Alloy can scrape Screenkeeper | `curl -s http://127.0.0.1:12345/api/v0/web/components \| jq '.[] \| select(.localID=="prometheus.scrape.screenkeeper")'` -- `health.state` should be `healthy` |
| OTLP receiver is listening | `curl -v http://127.0.0.1:4318/v1/metrics` should get a response (even a 4xx from an empty POST proves the port is open); or check `journalctl -u alloy \| grep otelcol.receiver.otlp` |
| Traces are flowing | confirm `tracing.enabled: true` in `config.yaml` (edge) or `JAVA_TOOL_OPTIONS`/`OTEL_EXPORTER_OTLP_ENDPOINT` are set (control); `curl -s http://127.0.0.1:12345/api/v0/web/components \| jq '.[] \| select(.localID | startswith("otelcol"))'`; `journalctl -u alloy \| grep otelcol.exporter.otlp` for export errors |
| Journal tailing is picking up Screenkeeper's units | `curl -s http://127.0.0.1:12345/api/v0/web/components \| jq '.[] \| select(.localID=="loki.source.journal.screenkeeper")'` -- `health.state` should be `healthy`; on the edge host, `journalctl --user -u screenkeeper.service -n5` should show recent entries to tail |
| Host metrics are being collected | `curl -s http://127.0.0.1:12345/api/v0/web/components \| jq '.[] \| select(.localID=="prometheus.exporter.unix.host")'` |
| Remote-write / OTLP export / Loki push succeeds | `journalctl -u alloy \| grep -E 'prometheus.remote_write\|otelcol.exporter.otlp\|loki.write'` for WAL/send errors; a healthy Alloy with an unreachable backend logs retries here, never in Screenkeeper's own logs |
| WAN outage doesn't break Screenkeeper | stop Alloy (`systemctl stop alloy`) or block outbound network for it; confirm `systemctl --user status screenkeeper.service screenkeeper-playback.service` stay active the whole time |
| Telemetry resumes after connectivity returns | restore Alloy/WAN; its WAL replays buffered samples automatically -- no Screenkeeper-side action needed |

## Implemented now vs. deferred

**Implemented:** local `/metrics` on both edge processes and the control
server (on a separate listener there); removal of the old Remote Write
push, its protobuf encoding, and the Loki push handler; new playback and
TV-command metrics; `deploy/alloy/*.alloy` (scrape, curated host metrics,
OTLP receiver, remote_write); the identity-bridge script; a light-touch
Grafana dashboard update; this document; local dev compose stack;
`signage-controller agent run` heartbeat metrics
(`signage_controller_control_plane_*`); `scripts/install.sh --install-alloy`,
which installs and configures Alloy on apt-based edge hosts (credential
population in `/etc/alloy/screenkeeper.env` stays manual by design -- see
`deploy/alloy/README.md`); OTel tracing spans in both components (edge:
TV command, playback start, control-plane heartbeat; control: enrollment
create/claim, the reaper job) via an optional `tracing:` config block (edge)
and `GlobalOpenTelemetry` (control), both true no-ops when disabled/the
agent isn't attached; OTel Java agent auto-instrumentation wiring for
`control/` (baked into the Docker image, inert unless an operator sets
`JAVA_TOOL_OPTIONS` at deploy time -- confirmed locally to auto-instrument
http4k's Jetty server and the JDBC driver); an actual Alloy→Loki log pipeline
(`loki.source.journal` filtered to `screenkeeper*.service` units, wired and
verified against a local Loki in the dev stack); a bearer-token auth
convention across all three telemetry channels (metrics/traces/logs), fleet-
appropriate for headless, unattended appliances; Loki and Tempo added to the
local dev compose stack, with Grafana datasources provisioned for both;
domain-level control-plane counters on `EnrollmentService`/`EnrollmentReaper`
(enrollment attempts, claims, and reaper runs, each tagged by a bounded
outcome/result label); Grafana dashboard coverage for the
`signage_controller_control_plane_*` heartbeat metrics; example PromQL alert
expressions documented in "Health and heartbeat semantics" above.

**Deferred:** content-sync metrics (no such subsystem exists yet); further
Grafana dashboard expansion beyond the panels added so far; any alerting
platform.
