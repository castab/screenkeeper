package com.screenkeeper.control.http.routes

import com.screenkeeper.control.application.enrollment.EnrollmentPollResult
import com.screenkeeper.control.application.enrollment.EnrollmentService
import com.screenkeeper.control.domain.DomainError
import com.screenkeeper.control.http.RequestContext
import com.screenkeeper.control.http.models.EnrollmentCreatedDto
import com.screenkeeper.control.http.models.EnrollmentRequestDto
import com.screenkeeper.control.http.models.EnrollmentStateDto
import com.screenkeeper.control.http.models.Json.auto
import com.screenkeeper.control.http.models.NamedEntityDto
import kotlinx.datetime.toKotlinInstant
import org.http4k.core.Body
import org.http4k.core.HttpHandler
import org.http4k.core.Response
import org.http4k.core.Status
import java.util.UUID

private val enrollmentRequestLens = Body.auto<EnrollmentRequestDto>().toLens()
private val enrollmentCreatedLens = Body.auto<EnrollmentCreatedDto>().toLens()
private val enrollmentStateLens = Body.auto<EnrollmentStateDto>().toLens()

/** POST/GET for /api/v1/enrollments -- exposed separately since only the
 *  poll route carries the bearer-auth filter (see http/routes/Routes.kt). */
object EnrollmentRoutes {
    fun create(service: EnrollmentService): HttpHandler = { request ->
        val dto = enrollmentRequestLens(request)
        val installationId = runCatching { UUID.fromString(dto.installationId) }
            .getOrElse { throw DomainError.MalformedRequest("installation_id must be a UUID.") }
        if (dto.deviceToken.length < 32) {
            throw DomainError.MalformedRequest("device_token must be at least 32 characters.")
        }

        val ticket = service.createOrReuse(installationId, dto.deviceToken, dto.screenkeeperVersion, dto.hostname)
        enrollmentCreatedLens(
            EnrollmentCreatedDto(
                enrollmentId = ticket.enrollmentId.toString(),
                code = ticket.code,
                expiresAt = ticket.expiresAt.toKotlinInstant(),
                pollIntervalSeconds = ticket.pollIntervalSeconds,
            ),
            Response(Status.CREATED),
        )
    }

    fun poll(service: EnrollmentService): HttpHandler = { request ->
        val enrollment = RequestContext.enrollmentKey(request)
        val dto = when (val result = service.pollState(enrollment)) {
            is EnrollmentPollResult.Pending -> EnrollmentStateDto(status = "pending")
            is EnrollmentPollResult.Claimed -> EnrollmentStateDto(
                status = "claimed",
                playerId = result.info.playerId.toString(),
                playerName = result.info.playerName,
                organization = NamedEntityDto(result.info.organization.id.toString(), result.info.organization.name),
                location = NamedEntityDto(result.info.location.id.toString(), result.info.location.name),
            )
        }
        enrollmentStateLens(dto, Response(Status.OK))
    }
}
