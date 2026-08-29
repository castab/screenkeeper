package com.screenkeeper.control.security

import com.screenkeeper.control.domain.Player
import com.screenkeeper.control.domain.PlayerCredential
import com.screenkeeper.control.http.RequestContext
import com.screenkeeper.control.http.models.errorResponse
import org.http4k.core.Filter
import org.http4k.core.Request
import org.http4k.core.Status
import org.http4k.core.with
import org.http4k.lens.Path
import java.util.UUID

private val playerIdPath = Path.of("player_id")

fun Request.bearerToken(): String? {
    val header = header("Authorization") ?: return null
    return if (header.startsWith("Bearer ")) header.removePrefix("Bearer ") else null
}

/**
 * Guards POST /api/v1/players/{player_id}/heartbeat. Ordering: unknown
 * player is 404, missing/mismatched bearer is 401, and a valid token whose
 * credential has been revoked is 403 -- the only case where 403 is
 * meaningful here, since credentials are 1:1 with players.
 *
 * [resolvePlayer]/[resolveCredential] are plain lookup functions rather than
 * a direct Jdbi/repository dependency so this filter's branching logic can
 * be unit tested against a fake with no database involved.
 */
fun PlayerCredentialFilter(
    resolvePlayer: (UUID) -> Player?,
    resolveCredential: (UUID) -> PlayerCredential?,
): Filter = Filter { next ->
    { request ->
        val playerId = runCatching { UUID.fromString(playerIdPath(request)) }.getOrNull()
        val player = playerId?.let(resolvePlayer)
        val credential = playerId?.let(resolveCredential)
        val bearer = request.bearerToken()

        when {
            player == null || credential == null ->
                errorResponse(Status.NOT_FOUND, "player_not_found", "Unknown player.")
            bearer == null || !ConstantTime.equals(sha256(bearer), credential.tokenHash) ->
                errorResponse(Status.UNAUTHORIZED, "invalid_credential", "Missing or invalid credential.")
            credential.isRevoked ->
                errorResponse(Status.FORBIDDEN, "revoked_credential", "This credential has been revoked.")
            else ->
                next(request.with(RequestContext.playerKey of player))
        }
    }
}
