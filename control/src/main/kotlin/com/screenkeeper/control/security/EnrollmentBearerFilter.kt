package com.screenkeeper.control.security

import com.screenkeeper.control.domain.Enrollment
import com.screenkeeper.control.http.RequestContext
import com.screenkeeper.control.http.models.errorResponse
import org.http4k.core.Filter
import org.http4k.core.Status
import org.http4k.core.with
import org.http4k.lens.Path
import java.time.Clock
import java.util.UUID

private val enrollmentIdPath = Path.of("enrollment_id")

/**
 * Guards GET /api/v1/enrollments/{enrollment_id}. Ordering matters: unknown
 * id is 404 before the credential is even checked, a bad/missing bearer is
 * 401, and only a correctly authenticated caller can learn an enrollment has
 * expired (410).
 *
 * [resolve] is a plain lookup function rather than a direct Jdbi/repository
 * dependency so this filter's branching logic can be unit tested against a
 * fake with no database involved.
 */
fun EnrollmentBearerFilter(clock: Clock, resolve: (UUID) -> Enrollment?): Filter = Filter { next ->
    { request ->
        val enrollmentId = runCatching { UUID.fromString(enrollmentIdPath(request)) }.getOrNull()
        val enrollment = enrollmentId?.let(resolve)
        val bearer = request.bearerToken()

        when {
            enrollment == null ->
                errorResponse(Status.NOT_FOUND, "enrollment_not_found", "Unknown enrollment.")
            bearer == null || !ConstantTime.equals(sha256(bearer), enrollment.deviceTokenHash) ->
                errorResponse(Status.UNAUTHORIZED, "invalid_credential", "Missing or invalid credential.")
            enrollment.isExpiredAt(clock.instant()) ->
                errorResponse(Status.GONE, "enrollment_expired", "This enrollment has expired.")
            else ->
                next(request.with(RequestContext.enrollmentKey of enrollment))
        }
    }
}
