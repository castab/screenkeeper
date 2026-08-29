package com.screenkeeper.control.application.registry

import com.screenkeeper.control.config.AppConfig
import com.screenkeeper.control.domain.Location
import com.screenkeeper.control.domain.Organization
import com.screenkeeper.control.domain.Player
import com.screenkeeper.control.domain.isOnline
import com.screenkeeper.control.persistence.repository.LocationRepository
import com.screenkeeper.control.persistence.repository.OrganizationRepository
import com.screenkeeper.control.persistence.repository.PlayerReportRepository
import com.screenkeeper.control.persistence.repository.PlayerRepository
import org.jdbi.v3.core.Handle
import org.jdbi.v3.core.Jdbi
import java.time.Clock
import java.util.UUID

data class PlayerSummary(
    val player: Player,
    val location: Location,
    val organization: Organization,
    val online: Boolean,
)

data class PlayerDetail(
    val summary: PlayerSummary,
    val latestReportJson: String?,
)

class PlayerRegistryService(
    private val jdbi: Jdbi,
    private val clock: Clock,
    private val config: AppConfig,
    private val playerRepository: PlayerRepository,
    private val locationRepository: LocationRepository,
    private val organizationRepository: OrganizationRepository,
    private val playerReportRepository: PlayerReportRepository,
) {
    fun list(): List<PlayerSummary> =
        jdbi.withHandle<List<PlayerSummary>, RuntimeException> { handle ->
            playerRepository.list(handle).map { player -> toSummary(handle, player) }
        }

    fun get(playerId: UUID): PlayerDetail? =
        jdbi.withHandle<PlayerDetail?, RuntimeException> { handle ->
            val player = playerRepository.findById(handle, playerId) ?: return@withHandle null
            val summary = toSummary(handle, player)
            val report = playerReportRepository.findByPlayerId(handle, playerId)
            PlayerDetail(summary, report?.reportJson)
        }

    private fun toSummary(handle: Handle, player: Player): PlayerSummary {
        // FK constraints guarantee both lookups succeed; a player can't exist
        // without its location, nor a location without its organization.
        val location = locationRepository.findById(handle, player.locationId)!!
        val organization = organizationRepository.findById(handle, location.organizationId)!!
        val online = isOnline(player.lastSeenAt, clock.instant(), config.heartbeatIntervalSeconds, config.onlineMultiplier)
        return PlayerSummary(player, location, organization, online)
    }
}
