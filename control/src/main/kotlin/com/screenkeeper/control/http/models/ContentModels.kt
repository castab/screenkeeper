package com.screenkeeper.control.http.models

import kotlinx.datetime.Instant
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class CreateAssetRequestDto(val name: String)

@Serializable
data class CreateAssetRevisionRequestDto(
    @SerialName("original_filename") val originalFilename: String,
    @SerialName("content_type") val contentType: String,
    @SerialName("byte_size") val byteSize: Long,
    val sha256: String,
)

@Serializable
data class AssignContentRequestDto(
    @SerialName("asset_revision_id") val assetRevisionId: String,
)

@Serializable
data class MediaAssetRevisionDto(
    val id: String,
    val revision: Int,
    @SerialName("original_filename") val originalFilename: String,
    @SerialName("content_type") val contentType: String,
    @SerialName("byte_size") val byteSize: Long,
    val sha256: String,
    val status: String,
    @SerialName("created_at") val createdAt: Instant,
    @SerialName("available_at") val availableAt: Instant? = null,
)

@Serializable
data class MediaAssetDto(
    val id: String,
    val name: String,
    @SerialName("created_at") val createdAt: Instant,
    val revisions: List<MediaAssetRevisionDto> = emptyList(),
)

@Serializable
data class UploadRequestDto(
    val required: Boolean,
    val method: String? = null,
    val url: String? = null,
    val headers: Map<String, String> = emptyMap(),
    @SerialName("expires_at") val expiresAt: Instant? = null,
)

@Serializable
data class CreateAssetRevisionResponseDto(
    @SerialName("asset_revision_id") val assetRevisionId: String,
    val revision: Int,
    val status: String,
    val upload: UploadRequestDto,
)

@Serializable
data class DesiredAssetDto(
    val kind: String = "asset",
    @SerialName("asset_id") val assetId: String,
    @SerialName("asset_revision_id") val assetRevisionId: String,
    val revision: Int,
    @SerialName("original_filename") val originalFilename: String,
    @SerialName("content_type") val contentType: String,
    @SerialName("byte_size") val byteSize: Long,
    val sha256: String,
    @SerialName("download_url") val downloadUrl: String,
)

@Serializable
data class PlayerContentAssignmentDto(
    @SerialName("player_id") val playerId: String,
    val content: DesiredAssetDto,
)

@Serializable
data class ContentManifestDto(
    val revision: Long,
    val players: List<PlayerContentAssignmentDto>,
)

@Serializable
data class PlayerContentStatusDto(
    @SerialName("player_id") val playerId: String,
    val status: String,
    @SerialName("desired_asset_revision_id") val desiredAssetRevisionId: String? = null,
    @SerialName("cached_asset_revision_id") val cachedAssetRevisionId: String? = null,
    @SerialName("active_asset_revision_id") val activeAssetRevisionId: String? = null,
    @SerialName("playing_asset_revision_id") val playingAssetRevisionId: String? = null,
    @SerialName("last_sync_at") val lastSyncAt: Instant? = null,
    @SerialName("last_error") val lastError: String? = null,
)

@Serializable
data class ContentStatusReportDto(
    @SerialName("reported_at") val reportedAt: Instant,
    @SerialName("manifest_revision") val manifestRevision: Long,
    val players: List<PlayerContentStatusDto>,
)

@Serializable
data class AssignmentResultDto(val revision: Long)
