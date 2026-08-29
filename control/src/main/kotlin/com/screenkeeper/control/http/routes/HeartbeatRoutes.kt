package com.screenkeeper.control.http.routes

import com.screenkeeper.control.application.heartbeat.HeartbeatService
import com.screenkeeper.control.domain.DomainError
import com.screenkeeper.control.http.RequestContext
import com.screenkeeper.control.http.models.HeartbeatAcceptedDto
import com.screenkeeper.control.http.models.HeartbeatRequestDto
import com.screenkeeper.control.http.models.Json.auto
import kotlinx.datetime.toJavaInstant
import org.http4k.core.Body
import org.http4k.core.HttpHandler
import org.http4k.core.Response
import org.http4k.core.Status

private val heartbeatRequestLens = Body.auto<HeartbeatRequestDto>().toLens()
private val heartbeatAcceptedLens = Body.auto<HeartbeatAcceptedDto>().toLens()

private const val MAX_HEARTBEAT_BODY_BYTES = 262_144 // 256 KiB

object HeartbeatRoutes {
    fun submit(service: HeartbeatService): HttpHandler = { request ->
        val player = RequestContext.playerKey(request)
        // Captured before decoding so unknown fields survive byte-for-byte
        // into player_reports.report -- decoding into the DTO and
        // re-serializing would silently drop them.
        val rawJson = request.bodyString()
        if (rawJson.toByteArray(Charsets.UTF_8).size > MAX_HEARTBEAT_BODY_BYTES) {
            throw DomainError.PayloadTooLarge()
        }

        val dto = heartbeatRequestLens(request)

        service.record(
            playerId = player.id,
            screenkeeperVersion = dto.agent.screenkeeperVersion,
            hostname = dto.agent.hostname,
            reportedAt = dto.reportedAt.toJavaInstant(),
            rawReportJson = rawJson,
        )

        heartbeatAcceptedLens(HeartbeatAcceptedDto(), Response(Status.ACCEPTED))
    }
}
