package com.screenkeeper.control.http.routes

import com.screenkeeper.control.application.content.ContentService
import com.screenkeeper.control.domain.DomainError
import com.screenkeeper.control.http.RequestContext
import com.screenkeeper.control.http.models.ContentManifestDto
import com.screenkeeper.control.http.models.ContentStatusReportDto
import com.screenkeeper.control.http.models.DesiredAssetDto
import com.screenkeeper.control.http.models.Json.auto
import com.screenkeeper.control.http.models.PlayerContentAssignmentDto
import kotlinx.datetime.toJavaInstant
import org.http4k.core.Body
import org.http4k.core.HttpHandler
import org.http4k.core.Response
import org.http4k.core.Status

private val contentManifestLens = Body.auto<ContentManifestDto>().toLens()
private val contentStatusLens = Body.auto<ContentStatusReportDto>().toLens()
private const val MAX_CONTENT_STATUS_BYTES = 262_144

object ContentRoutes {
    fun manifest(service: ContentService): HttpHandler = { request ->
        val player = RequestContext.playerKey(request)
        val desired = service.desired(player.id)
        val dto = ContentManifestDto(
            revision = desired.revision,
            players = desired.assignments.map { (assignment, signed) ->
                val revision = assignment.assetRevision
                PlayerContentAssignmentDto(
                    playerId = assignment.playbackPlayerId,
                    content = DesiredAssetDto(
                        assetId = revision.assetId.toString(),
                        assetRevisionId = revision.id.toString(),
                        revision = revision.revision,
                        originalFilename = revision.originalFilename,
                        contentType = revision.contentType,
                        byteSize = revision.byteSize,
                        sha256 = revision.sha256,
                        downloadUrl = signed.url,
                    ),
                )
            },
        )
        contentManifestLens(dto, Response(Status.OK))
    }

    fun status(service: ContentService): HttpHandler = { request ->
        val player = RequestContext.playerKey(request)
        val rawJson = request.bodyString()
        if (rawJson.toByteArray(Charsets.UTF_8).size > MAX_CONTENT_STATUS_BYTES) {
            throw DomainError.PayloadTooLarge()
        }
        val dto = contentStatusLens(request)
        service.recordStatus(player.id, dto.reportedAt.toJavaInstant(), rawJson)
        Response(Status.NO_CONTENT)
    }
}
