package com.screenkeeper.control.persistence.repository

import com.screenkeeper.control.domain.Enrollment
import org.jdbi.v3.core.Handle
import org.jdbi.v3.core.kotlin.mapTo
import java.time.Instant
import java.util.UUID

private const val COLUMNS = """
    id, installation_id AS installationId, device_token_hash AS deviceTokenHash,
    code_hash AS codeHash, expires_at AS expiresAt, claimed_at AS claimedAt,
    player_id AS playerId, screenkeeper_version AS screenkeeperVersion, hostname,
    created_at AS createdAt
"""

class EnrollmentRepository {
    /**
     * Creates a pending enrollment, or -- if a pending (unclaimed) enrollment
     * already exists for this installation_id -- supersedes it in place: same
     * enrollment_id, fresh code_hash/device_token_hash/expires_at. Atomic via
     * the partial unique index on (installation_id) WHERE claimed_at IS NULL,
     * so concurrent duplicate requests can never create two pending rows or
     * race each other into an inconsistent state.
     */
    fun upsertPending(
        handle: Handle,
        installationId: UUID,
        deviceTokenHash: ByteArray,
        codeHash: ByteArray,
        expiresAt: Instant,
        screenkeeperVersion: String,
        hostname: String,
    ): Enrollment =
        handle.createQuery(
            """
            INSERT INTO enrollments (installation_id, device_token_hash, code_hash, expires_at, screenkeeper_version, hostname)
            VALUES (:installationId, :deviceTokenHash, :codeHash, :expiresAt, :screenkeeperVersion, :hostname)
            ON CONFLICT (installation_id) WHERE claimed_at IS NULL DO UPDATE SET
                device_token_hash = EXCLUDED.device_token_hash,
                code_hash = EXCLUDED.code_hash,
                expires_at = EXCLUDED.expires_at,
                screenkeeper_version = EXCLUDED.screenkeeper_version,
                hostname = EXCLUDED.hostname
            RETURNING $COLUMNS
            """.trimIndent(),
        )
            .bind("installationId", installationId)
            .bind("deviceTokenHash", deviceTokenHash)
            .bind("codeHash", codeHash)
            .bind("expiresAt", expiresAt)
            .bind("screenkeeperVersion", screenkeeperVersion)
            .bind("hostname", hostname)
            .mapTo<Enrollment>()
            .one()

    fun findById(handle: Handle, id: UUID): Enrollment? =
        handle.createQuery("SELECT $COLUMNS FROM enrollments WHERE id = :id")
            .bind("id", id)
            .mapTo<Enrollment>()
            .findFirst()
            .orElse(null)

    fun findByCodeHash(handle: Handle, codeHash: ByteArray): Enrollment? =
        handle.createQuery("SELECT $COLUMNS FROM enrollments WHERE code_hash = :codeHash")
            .bind("codeHash", codeHash)
            .mapTo<Enrollment>()
            .findFirst()
            .orElse(null)

    fun markClaimed(handle: Handle, enrollmentId: UUID, playerId: UUID, claimedAt: Instant) {
        handle.createUpdate(
            "UPDATE enrollments SET claimed_at = :claimedAt, player_id = :playerId WHERE id = :enrollmentId",
        )
            .bind("enrollmentId", enrollmentId)
            .bind("playerId", playerId)
            .bind("claimedAt", claimedAt)
            .execute()
    }

    /** Reaper support: deletes pending rows that expired before [cutoff]. Returns the number removed. */
    fun deleteExpiredPendingBefore(handle: Handle, cutoff: Instant): Int =
        handle.createUpdate("DELETE FROM enrollments WHERE claimed_at IS NULL AND expires_at < :cutoff")
            .bind("cutoff", cutoff)
            .execute()
}
