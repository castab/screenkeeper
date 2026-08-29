package com.screenkeeper.control.http.routes

import com.screenkeeper.control.application.enrollment.EnrollmentService
import com.screenkeeper.control.application.heartbeat.HeartbeatService
import com.screenkeeper.control.application.registry.LocationService
import com.screenkeeper.control.application.registry.OrganizationService
import com.screenkeeper.control.application.registry.PlayerRegistryService
import com.screenkeeper.control.http.filters.ErrorHandlingFilter
import com.screenkeeper.control.http.filters.MetricsFilter
import com.screenkeeper.control.http.filters.RequestLoggingFilter
import com.screenkeeper.control.persistence.migration.Migrator
import com.screenkeeper.control.persistence.repository.EnrollmentRepository
import com.screenkeeper.control.persistence.repository.PlayerCredentialRepository
import com.screenkeeper.control.persistence.repository.PlayerRepository
import com.screenkeeper.control.security.AdminAuthFilter
import com.screenkeeper.control.security.EnrollmentBearerFilter
import com.screenkeeper.control.security.PlayerCredentialFilter
import org.http4k.core.HttpHandler
import org.http4k.core.Method
import org.http4k.core.then
import io.micrometer.core.instrument.MeterRegistry
import org.http4k.routing.bind
import org.http4k.routing.routes
import org.jdbi.v3.core.Jdbi
import java.time.Clock
import javax.sql.DataSource

object Routes {
    fun build(
        jdbi: Jdbi,
        dataSource: DataSource,
        migrator: Migrator,
        clock: Clock,
        adminToken: String,
        enrollmentService: EnrollmentService,
        heartbeatService: HeartbeatService,
        organizationService: OrganizationService,
        locationService: LocationService,
        playerRegistryService: PlayerRegistryService,
        enrollmentRepository: EnrollmentRepository,
        playerRepository: PlayerRepository,
        playerCredentialRepository: PlayerCredentialRepository,
        meterRegistry: MeterRegistry,
    ): HttpHandler {
        val enrollmentPollRoute = "/api/v1/enrollments/{enrollment_id}" bind Method.GET to
            EnrollmentBearerFilter(clock) { id ->
                jdbi.withHandle<com.screenkeeper.control.domain.Enrollment?, RuntimeException> { handle ->
                    enrollmentRepository.findById(handle, id)
                }
            }.then(EnrollmentRoutes.poll(enrollmentService))

        val enrollmentCreateRoute = "/api/v1/enrollments" bind Method.POST to
            EnrollmentRoutes.create(enrollmentService)

        val heartbeatRoute = "/api/v1/players/{player_id}/heartbeat" bind Method.POST to
            PlayerCredentialFilter(
                resolvePlayer = { id ->
                    jdbi.withHandle<com.screenkeeper.control.domain.Player?, RuntimeException> { handle ->
                        playerRepository.findById(handle, id)
                    }
                },
                resolveCredential = { id ->
                    jdbi.withHandle<com.screenkeeper.control.domain.PlayerCredential?, RuntimeException> { handle ->
                        playerCredentialRepository.findByPlayerId(handle, id)
                    }
                },
            ).then(HeartbeatRoutes.submit(heartbeatService))

        val adminRoutes = routes(
            AdminOrganizationRoutes.build(organizationService),
            AdminLocationRoutes.build(locationService),
            AdminEnrollmentRoutes.build(enrollmentService, clock),
            AdminPlayerRoutes.build(playerRegistryService),
        ).withFilter(AdminAuthFilter(adminToken))

        val app = routes(
            HealthRoutes.build(dataSource, migrator),
            enrollmentCreateRoute,
            enrollmentPollRoute,
            heartbeatRoute,
            adminRoutes,
        )

        return RequestLoggingFilter
            .then(ErrorHandlingFilter)
            .then(MetricsFilter(meterRegistry))
            .then(app)
    }
}
