# Screenkeeper Control

The cloud/server-side control plane for Screenkeeper. Implements the server
side of the shared contract at [`contracts/openapi.yaml`](../contracts/openapi.yaml):
appliance enrollment via a short pairing code, and heartbeat reporting of
observed display hardware and configured bindings. It also exposes a small
admin API for organizations/locations, enrollment claims, immutable video
assets, and per-player content assignments.

See [`contracts/README.md`](../contracts/README.md) for the compatibility
rules this server and the Screenkeeper Edge player both honor, and
[`docs/architecture/control-plane.md`](../docs/architecture/control-plane.md)
for the broader architecture.

## Technology

| Component | Version | Notes |
|---|---|---|
| Java | 25 (Temurin) | Runtime/toolchain. See "Java 25 note" below for the bytecode-target caveat. |
| Kotlin | 2.2.20 | `kotlin("jvm")` + `kotlin("plugin.serialization")` |
| Gradle | 9.7.1 | Kotlin DSL, version catalog (`gradle/libs.versions.toml`) |
| http4k | 6.15.1.0 | `http4k-core`, `http4k-server-jetty`, `http4k-format-kotlinx-serialization` |
| Jdbi | 3.49.0 | `jdbi3-core`, `jdbi3-postgres`, `jdbi3-kotlin` |
| Flyway | 11.7.0 | `flyway-core`, `flyway-database-postgresql` (Community Edition) |
| Kotest | 5.9.1 | `kotest-runner-junit5`, `kotest-assertions-core` |
| PostgreSQL (server) | 18+ | `postgres:18-alpine` in `compose.dev.yaml` and CI |
| PostgreSQL JDBC | 42.7.4 | `org.postgresql:postgresql` |
| kotlinx.serialization | 1.9.0 | JSON only; see `http/models/Json.kt` |
| kotlinx-datetime | 0.6.1 | `Instant` (de)serialization for wire timestamps |
| HikariCP | 6.2.1 | Connection pool feeding Jdbi's `DataSource` |
| logback-classic | 1.5.12 | See `src/main/resources/logback.xml` |
| AWS SDK for Kotlin | 1.5.79 | S3-compatible `head` and presigning only; media bytes never pass through Control |
| OkHttp | 5.0.0 | HTTP engine for the AWS Kotlin S3 client |

**Java 25 note:** Kotlin 2.2.20's compiler does not yet document explicit
support for JVM bytecode target `25` ("Kotlin does not yet support 25 JDK
target"), so `build.gradle.kts` pins `jvmTarget` to `24` while the Gradle
toolchain (and the Docker runtime image) still use JDK 25 to *run* the
compiler and the app. The resulting class files run correctly on a JRE 25,
since newer JVMs execute older bytecode without issue. Revisit once a Kotlin
release documents JVM 25 support.

**Flyway/PostgreSQL 18 note:** Flyway 11.7.0 logs "PostgreSQL 18.4 is newer
than this version of Flyway and support has not been tested. The latest
supported version of PostgreSQL is 17" on connect. This has been verified
functionally fine against a real PostgreSQL 18.4 instance (migration,
`/readyz`, and the full enrollment/heartbeat/admin flow all work) -- the
warning is Flyway being conservative about its own test matrix, not an
actual incompatibility observed here. Revisit if a newer Flyway release
explicitly adds tested PG18 support.

http4k backend is **Jetty**: the whole app is JDBC-bound and blocking (Jdbi
has no non-blocking mode), so a traditional thread-per-request servlet
container is the right fit.

## Architecture

```text
http (routes, models, filters)
    -> application (enrollment, heartbeat, registry, assets, content)
        -> persistence (repositories, Jdbi) -> PostgreSQL
        -> ObjectStore -> S3-compatible storage
```

Jdbi is used via explicit `Handle` + `Jdbi.inTransaction`/`useTransaction`/
`withHandle` from application services, not SQL Object interfaces -- the two
multi-step flows (create-or-reuse enrollment, claim) need conditional,
multi-statement orchestration. Repositories are plain classes taking a
`Handle` per call. Flyway owns the schema exclusively
(`src/main/resources/db/migration/`); Jdbi never creates or alters tables.

## Local development

```bash
cp .env.example .env   # fill in SCREENKEEPER_ADMIN_TOKEN and a DB password
docker compose -f compose.dev.yaml up -d
```

This starts PostgreSQL and Screenkeeper Control together. The Screenkeeper
Edge/mpv player is **never** part of this compose file -- it stays
host-native and runs directly on the signage appliance, pointed at
`http://localhost:8080` via `control_plane.base_url` in its own
`config.yaml`.

Build and test without Docker (needs a JDK 25 toolchain, resolved
automatically by Gradle if one isn't already on `PATH`):

```bash
./gradlew check                # fast unit tests, no database
./gradlew integrationTest      # Postgres-backed tests -- see below
./gradlew run                  # requires the required env vars to be set
```

### Integration tests (no Testcontainers)

Integration tests run against an **externally provided** PostgreSQL
instance rather than Testcontainers, since Testcontainers has known
reliability issues in some CI/CD environments (rootless Docker, missing
socket access, Docker-in-Docker restrictions). Point them at any reachable
Postgres via env vars that are deliberately separate from the app's own
runtime vars, so a test run can never touch a real deployment by accident:

```bash
export SCREENKEEPER_TEST_DATABASE_URL=jdbc:postgresql://localhost:5432/screenkeeper_control
export SCREENKEEPER_TEST_DATABASE_USER=screenkeeper
export SCREENKEEPER_TEST_DATABASE_PASSWORD=devpassword
./gradlew integrationTest
```

`docker compose -f compose.dev.yaml up -d postgres` is the easiest way to get
a Postgres instance for this locally. Each spec truncates all tables before
running (`beforeTest { TestDatabase.truncateAll() }`), so tests don't need
their own throwaway database.

## Configuration (environment variables)

| Variable | Required | Default | Notes |
|---|---|---|---|
| `SCREENKEEPER_CONTROL_HOST` | no | `0.0.0.0` | |
| `SCREENKEEPER_CONTROL_PORT` | no | `8080` | |
| `SCREENKEEPER_DATABASE_URL` | **yes** | -- | JDBC URL |
| `SCREENKEEPER_DATABASE_USER` | **yes** | -- | |
| `SCREENKEEPER_DATABASE_PASSWORD` | **yes** | -- | Never logged |
| `SCREENKEEPER_ADMIN_TOKEN` | **yes** | -- | No default. Startup fails clearly if absent. Never logged. |
| `SCREENKEEPER_HEARTBEAT_INTERVAL_SECONDS` | no | `30` | Used only to compute `online` |
| `SCREENKEEPER_ONLINE_MULTIPLIER` | no | `3` | `online = (now - last_seen_at) <= interval * multiplier` |
| `SCREENKEEPER_ENROLLMENT_TTL_MINUTES` | no | `15` | Pairing code lifetime |
| `SCREENKEEPER_ENROLLMENT_REAP_AFTER_MINUTES` | no | `60` | Grace period before an expired pending enrollment is deleted (410 -> 404) |
| `SCREENKEEPER_CONTROL_METRICS_ENABLED` | no | `true` | Local Prometheus `/metrics` exposition |
| `SCREENKEEPER_CONTROL_METRICS_HOST` | no | `127.0.0.1` | Bound on a separate listener from `SCREENKEEPER_CONTROL_PORT` -- see below |
| `SCREENKEEPER_CONTROL_METRICS_PORT` | no | `9464` | |
| `S3_ENDPOINT` | storage | -- | S3-compatible HTTPS endpoint |
| `S3_BUCKET` | storage | -- | Bucket name |
| `S3_ACCESS_KEY_ID` | storage | -- | Credential; never logged |
| `S3_SECRET_ACCESS_KEY` | storage | -- | Credential; never logged |
| `S3_REGION` | storage | -- | Signing region |
| `S3_PATH_STYLE` | no | `false` | Set `true` for providers requiring path-style addressing |
| `S3_UPLOAD_URL_TTL_SECONDS` | no | `3600` | Presigned PUT lifetime |
| `S3_DOWNLOAD_URL_TTL_SECONDS` | no | `3600` | Presigned GET lifetime |

`online` is always computed at read time from `last_seen_at` -- it is never
persisted as a boolean that could go stale.

Storage is disabled when none of the five required `S3_*` variables is set, so
existing deployments start unchanged. If any is present, all five are
required; startup reports the missing variable names without rendering either
credential. `S3_*` names describe the supported protocol, not a vendor.
Railway Buckets can map `BUCKET`, `ENDPOINT`, `ACCESS_KEY_ID`,
`SECRET_ACCESS_KEY`, and `REGION` directly into their corresponding variables;
other S3-compatible services can use endpoint, region, and path-style settings.
See [Railway's storage-bucket documentation](https://docs.railway.com/storage-buckets).

## Observability

`/metrics` is served on its own listener (`SCREENKEEPER_CONTROL_METRICS_HOST`:
`SCREENKEEPER_CONTROL_METRICS_PORT`), never on `SCREENKEEPER_CONTROL_PORT`.
That distinction matters here specifically because, unlike an edge player,
this server's public port is reachable over WAN -- every enrolled player's
heartbeat reaches it directly, so metrics must never share that listener.
For Prometheus scraping specifically, this process holds no remote-write URL
or credential of any kind; a host-level agent (e.g. Grafana Alloy) is
expected to scrape this endpoint and own all outbound transport. See
`docs/architecture/observability.md` at the repository root for the full
architecture.

```bash
curl http://127.0.0.1:9464/metrics       # local Prometheus exposition
curl -i http://localhost:8080/metrics    # 404 -- not served on the public port
```

### Tracing

The image bundles the OpenTelemetry Java agent at `/opt/opentelemetry-javaagent.jar`
(see `Dockerfile`), but it is **inert by default** -- no `-javaagent` flag is
set unless an operator adds one. Enabling it is a deployment change only, no
application code needs to change:

```bash
JAVA_TOOL_OPTIONS="-javaagent:/opt/opentelemetry-javaagent.jar"
OTEL_EXPORTER_OTLP_ENDPOINT="http://127.0.0.1:4318"   # the local Alloy OTLP receiver
OTEL_SERVICE_NAME="screenkeeper-control"
OTEL_RESOURCE_ATTRIBUTES="deployment.environment=production"
```

OTLP export is push-based (unlike the pull-based `/metrics` above), but the
push target is always a local Alloy instance -- never a WAN/central endpoint
-- so this does not cross the "no outbound transport in the app" boundary; see
`docs/architecture/observability.md`'s "OTLP readiness" section. Confirmed
locally: the agent's Jetty instrumentation produces a proper `SERVER` span for
http4k's Jetty backend (parenting the JDBC client spans and this codebase's
own manual spans, e.g. `screenkeeper.enrollment.claim`), so enabling the agent
gives full request-level tracing with no gaps to fill manually.

`EnrollmentReaper`'s background reap job and `EnrollmentService`'s
create/claim operations also emit manual spans via `GlobalOpenTelemetry`
(`opentelemetry-api` only, no SDK dependency in application code) -- these are
true no-ops whenever the agent isn't attached, so they carry no cost or
behavior change when tracing is off.

## Admin API walkthrough

```bash
BASE=http://localhost:8080
ADMIN=your-admin-token

# 1. Create an organization
curl -s -X POST "$BASE/api/v1/admin/organizations" \
  -H "Authorization: Bearer $ADMIN" -H "Content-Type: application/json" \
  -d '{"name":"Example Restaurant"}'

# 2. Create a location (organization_id from step 1)
curl -s -X POST "$BASE/api/v1/admin/locations" \
  -H "Authorization: Bearer $ADMIN" -H "Content-Type: application/json" \
  -d '{"organization_id":"<org-id>","name":"Downtown"}'

# 3. On the appliance: signage-controller device enroll
#    It prints a pairing code like Q7KM-4HF2 and polls for a claim.

# 4. Watch/look up the enrollment by that code
curl -s "$BASE/api/v1/admin/enrollments/Q7KM-4HF2" -H "Authorization: Bearer $ADMIN"

# 5. Claim it, assigning the location from step 2
curl -s -X POST "$BASE/api/v1/admin/enrollments/Q7KM-4HF2/claim" \
  -H "Authorization: Bearer $ADMIN" -H "Content-Type: application/json" \
  -d '{"location_id":"<location-id>","name":"Main Menu Wall"}'

# 6. On the appliance: signage-controller agent run
#    It sends a heartbeat; the player_id is now permanent.

# 7. List players
curl -s "$BASE/api/v1/admin/players" -H "Authorization: Bearer $ADMIN"

# 8. Inspect one player's identity, online state, and latest hardware report
curl -s "$BASE/api/v1/admin/players/<player-id>" -H "Authorization: Bearer $ADMIN"
```

## Direct content upload walkthrough

The upload never passes through Screenkeeper Control. This curl flow creates a
logical asset and immutable revision, uploads bytes with every returned signed
header, finalizes by HEAD verification, assigns the revision to the local
playback ID `dev-menu`, and lets the appliance reconcile it:

```bash
FILE=menu.mp4
SHA=$(sha256sum "$FILE" | cut -d' ' -f1)
SIZE=$(stat -c%s "$FILE")

ASSET=$(curl -s -X POST "$BASE/api/v1/admin/assets" \
  -H "Authorization: Bearer $ADMIN" -H "Content-Type: application/json" \
  -d '{"name":"Main menu"}')
ASSET_ID=$(printf '%s' "$ASSET" | jq -r .id)

REVISION=$(curl -s -X POST "$BASE/api/v1/admin/assets/$ASSET_ID/revisions" \
  -H "Authorization: Bearer $ADMIN" -H "Content-Type: application/json" \
  -d "{\"original_filename\":\"menu.mp4\",\"content_type\":\"video/mp4\",\"byte_size\":$SIZE,\"sha256\":\"$SHA\"}")
REVISION_ID=$(printf '%s' "$REVISION" | jq -r .asset_revision_id)
UPLOAD_URL=$(printf '%s' "$REVISION" | jq -r .upload.url)

# Convert every .upload.headers entry into a curl -H argument. Do not omit
# checksum/content headers: they are part of the signature and finalization.
mapfile -t UPLOAD_HEADERS < <(printf '%s' "$REVISION" | jq -r '.upload.headers | to_entries[] | "\(.key): \(.value)"')
CURL_HEADERS=()
for header in "${UPLOAD_HEADERS[@]}"; do CURL_HEADERS+=(-H "$header"); done
curl --fail -X PUT "$UPLOAD_URL" "${CURL_HEADERS[@]}" --data-binary @"$FILE"

curl --fail -X POST \
  "$BASE/api/v1/admin/assets/$ASSET_ID/revisions/$REVISION_ID/complete" \
  -H "Authorization: Bearer $ADMIN"
curl --fail -X PUT \
  "$BASE/api/v1/admin/players/<player-id>/content/dev-menu" \
  -H "Authorization: Bearer $ADMIN" -H "Content-Type: application/json" \
  -d "{\"asset_revision_id\":\"$REVISION_ID\"}"

# On the enrolled appliance:
signage-controller content sync
signage-controller content status
```

Confirm that mpv transitions to the hash-named cached file, disconnect WAN,
and confirm the same video keeps looping. Assignment removal uses `DELETE` on
the assignment URL. It increments the manifest revision and returns playback
to the YAML media path only when that fallback exists.

## Security notes

- The device token and pairing code are stored only as SHA-256 hashes
  (`player_credentials.token_hash`, `enrollments.device_token_hash`,
  `enrollments.code_hash`) -- plaintext is never persisted and never appears
  in any response.
- Pairing-code hashing with a fast hash (SHA-256, not a slow KDF) is
  deliberate: `GET/POST /api/v1/admin/enrollments/{code}...` require a valid
  admin bearer token first, so brute-forcing a code requires already holding
  the admin credential -- a materially different threat model than a public
  endpoint.
- All secret comparisons (admin token, device token hash, code hash) use
  `MessageDigest.isEqual` via `security/ConstantTime.kt`.
- `SCREENKEEPER_ADMIN_TOKEN` has no default; startup fails with a clear
  message if it's absent, before any socket or DB connection is opened.
- This is MVP-only administration authentication: a single static bearer
  token. No users, OAuth, or RBAC in this phase.

## Deliberately out of scope for this phase

Object deletion, media proxying, cache garbage collection, playlists,
schedules, multipart administration, NATS/JetStream, playback analytics, and
any form of remote command execution. See
`docs/architecture/control-plane.md` for the full list and rationale.
