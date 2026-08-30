package com.screenkeeper.control.persistence.repository

import com.screenkeeper.control.domain.ContentAssignment
import com.screenkeeper.control.domain.MediaAssetRevision
import org.jdbi.v3.core.Handle
import java.time.Instant
import java.util.UUID

class ContentRepository {
    fun assignedRevisionId(handle: Handle, playerId: UUID, playbackPlayerId: String): UUID? =
        handle.createQuery(
            "SELECT asset_revision_id FROM player_content_assignments " +
                "WHERE player_id = :playerId AND playback_player_id = :playbackPlayerId",
        )
            .bind("playerId", playerId)
            .bind("playbackPlayerId", playbackPlayerId)
            .mapTo(UUID::class.java)
            .findFirst()
            .orElse(null)

    fun manifestRevision(handle: Handle, playerId: UUID): Long =
        handle.createQuery("SELECT revision FROM player_content_manifests WHERE player_id = :playerId")
            .bind("playerId", playerId)
            .mapTo(Long::class.java)
            .findFirst()
            .orElse(0L)

    fun bumpManifest(handle: Handle, playerId: UUID, now: Instant): Long =
        handle.createUpdate(
            """
            INSERT INTO player_content_manifests (player_id, revision, updated_at)
            VALUES (:playerId, 1, :now)
            ON CONFLICT (player_id) DO UPDATE
            SET revision = player_content_manifests.revision + 1, updated_at = EXCLUDED.updated_at
            """.trimIndent(),
        )
            .bind("playerId", playerId)
            .bind("now", now)
            .executeAndReturnGeneratedKeys("revision")
            .mapTo(Long::class.java)
            .one()

    fun assign(
        handle: Handle,
        playerId: UUID,
        playbackPlayerId: String,
        assetRevisionId: UUID,
        now: Instant,
    ) {
        handle.createUpdate(
            """
            INSERT INTO player_content_assignments
                (player_id, playback_player_id, asset_revision_id, assigned_at)
            VALUES (:playerId, :playbackPlayerId, :assetRevisionId, :now)
            ON CONFLICT (player_id, playback_player_id) DO UPDATE SET
                asset_revision_id = EXCLUDED.asset_revision_id,
                assigned_at = EXCLUDED.assigned_at
            """.trimIndent(),
        )
            .bind("playerId", playerId)
            .bind("playbackPlayerId", playbackPlayerId)
            .bind("assetRevisionId", assetRevisionId)
            .bind("now", now)
            .execute()
    }

    fun clear(handle: Handle, playerId: UUID, playbackPlayerId: String): Boolean =
        handle.createUpdate(
            "DELETE FROM player_content_assignments WHERE player_id = :playerId " +
                "AND playback_player_id = :playbackPlayerId",
        )
            .bind("playerId", playerId)
            .bind("playbackPlayerId", playbackPlayerId)
            .execute() > 0

    fun assignments(handle: Handle, playerId: UUID): List<ContentAssignment> =
        handle.createQuery(
            """
            SELECT a.player_id, a.playback_player_id,
                   r.id, r.asset_id, r.revision, r.original_filename, r.content_type,
                   r.byte_size, r.sha256, r.storage_key, r.status, r.created_at, r.available_at
            FROM player_content_assignments a
            JOIN media_asset_revisions r ON r.id = a.asset_revision_id
            WHERE a.player_id = :playerId
            ORDER BY a.playback_player_id
            """.trimIndent(),
        )
            .bind("playerId", playerId)
            .map { row, _ ->
                ContentAssignment(
                    playerId = row.getObject("player_id", UUID::class.java),
                    playbackPlayerId = row.getString("playback_player_id"),
                    assetRevision = MediaAssetRevision(
                        id = row.getObject("id", UUID::class.java),
                        assetId = row.getObject("asset_id", UUID::class.java),
                        revision = row.getInt("revision"),
                        originalFilename = row.getString("original_filename"),
                        contentType = row.getString("content_type"),
                        byteSize = row.getLong("byte_size"),
                        sha256 = row.getString("sha256").trim(),
                        storageKey = row.getString("storage_key"),
                        status = row.getString("status"),
                        createdAt = row.getTimestamp("created_at").toInstant(),
                        availableAt = row.getTimestamp("available_at")?.toInstant(),
                    ),
                )
            }
            .list()

    fun upsertReport(
        handle: Handle,
        playerId: UUID,
        reportedAt: Instant,
        receivedAt: Instant,
        reportJson: String,
    ) {
        handle.createUpdate(
            """
            INSERT INTO player_content_reports (player_id, reported_at, received_at, report)
            VALUES (:playerId, :reportedAt, :receivedAt, CAST(:reportJson AS jsonb))
            ON CONFLICT (player_id) DO UPDATE SET
                reported_at = EXCLUDED.reported_at,
                received_at = EXCLUDED.received_at,
                report = EXCLUDED.report
            """.trimIndent(),
        )
            .bind("playerId", playerId)
            .bind("reportedAt", reportedAt)
            .bind("receivedAt", receivedAt)
            .bind("reportJson", reportJson)
            .execute()
    }

    fun reportJson(handle: Handle, playerId: UUID): String? =
        handle.createQuery("SELECT report::text FROM player_content_reports WHERE player_id = :playerId")
            .bind("playerId", playerId)
            .mapTo(String::class.java)
            .findFirst()
            .orElse(null)
}
