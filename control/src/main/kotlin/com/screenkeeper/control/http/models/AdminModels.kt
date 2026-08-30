package com.screenkeeper.control.http.models

import kotlinx.datetime.Instant
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

@Serializable
data class CreateOrganizationRequestDto(val name: String)

@Serializable
data class OrganizationDto(
    val id: String,
    val name: String,
    @SerialName("created_at") val createdAt: Instant,
)

@Serializable
data class CreateLocationRequestDto(
    @SerialName("organization_id") val organizationId: String,
    val name: String,
)

@Serializable
data class LocationDto(
    val id: String,
    @SerialName("organization_id") val organizationId: String,
    val name: String,
    @SerialName("created_at") val createdAt: Instant,
)

@Serializable
data class ClaimRequestDto(
    @SerialName("location_id") val locationId: String,
    val name: String,
)

@Serializable
data class ClaimResponseDto(
    @SerialName("player_id") val playerId: String,
    @SerialName("player_name") val playerName: String,
    val organization: NamedEntityDto,
    val location: NamedEntityDto,
)

@Serializable
data class EnrollmentLookupDto(
    @SerialName("enrollment_id") val enrollmentId: String,
    @SerialName("installation_id") val installationId: String,
    val status: String,
    @SerialName("expires_at") val expiresAt: Instant,
    @SerialName("screenkeeper_version") val screenkeeperVersion: String? = null,
    val hostname: String? = null,
)

@Serializable
data class PlayerSummaryDto(
    val id: String,
    val name: String,
    val organization: NamedEntityDto,
    val location: NamedEntityDto,
    val online: Boolean,
    @SerialName("last_seen_at") val lastSeenAt: Instant? = null,
)

@Serializable
data class PlayerDetailDto(
    val id: String,
    val name: String,
    @SerialName("installation_id") val installationId: String,
    val organization: NamedEntityDto,
    val location: NamedEntityDto,
    val online: Boolean,
    @SerialName("last_seen_at") val lastSeenAt: Instant? = null,
    @SerialName("screenkeeper_version") val screenkeeperVersion: String? = null,
    @SerialName("latest_report") val latestReport: JsonElement? = null,
    @SerialName("content_status") val contentStatus: JsonElement? = null,
)
