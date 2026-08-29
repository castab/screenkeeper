package com.screenkeeper.control.domain

import java.time.Instant
import java.util.UUID

data class Location(
    val id: UUID,
    val organizationId: UUID,
    val name: String,
    val createdAt: Instant,
)
