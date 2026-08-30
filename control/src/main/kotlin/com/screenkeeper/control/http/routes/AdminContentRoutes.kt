package com.screenkeeper.control.http.routes

import com.screenkeeper.control.application.content.AssetDetail
import com.screenkeeper.control.application.content.AssetService
import com.screenkeeper.control.application.content.ContentService
import com.screenkeeper.control.domain.DomainError
import com.screenkeeper.control.domain.MediaAssetRevision
import com.screenkeeper.control.http.models.AssignContentRequestDto
import com.screenkeeper.control.http.models.AssignmentResultDto
import com.screenkeeper.control.http.models.CreateAssetRequestDto
import com.screenkeeper.control.http.models.CreateAssetRevisionRequestDto
import com.screenkeeper.control.http.models.CreateAssetRevisionResponseDto
import com.screenkeeper.control.http.models.Json.auto
import com.screenkeeper.control.http.models.MediaAssetDto
import com.screenkeeper.control.http.models.MediaAssetRevisionDto
import com.screenkeeper.control.http.models.UploadRequestDto
import kotlinx.datetime.toKotlinInstant
import org.http4k.core.Body
import org.http4k.core.Method
import org.http4k.core.Response
import org.http4k.core.Status
import org.http4k.lens.Path
import org.http4k.routing.bind
import org.http4k.routing.routes
import java.util.UUID

private val createAssetLens = Body.auto<CreateAssetRequestDto>().toLens()
private val assetLens = Body.auto<MediaAssetDto>().toLens()
private val assetListLens = Body.auto<List<MediaAssetDto>>().toLens()
private val createRevisionLens = Body.auto<CreateAssetRevisionRequestDto>().toLens()
private val createRevisionResponseLens = Body.auto<CreateAssetRevisionResponseDto>().toLens()
private val assignContentLens = Body.auto<AssignContentRequestDto>().toLens()
private val assignmentResultLens = Body.auto<AssignmentResultDto>().toLens()
private val assetIdPath = Path.of("asset_id")
private val revisionIdPath = Path.of("revision_id")
private val adminPlayerIdPath = Path.of("player_id")
private val playbackPlayerIdPath = Path.of("playback_player_id")

object AdminContentRoutes {
    fun build(assetService: AssetService, contentService: ContentService) = routes(
        "/api/v1/admin/assets" bind Method.GET to { _ ->
            assetListLens(assetService.list().map { it.toDto() }, Response(Status.OK))
        },
        "/api/v1/admin/assets" bind Method.POST to { request ->
            val created = assetService.create(createAssetLens(request).name)
            assetLens(
                MediaAssetDto(
                    created.id.toString(), created.name, created.createdAt.toKotlinInstant(), emptyList(),
                ),
                Response(Status.CREATED),
            )
        },
        "/api/v1/admin/assets/{asset_id}" bind Method.GET to { request ->
            val detail = assetService.get(uuid(assetIdPath(request), DomainError.AssetNotFound()))
                ?: throw DomainError.AssetNotFound()
            assetLens(detail.toDto(), Response(Status.OK))
        },
        "/api/v1/admin/assets/{asset_id}/revisions" bind Method.POST to { request ->
            val dto = createRevisionLens(request)
            val result = assetService.createRevision(
                uuid(assetIdPath(request), DomainError.AssetNotFound()),
                dto.originalFilename,
                dto.contentType,
                dto.byteSize,
                dto.sha256,
            )
            val upload = result.upload
            createRevisionResponseLens(
                CreateAssetRevisionResponseDto(
                    assetRevisionId = result.revision.id.toString(),
                    revision = result.revision.revision,
                    status = result.revision.status,
                    upload = UploadRequestDto(
                        required = upload != null,
                        method = upload?.method,
                        url = upload?.url,
                        headers = upload?.headers ?: emptyMap(),
                        expiresAt = upload?.expiresAt?.toKotlinInstant(),
                    ),
                ),
                Response(Status.CREATED),
            )
        },
        "/api/v1/admin/assets/{asset_id}/revisions/{revision_id}/complete" bind Method.POST to { request ->
            val revision = assetService.complete(
                uuid(assetIdPath(request), DomainError.AssetNotFound()),
                uuid(revisionIdPath(request), DomainError.AssetRevisionNotFound()),
            )
            Body.auto<MediaAssetRevisionDto>().toLens()(revision.toDto(), Response(Status.OK))
        },
        "/api/v1/admin/players/{player_id}/content/{playback_player_id}" bind Method.PUT to { request ->
            val dto = assignContentLens(request)
            val revision = contentService.assign(
                uuid(adminPlayerIdPath(request), DomainError.PlayerNotFound()),
                playbackPlayerIdPath(request),
                uuid(dto.assetRevisionId, DomainError.AssetRevisionNotFound()),
            )
            assignmentResultLens(AssignmentResultDto(revision), Response(Status.OK))
        },
        "/api/v1/admin/players/{player_id}/content/{playback_player_id}" bind Method.DELETE to { request ->
            contentService.clear(
                uuid(adminPlayerIdPath(request), DomainError.PlayerNotFound()),
                playbackPlayerIdPath(request),
            )
            Response(Status.NO_CONTENT)
        },
    )

    private fun uuid(value: String, error: DomainError): UUID =
        runCatching { UUID.fromString(value) }.getOrElse { throw error }

    private fun AssetDetail.toDto() = MediaAssetDto(
        asset.id.toString(), asset.name, asset.createdAt.toKotlinInstant(), revisions.map { it.toDto() },
    )

    private fun MediaAssetRevision.toDto() = MediaAssetRevisionDto(
        id = id.toString(),
        revision = revision,
        originalFilename = originalFilename,
        contentType = contentType,
        byteSize = byteSize,
        sha256 = sha256,
        status = status,
        createdAt = createdAt.toKotlinInstant(),
        availableAt = availableAt?.toKotlinInstant(),
    )
}
