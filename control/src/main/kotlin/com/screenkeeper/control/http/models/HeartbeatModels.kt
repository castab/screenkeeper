package com.screenkeeper.control.http.models

import kotlinx.datetime.Instant
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class AgentInfoDto(
    @SerialName("screenkeeper_version") val screenkeeperVersion: String,
    val hostname: String,
    val os: String,
    val kernel: String? = null,
    val architecture: String? = null,
)

@Serializable
data class EdidDto(
    val sha256: String,
    val manufacturer: String? = null,
    @SerialName("product_code") val productCode: Int? = null,
    @SerialName("product_name") val productName: String? = null,
    val serial: String? = null,
)

@Serializable
data class ConnectorDto(
    val name: String,
    val status: String? = null,
    val enabled: Boolean? = null,
    val modes: List<String> = emptyList(),
    val edid: EdidDto? = null,
)

@Serializable
data class GpuDto(
    val card: String,
    @SerialName("vendor_id") val vendorId: String? = null,
    @SerialName("device_id") val deviceId: String? = null,
    val driver: String? = null,
)

@Serializable
data class InventoryDto(
    val gpus: List<GpuDto> = emptyList(),
    val connectors: List<ConnectorDto> = emptyList(),
)

@Serializable
data class ConfiguredTvDto(
    val id: String,
    val name: String,
    val host: String,
    @SerialName("desired_input") val desiredInput: String,
    @SerialName("desired_volume") val desiredVolume: Int,
    val driver: String,
)

@Serializable
data class ConfiguredPlaybackPlayerDto(
    val id: String,
    val name: String,
    @SerialName("tv_id") val tvId: String? = null,
    val screen: Int? = null,
    @SerialName("screen_name") val screenName: String? = null,
)

@Serializable
data class ConfiguredBindingsDto(
    val tvs: List<ConfiguredTvDto> = emptyList(),
    @SerialName("playback_players") val playbackPlayers: List<ConfiguredPlaybackPlayerDto> = emptyList(),
)

@Serializable
data class HeartbeatRequestDto(
    @SerialName("reported_at") val reportedAt: Instant,
    val agent: AgentInfoDto,
    val inventory: InventoryDto,
    @SerialName("configured_bindings") val configuredBindings: ConfiguredBindingsDto,
)

/** Reserved: the appliance ignores every field here today. */
@Serializable
class HeartbeatAcceptedDto
