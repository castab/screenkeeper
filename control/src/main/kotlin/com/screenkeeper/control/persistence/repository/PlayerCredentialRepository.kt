package com.screenkeeper.control.persistence.repository

import com.screenkeeper.control.domain.PlayerCredential
import org.jdbi.v3.core.Handle
import org.jdbi.v3.core.kotlin.mapTo
import java.util.UUID

private const val COLUMNS = "player_id AS playerId, token_hash AS tokenHash, created_at AS createdAt, revoked_at AS revokedAt"

class PlayerCredentialRepository {
    fun insert(handle: Handle, playerId: UUID, tokenHash: ByteArray): PlayerCredential =
        handle.createUpdate("INSERT INTO player_credentials (player_id, token_hash) VALUES (:playerId, :tokenHash)")
            .bind("playerId", playerId)
            .bind("tokenHash", tokenHash)
            .executeAndReturnGeneratedKeys("player_id", "token_hash", "created_at", "revoked_at")
            .mapTo<PlayerCredential>()
            .one()

    fun findByTokenHash(handle: Handle, tokenHash: ByteArray): PlayerCredential? =
        handle.createQuery("SELECT $COLUMNS FROM player_credentials WHERE token_hash = :tokenHash")
            .bind("tokenHash", tokenHash)
            .mapTo<PlayerCredential>()
            .findFirst()
            .orElse(null)

    fun findByPlayerId(handle: Handle, playerId: UUID): PlayerCredential? =
        handle.createQuery("SELECT $COLUMNS FROM player_credentials WHERE player_id = :playerId")
            .bind("playerId", playerId)
            .mapTo<PlayerCredential>()
            .findFirst()
            .orElse(null)
}
