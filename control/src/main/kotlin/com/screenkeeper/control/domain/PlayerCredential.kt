package com.screenkeeper.control.domain

import java.time.Instant
import java.util.UUID

data class PlayerCredential(
    val playerId: UUID,
    val tokenHash: ByteArray,
    val createdAt: Instant,
    val revokedAt: Instant?,
) {
    val isRevoked: Boolean get() = revokedAt != null

    override fun equals(other: Any?): Boolean {
        if (this === other) return true
        if (other !is PlayerCredential) return false
        return playerId == other.playerId &&
            tokenHash.contentEquals(other.tokenHash) &&
            createdAt == other.createdAt &&
            revokedAt == other.revokedAt
    }

    override fun hashCode(): Int {
        var result = playerId.hashCode()
        result = 31 * result + tokenHash.contentHashCode()
        result = 31 * result + createdAt.hashCode()
        result = 31 * result + (revokedAt?.hashCode() ?: 0)
        return result
    }
}
