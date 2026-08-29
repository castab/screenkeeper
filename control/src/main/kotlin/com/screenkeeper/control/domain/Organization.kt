package com.screenkeeper.control.domain

import java.time.Instant
import java.util.UUID

data class Organization(
    val id: UUID,
    val name: String,
    val createdAt: Instant,
)
