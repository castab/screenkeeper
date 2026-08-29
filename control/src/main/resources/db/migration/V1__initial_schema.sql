-- Screenkeeper Control -- initial schema.
-- Flyway owns this schema exclusively. Jdbi performs no DDL and no
-- auto-generation; every schema change is a new Flyway migration.
--
-- Requires PostgreSQL 13+ (gen_random_uuid() is built into core as of PG13,
-- no pgcrypto extension needed). Dev/CI/prod target PostgreSQL 18+.

CREATE TABLE organizations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE locations (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id  UUID NOT NULL REFERENCES organizations (id),
    name             TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_locations_organization_id ON locations (organization_id);

CREATE TABLE players (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    installation_id       UUID NOT NULL,
    location_id           UUID NOT NULL REFERENCES locations (id),
    name                  TEXT NOT NULL,
    screenkeeper_version  TEXT,
    hostname              TEXT,
    last_seen_at          TIMESTAMPTZ,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX ux_players_installation_id ON players (installation_id);
CREATE INDEX ix_players_location_id ON players (location_id);

CREATE TABLE player_credentials (
    player_id   UUID PRIMARY KEY REFERENCES players (id),
    token_hash  BYTEA NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at  TIMESTAMPTZ
);

-- Bearer-token lookup on every heartbeat is by hash; must be indexed and
-- unique (two players must never share a device token).
CREATE UNIQUE INDEX ux_player_credentials_token_hash ON player_credentials (token_hash);

CREATE TABLE enrollments (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    installation_id       UUID NOT NULL,
    device_token_hash     BYTEA NOT NULL,
    code_hash             BYTEA NOT NULL,
    expires_at            TIMESTAMPTZ NOT NULL,
    claimed_at            TIMESTAMPTZ,
    player_id             UUID REFERENCES players (id),
    screenkeeper_version  TEXT,
    hostname              TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_enrollments_installation_id ON enrollments (installation_id);
CREATE INDEX ix_enrollments_expires_at ON enrollments (expires_at);
CREATE INDEX ix_enrollments_player_id ON enrollments (player_id);
CREATE INDEX ix_enrollments_code_hash ON enrollments (code_hash);

-- Enforces "one active pending enrollment per installation_id". A second
-- POST /enrollments for the same installation_id while a pending row exists
-- hits this unique index via INSERT ... ON CONFLICT (installation_id)
-- WHERE claimed_at IS NULL DO UPDATE ..., which atomically supersedes the
-- code/device_token_hash/expires_at on the SAME row (same enrollment_id),
-- race-safely, with no explicit locking required. Claimed enrollments are
-- excluded from this constraint so claim history is never blocked.
CREATE UNIQUE INDEX ux_enrollments_pending_installation_id
    ON enrollments (installation_id)
    WHERE claimed_at IS NULL;

CREATE TABLE player_reports (
    player_id    UUID PRIMARY KEY REFERENCES players (id),
    reported_at  TIMESTAMPTZ NOT NULL,
    received_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    report       JSONB NOT NULL
);
