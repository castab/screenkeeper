# Control plane architecture

This document explains how Screenkeeper Control (the cloud/server half of
the monorepo) relates to Screenkeeper Edge (the player half), and how
Screenkeeper Control is put together internally.

## The two halves, and the shared contract

```text
screenkeeper/
├── src/signage_controller/    Screenkeeper Edge -- Python, host-native, the client
├── control/                   Screenkeeper Control -- Kotlin, may run in Docker, the server
└── contracts/                 the shared boundary: contracts/openapi.yaml
```

Neither side imports the other's code. The edge does not generate a client
from the server's implementation, and the server does not import the edge's
Python modules. Both implement `contracts/openapi.yaml` independently, which
is what lets them ship on their own schedules from one repository. See
`contracts/README.md` for the full compatibility rules (additive evolution
within `/api/v1`, tolerating unknown fields, no version lockstep).

## Network shape

```text
                     Internet
                         |
                Screenkeeper Control
          Kotlin + http4k + Jdbi + PostgreSQL
                    /             \
        signed S3 URLs       outbound HTTPS only
                 /                 \
      S3-compatible store      Screenkeeper Player
                /         |         \
             DP-1       DP-2       DP-3   (physical connectors/TVs)
```

The appliance initiates every request; the control plane never calls the
appliance. There is no callback URL, no inbound port, and no webhook -- a
Screenkeeper host requires no inbound Internet connectivity at all. The TVs
themselves need no Internet connectivity either: they're controlled over the
LAN by the appliance, which is the only thing that talks to Screenkeeper
Control.

## Identity hierarchy

```text
Organization
    └── Location
          └── Player
                ├── physical connectors     (observed, from heartbeat.inventory)
                ├── configured TVs          (operator-configured, from heartbeat.configured_bindings)
                └── configured playback     (operator-configured, from heartbeat.configured_bindings)
```

A Player's Location (and therefore Organization) is assigned once, at
enrollment claim time, by an administrator -- never inferred by the
appliance from IP, GPS, Wi-Fi SSID, hostname, or network topology.

## Observed vs. configured

The heartbeat deliberately reports two different things side by side:

```text
OBSERVED     inventory.connectors[]      DP-1 is connected, its EDID hashes to abc123
CONFIGURED   configured_bindings         dev-menu is configured for DP-1 and dev-tv
```

The first is what the Linux kernel sees through `/sys/class/drm` on the
appliance. The second is what an operator wrote in the appliance's
`config.yaml`. Screenkeeper Control stores and returns both separately
(`players.*` + `player_reports.report` for the player's identity/liveness,
and `player_reports.report.configured_bindings` for the operator's intent)
so the control plane can compare them -- to notice that a display was
swapped, or that a player is bound to a connector that no longer exists.
Merging them at any layer would throw that signal away.

## Runtime layering

```text
http4k (routes, models, filters, security)
    |
application services (enrollment, heartbeat, registry, assets, content)
    |
Jdbi repositories        ObjectStore
    |
PostgreSQL               S3-compatible storage
```

- **http**: route definitions, request/response DTOs (kotlinx.serialization
  with `ignoreUnknownKeys`), the admin/enrollment/player auth filters, and
  the single error-mapping filter that turns any failure into the shared
  `{error, message}` JSON shape without leaking stack traces or SQL detail.
- **application**: the actual business flows -- create-or-reuse enrollment,
  claim, record heartbeat, asset revision finalization, manifest generation,
  assignment revisioning, and CRUD -- each owning its transaction boundary.
- **persistence**: one repository per table, plain classes operating on an
  injected `Handle`. **Flyway owns the schema exclusively** -- Jdbi never
  creates or alters a table, and there is no ORM-style auto-migration.
- **storage**: a narrow `ObjectStore` boundary (`head`, `presignPut`, and
  `presignGet`). The S3 adapter signs direct transfers; control never proxies
  media bytes and has no delete operation.

## Content synchronization

Control owns logical assets, immutable numbered revisions, per-device/local-
player assignments, and the monotonic manifest revision. S3-compatible storage
owns media bytes. Keys are content-addressed only as
`assets/<first-two-hash-characters>/<sha256>`; original filenames never affect
paths and available objects are never overwritten.

The edge pulls fresh signed download URLs, streams a unique temporary file,
verifies exact size and SHA-256, fsyncs, and atomically installs it in the local
cache before selecting it. Playback reads that selected local path. The signed
URL is never persisted. Desired, cached, selected-active, and actually-playing
revision IDs are reported through a dedicated latest-snapshot endpoint, not
mixed into the heartbeat.

This ordering is the outage guarantee: a manifest, control, storage, download,
checksum, or status-report failure cannot replace the current selection, so
valid signage continues without WAN access.

## Deployment model

Screenkeeper Edge remains a separate, host-native, non-containerized
deployment on the signage appliance -- that invariant is unchanged by this
phase and applies only to the player. Screenkeeper Control is explicitly
authorized to run in Docker (`control/Dockerfile`, `control/compose.dev.yaml`)
for both development and deployment. A typical local development topology:

```text
Host Linux
├── signage-controller agent run        (host-native)
├── signage-controller content run      (host-native)
├── signage-controller playback run     (host-native)
│
└── Docker / local services
    ├── screenkeeper-control
    └── PostgreSQL
```

## Deliberately not implemented in this phase

The following remain explicitly out of scope:

- Object deletion and cache garbage collection
- Playlists, schedules, multipart upload administration, and analytics
- NATS / JetStream or any other message bus
- Remote shell, SSH proxying, or arbitrary server-issued commands to a
  player -- Screenkeeper is a desired-state system, not a remote-access tool
- A web UI (the admin API is exercised via curl/httpie for this phase)

This phase deliberately does not build extension points for them ahead of an
actual second use case.
