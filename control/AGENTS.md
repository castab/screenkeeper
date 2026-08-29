# AGENTS.md (control/)

## Project Purpose

Screenkeeper Control is the cloud/server-side counterpart to the Screenkeeper
Edge player at the repository root. It implements the server side of the
shared contract at `contracts/openapi.yaml`: appliance enrollment via a
short pairing code, heartbeat reporting of observed display hardware and
configured bindings, and a small admin API for organizations, locations,
and claiming enrollments. See the root `AGENTS.md` for the monorepo
boundary and `contracts/README.md` for the compatibility rules.

Read `README.md` in this directory before changing configuration, routes,
or the database schema.

## Technology invariants

- **http4k is the only HTTP server framework.** Do not introduce Spring,
  Ktor, Micronaut, Quarkus, Vert.x, or any other server framework.
- **Jdbi is the only persistence abstraction.** Do not introduce Hibernate,
  JPA, Exposed, jOOQ, Spring Data, or any ORM/query-builder layer.
- **Flyway owns schema evolution exclusively.** Do not let Jdbi create or
  alter tables. Every schema change is a new `V<n>__description.sql` file
  under `src/main/resources/db/migration/`; never edit an already-applied
  migration to change production semantics.
- **Kotest is the only test framework.** Fast unit tests live under
  `src/test/kotlin` (no database). Postgres-backed tests live under
  `src/testIntegration/kotlin`, run via `./gradlew integrationTest` against
  an externally provided Postgres (env vars `SCREENKEEPER_TEST_DATABASE_*`)
  -- **no Testcontainers**, deliberately, for CI reliability. See
  `README.md` for exact commands.
- **OkHttp is reserved, not wired in.** Do not add a speculative outbound
  HTTP client. If a real outbound integration is added later, use OkHttp
  with explicit timeouts, a bounded connection pool, and MockWebServer
  tests -- never for calling the local database or replacing direct
  application-service calls in tests.
- Kotlin DSL for Gradle, a version catalog (`gradle/libs.versions.toml`),
  and explicit dependency versions -- no dynamic/`+` versions.

## Architectural rules

- Layering is `http -> application -> persistence`. HTTP route handlers do
  not talk to Jdbi directly; they call an application service.
- Application services own transaction boundaries via
  `Jdbi.inTransaction`/`useTransaction`/`withHandle`. Repositories are plain
  classes taking a `Handle` per call, not SQL Object interfaces.
- The two multi-step flows -- create-or-reuse enrollment, and claim -- must
  each run in one database transaction.
- Never generate a client from the Screenkeeper Edge Python code, and never
  import the edge's modules. The shared boundary is `contracts/openapi.yaml`
  only.
- Do not import or depend on the Screenkeeper Edge player's Python package
  in any way.
- Within `/api/v1`, evolve the contract additively only: tolerate unknown
  request fields, never remove/rename/narrow a field. A breaking change
  means a new `/api/v2` prefix. Change `contracts/openapi.yaml` and
  `contracts/examples/*.json` before changing this side's code (see the
  root `AGENTS.md`).
- Do not add empty abstractions (interfaces with one implementation, unused
  extension points) ahead of an actual second use case.

## Security invariants

- Never store, log, or return a plaintext device token or pairing code.
  Only `*_hash` columns exist for these; there are no plaintext columns to
  accidentally select.
- Never log `SCREENKEEPER_ADMIN_TOKEN`, `SCREENKEEPER_DATABASE_PASSWORD`, a
  device token, a pairing code, or a credential hash. `AppConfig.toString()`
  is deliberately hand-written to exclude secrets rather than relying on the
  data class default.
- All secret comparisons use `security/ConstantTime.equals` (backed by
  `MessageDigest.isEqual`), never `==`/`contentEquals` on raw bytes/strings.
- `SCREENKEEPER_ADMIN_TOKEN` must never have a default value. Missing it is
  a hard startup failure with a clear message, before any socket or DB
  connection opens.
- Do not add users, OAuth, SSO, or RBAC in this phase -- the single static
  admin bearer token is a deliberate MVP choice, documented as such.
- Response bodies never include a stack trace or raw SQL error detail
  (`http/filters/ErrorHandlingFilter.kt` is the one place that maps failures
  to the wire `{error, message}` shape; full detail is logged server-side
  only).

## Scope invariants

Do not implement, even as a stub or placeholder table: Asset, object
storage, Deployment, Playlist, Schedule, Manifest, download URLs, NATS,
JetStream, or any remote command/shell/arbitrary-subprocess mechanism. This
product is a desired-state system, not a remote-access tool. These are
explicitly deferred to a later phase -- see
`docs/architecture/control-plane.md`.

## Containerization

Unlike the Screenkeeper Edge player (host-native, never containerized),
Screenkeeper Control **is** expected to run in Docker for development and
deployment (`Dockerfile`, `compose.dev.yaml`). `compose.dev.yaml` must only
ever contain Postgres and Screenkeeper Control -- never the edge/mpv player.
