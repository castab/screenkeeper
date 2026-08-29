package com.screenkeeper.control.http.filters

import com.screenkeeper.control.domain.DomainError
import com.screenkeeper.control.http.models.errorResponse
import org.http4k.core.Filter
import org.http4k.core.Status
import org.http4k.lens.LensFailure
import org.slf4j.LoggerFactory

private val logger = LoggerFactory.getLogger("ErrorHandlingFilter")

/**
 * The single place that maps a failure to the wire {error, message} shape.
 * Full exception detail (including SQL errors) is logged server-side only;
 * the response body never contains a stack trace or SQL state.
 */
val ErrorHandlingFilter: Filter = Filter { next ->
    { request ->
        try {
            next(request)
        } catch (e: DomainError) {
            val status = when (e) {
                is DomainError.AlreadyEnrolled -> Status.CONFLICT
                is DomainError.EnrollmentNotFound -> Status.NOT_FOUND
                is DomainError.EnrollmentExpired -> Status.GONE
                is DomainError.EnrollmentAlreadyClaimed -> Status.CONFLICT
                is DomainError.OrganizationNotFound -> Status.NOT_FOUND
                is DomainError.LocationNotFound -> Status.NOT_FOUND
                is DomainError.PlayerNotFound -> Status.NOT_FOUND
                is DomainError.InvalidCredential -> Status.UNAUTHORIZED
                is DomainError.RevokedCredential -> Status.FORBIDDEN
                is DomainError.MalformedRequest -> Status.BAD_REQUEST
                is DomainError.PayloadTooLarge -> Status(413, "Payload Too Large")
            }
            errorResponse(status, e.code, e.message)
        } catch (e: LensFailure) {
            logger.debug("Request failed lens validation: {}", e.message)
            errorResponse(Status.BAD_REQUEST, "malformed_request", "The request is invalid.")
        } catch (e: Exception) {
            logger.error("Unhandled exception handling request", e)
            errorResponse(Status.INTERNAL_SERVER_ERROR, "internal_error", "An unexpected error occurred.")
        }
    }
}
