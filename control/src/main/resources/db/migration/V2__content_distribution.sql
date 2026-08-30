CREATE TABLE media_assets (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE media_asset_revisions (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    asset_id           UUID NOT NULL REFERENCES media_assets (id),
    revision           INTEGER NOT NULL CHECK (revision > 0),
    original_filename  TEXT NOT NULL,
    content_type       TEXT NOT NULL CHECK (content_type LIKE 'video/%'),
    byte_size          BIGINT NOT NULL CHECK (byte_size > 0),
    sha256             CHAR(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    storage_key        TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'available')),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    available_at       TIMESTAMPTZ,
    UNIQUE (asset_id, revision)
);

CREATE INDEX ix_media_asset_revisions_asset_id ON media_asset_revisions (asset_id);
CREATE INDEX ix_media_asset_revisions_sha256 ON media_asset_revisions (sha256);

CREATE TABLE player_content_manifests (
    player_id   UUID PRIMARY KEY REFERENCES players (id) ON DELETE CASCADE,
    revision    BIGINT NOT NULL DEFAULT 0 CHECK (revision >= 0),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE player_content_assignments (
    player_id           UUID NOT NULL REFERENCES players (id) ON DELETE CASCADE,
    playback_player_id  TEXT NOT NULL,
    asset_revision_id   UUID NOT NULL REFERENCES media_asset_revisions (id),
    assigned_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (player_id, playback_player_id)
);

CREATE INDEX ix_player_content_assignments_revision ON player_content_assignments (asset_revision_id);

CREATE TABLE player_content_reports (
    player_id    UUID PRIMARY KEY REFERENCES players (id) ON DELETE CASCADE,
    reported_at  TIMESTAMPTZ NOT NULL,
    received_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    report       JSONB NOT NULL
);
