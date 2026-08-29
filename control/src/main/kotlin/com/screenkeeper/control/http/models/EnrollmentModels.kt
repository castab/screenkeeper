package com.screenkeeper.control.http.models

import kotlinx.datetime.Instant
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class EnrollmentRequestDto(
    @SerialName("installation_id") val installationId: String,
    @SerialName("device_token") val deviceToken: String,
    @SerialName("screenkeeper_version") val screenkeeperVersion: String,
    val hostname: String,
)

@Serializable
data class EnrollmentCreatedDto(
    @SerialName("enrollment_id") val enrollmentId: String,
    val code: String,
    @SerialName("expires_at") val expiresAt: Instant,
    @SerialName("poll_interval_seconds") val pollIntervalSeconds: Int,
)

@Serializable
data class NamedEntityDto(
    val id: String,
    val name: String,
)

@Serializable
data class EnrollmentStateDto(
    val status: String,
    @SerialName("player_id") val playerId: String? = null,
    @SerialName("player_name") val playerName: String? = null,
    val organization: NamedEntityDto? = null,
    val location: NamedEntityDto? = null,
)
