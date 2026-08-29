package com.screenkeeper.control.domain

import java.time.Instant
import java.util.UUID

data class Player(
    val id: UUID,
    val installationId: UUID,
    val locationId: UUID,
    val name: String,
    val screenkeeperVersion: String?,
    val hostname: String?,
    val lastSeenAt: Instant?,
    val createdAt: Instant,
    val updatedAt: Instant,
)
