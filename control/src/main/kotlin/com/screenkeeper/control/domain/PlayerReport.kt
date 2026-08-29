package com.screenkeeper.control.domain

import java.time.Instant
import java.util.UUID

/** [reportJson] is the raw, byte-for-byte heartbeat body as received -- never
 *  re-derived from a decoded DTO, so genuinely unknown fields survive for
 *  display through the admin API even though the server doesn't understand them. */
data class PlayerReport(
    val playerId: UUID,
    val reportedAt: Instant,
    val receivedAt: Instant,
    val reportJson: String,
)
