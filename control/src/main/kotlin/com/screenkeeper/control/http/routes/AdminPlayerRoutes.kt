package com.screenkeeper.control.http.routes

import com.screenkeeper.control.application.registry.PlayerRegistryService
import com.screenkeeper.control.application.registry.PlayerSummary
import com.screenkeeper.control.domain.DomainError
import com.screenkeeper.control.http.models.Json.auto
import com.screenkeeper.control.http.models.NamedEntityDto
import com.screenkeeper.control.http.models.PlayerDetailDto
import com.screenkeeper.control.http.models.PlayerSummaryDto
import kotlinx.datetime.toKotlinInstant
import kotlinx.serialization.json.Json as KotlinxJson
import org.http4k.core.Body
import org.http4k.core.Method
import org.http4k.core.Response
import org.http4k.core.Status
import org.http4k.lens.Path
import org.http4k.routing.bind
import org.http4k.routing.routes
import java.util.UUID

private val playerSummaryListLens = Body.auto<List<PlayerSummaryDto>>().toLens()
private val playerDetailLens = Body.auto<PlayerDetailDto>().toLens()
private val playerIdPath = Path.of("player_id")

object AdminPlayerRoutes {
    fun build(service: PlayerRegistryService) = routes(
        "/api/v1/admin/players" bind Method.GET to { _ ->
            playerSummaryListLens(service.list().map { it.toDto() }, Response(Status.OK))
        },
        "/api/v1/admin/players/{player_id}" bind Method.GET to { request ->
            val playerId = runCatching { UUID.fromString(playerIdPath(request)) }
                .getOrElse { throw DomainError.PlayerNotFound() }
            val detail = service.get(playerId) ?: throw DomainError.PlayerNotFound()
            playerDetailLens(
                PlayerDetailDto(
                    id = detail.summary.player.id.toString(),
                    name = detail.summary.player.name,
                    installationId = detail.summary.player.installationId.toString(),
                    organization = NamedEntityDto(
                        detail.summary.organization.id.toString(),
                        detail.summary.organization.name,
                    ),
                    location = NamedEntityDto(detail.summary.location.id.toString(), detail.summary.location.name),
                    online = detail.summary.online,
                    lastSeenAt = detail.summary.player.lastSeenAt?.toKotlinInstant(),
                    screenkeeperVersion = detail.summary.player.screenkeeperVersion,
                    latestReport = detail.latestReportJson?.let { KotlinxJson.parseToJsonElement(it) },
                ),
                Response(Status.OK),
            )
        },
    )

    private fun PlayerSummary.toDto() = PlayerSummaryDto(
        id = player.id.toString(),
        name = player.name,
        organization = NamedEntityDto(organization.id.toString(), organization.name),
        location = NamedEntityDto(location.id.toString(), location.name),
        online = online,
        lastSeenAt = player.lastSeenAt?.toKotlinInstant(),
    )
}
