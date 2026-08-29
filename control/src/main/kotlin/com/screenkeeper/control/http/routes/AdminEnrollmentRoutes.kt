package com.screenkeeper.control.http.routes

import com.screenkeeper.control.application.enrollment.EnrollmentService
import com.screenkeeper.control.domain.DomainError
import com.screenkeeper.control.http.models.ClaimRequestDto
import com.screenkeeper.control.http.models.ClaimResponseDto
import com.screenkeeper.control.http.models.EnrollmentLookupDto
import com.screenkeeper.control.http.models.Json.auto
import com.screenkeeper.control.http.models.NamedEntityDto
import kotlinx.datetime.toKotlinInstant
import org.http4k.core.Body
import org.http4k.core.Method
import org.http4k.core.Response
import org.http4k.core.Status
import org.http4k.lens.Path
import org.http4k.routing.bind
import org.http4k.routing.routes
import java.time.Clock
import java.util.UUID

private val claimRequestLens = Body.auto<ClaimRequestDto>().toLens()
private val claimResponseLens = Body.auto<ClaimResponseDto>().toLens()
private val enrollmentLookupLens = Body.auto<EnrollmentLookupDto>().toLens()
private val codePath = Path.of("code")

object AdminEnrollmentRoutes {
    fun build(service: EnrollmentService, clock: Clock) = routes(
        "/api/v1/admin/enrollments/{code}" bind Method.GET to { request ->
            val code = codePath(request)
            val enrollment = service.findByCode(code) ?: throw DomainError.EnrollmentNotFound()
            val status = when {
                enrollment.isClaimed -> "claimed"
                enrollment.isExpiredAt(clock.instant()) -> "expired"
                else -> "pending"
            }
            enrollmentLookupLens(
                EnrollmentLookupDto(
                    enrollmentId = enrollment.id.toString(),
                    installationId = enrollment.installationId.toString(),
                    status = status,
                    expiresAt = enrollment.expiresAt.toKotlinInstant(),
                    screenkeeperVersion = enrollment.screenkeeperVersion,
                    hostname = enrollment.hostname,
                ),
                Response(Status.OK),
            )
        },
        "/api/v1/admin/enrollments/{code}/claim" bind Method.POST to { request ->
            val code = codePath(request)
            val dto = claimRequestLens(request)
            val locationId = runCatching { UUID.fromString(dto.locationId) }
                .getOrElse { throw DomainError.MalformedRequest("location_id must be a UUID.") }
            val result = service.claim(code, locationId, dto.name)
            claimResponseLens(
                ClaimResponseDto(
                    playerId = result.player.id.toString(),
                    playerName = result.player.name,
                    organization = NamedEntityDto(result.organization.id.toString(), result.organization.name),
                    location = NamedEntityDto(result.location.id.toString(), result.location.name),
                ),
                Response(Status.OK),
            )
        },
    )
}
