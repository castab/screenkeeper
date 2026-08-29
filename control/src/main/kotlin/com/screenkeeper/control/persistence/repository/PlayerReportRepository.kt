package com.screenkeeper.control.persistence.repository

import com.screenkeeper.control.domain.PlayerReport
import org.jdbi.v3.core.Handle
import org.jdbi.v3.core.kotlin.mapTo
import java.time.Instant
import java.util.UUID

private const val COLUMNS =
    "player_id AS playerId, reported_at AS reportedAt, received_at AS receivedAt, report::text AS reportJson"

class PlayerReportRepository {
    /** The latest report replaces the previous one -- no history is kept in this phase. */
    fun upsert(handle: Handle, playerId: UUID, reportedAt: Instant, receivedAt: Instant, reportJson: String) {
        handle.createUpdate(
            """
            INSERT INTO player_reports (player_id, reported_at, received_at, report)
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

    fun findByPlayerId(handle: Handle, playerId: UUID): PlayerReport? =
        handle.createQuery("SELECT $COLUMNS FROM player_reports WHERE player_id = :playerId")
            .bind("playerId", playerId)
            .mapTo<PlayerReport>()
            .findFirst()
            .orElse(null)
}
