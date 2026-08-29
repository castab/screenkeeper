package com.screenkeeper.control.persistence.repository

import com.screenkeeper.control.domain.Player
import org.jdbi.v3.core.Handle
import org.jdbi.v3.core.kotlin.mapTo
import java.time.Instant
import java.util.UUID

private const val COLUMNS = """
    id, installation_id AS installationId, location_id AS locationId, name,
    screenkeeper_version AS screenkeeperVersion, hostname, last_seen_at AS lastSeenAt,
    created_at AS createdAt, updated_at AS updatedAt
"""

private val GENERATED_KEY_COLUMNS = arrayOf(
    "id", "installation_id", "location_id", "name", "screenkeeper_version",
    "hostname", "last_seen_at", "created_at", "updated_at",
)

class PlayerRepository {
    fun existsByInstallationId(handle: Handle, installationId: UUID): Boolean =
        handle.createQuery("SELECT EXISTS(SELECT 1 FROM players WHERE installation_id = :installationId)")
            .bind("installationId", installationId)
            .mapTo<Boolean>()
            .one()

    fun insert(
        handle: Handle,
        installationId: UUID,
        locationId: UUID,
        name: String,
        screenkeeperVersion: String?,
        hostname: String?,
    ): Player =
        handle.createUpdate(
            """
            INSERT INTO players (installation_id, location_id, name, screenkeeper_version, hostname)
            VALUES (:installationId, :locationId, :name, :screenkeeperVersion, :hostname)
            """.trimIndent(),
        )
            .bind("installationId", installationId)
            .bind("locationId", locationId)
            .bind("name", name)
            .bind("screenkeeperVersion", screenkeeperVersion)
            .bind("hostname", hostname)
            .executeAndReturnGeneratedKeys(*GENERATED_KEY_COLUMNS)
            .mapTo<Player>()
            .one()

    fun findById(handle: Handle, id: UUID): Player? =
        handle.createQuery("SELECT $COLUMNS FROM players WHERE id = :id")
            .bind("id", id)
            .mapTo<Player>()
            .findFirst()
            .orElse(null)

    fun touch(handle: Handle, playerId: UUID, screenkeeperVersion: String, hostname: String, now: Instant) {
        handle.createUpdate(
            """
            UPDATE players
            SET last_seen_at = :now, screenkeeper_version = :screenkeeperVersion, hostname = :hostname, updated_at = :now
            WHERE id = :playerId
            """.trimIndent(),
        )
            .bind("playerId", playerId)
            .bind("screenkeeperVersion", screenkeeperVersion)
            .bind("hostname", hostname)
            .bind("now", now)
            .execute()
    }

    fun list(handle: Handle): List<Player> =
        handle.createQuery("SELECT $COLUMNS FROM players ORDER BY created_at")
            .mapTo<Player>()
            .list()
}
