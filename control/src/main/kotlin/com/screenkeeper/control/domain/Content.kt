package com.screenkeeper.control.domain

import java.time.Instant
import java.util.UUID

data class MediaAsset(
    val id: UUID,
    val name: String,
    val createdAt: Instant,
)

data class MediaAssetRevision(
    val id: UUID,
    val assetId: UUID,
    val revision: Int,
    val originalFilename: String,
    val contentType: String,
    val byteSize: Long,
    val sha256: String,
    val storageKey: String,
    val status: String,
    val createdAt: Instant,
    val availableAt: Instant?,
)

data class ContentAssignment(
    val playerId: UUID,
    val playbackPlayerId: String,
    val assetRevision: MediaAssetRevision,
)
