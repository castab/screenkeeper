package com.screenkeeper.control.domain

import java.time.Instant
import java.util.UUID

data class Enrollment(
    val id: UUID,
    val installationId: UUID,
    val deviceTokenHash: ByteArray,
    val codeHash: ByteArray,
    val expiresAt: Instant,
    val claimedAt: Instant?,
    val playerId: UUID?,
    val screenkeeperVersion: String?,
    val hostname: String?,
    val createdAt: Instant,
) {
    val isClaimed: Boolean get() = claimedAt != null

    fun isExpiredAt(now: Instant): Boolean = expiresAt.isBefore(now)

    override fun equals(other: Any?): Boolean {
        if (this === other) return true
        if (other !is Enrollment) return false
        return id == other.id &&
            installationId == other.installationId &&
            deviceTokenHash.contentEquals(other.deviceTokenHash) &&
            codeHash.contentEquals(other.codeHash) &&
            expiresAt == other.expiresAt &&
            claimedAt == other.claimedAt &&
            playerId == other.playerId &&
            screenkeeperVersion == other.screenkeeperVersion &&
            hostname == other.hostname &&
            createdAt == other.createdAt
    }

    override fun hashCode(): Int {
        var result = id.hashCode()
        result = 31 * result + installationId.hashCode()
        result = 31 * result + deviceTokenHash.contentHashCode()
        result = 31 * result + codeHash.contentHashCode()
        result = 31 * result + expiresAt.hashCode()
        result = 31 * result + (claimedAt?.hashCode() ?: 0)
        result = 31 * result + (playerId?.hashCode() ?: 0)
        return result
    }
}
