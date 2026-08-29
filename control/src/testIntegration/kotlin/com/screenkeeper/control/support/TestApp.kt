package com.screenkeeper.control.support

import com.screenkeeper.control.application.enrollment.EnrollmentService
import com.screenkeeper.control.application.heartbeat.HeartbeatService
import com.screenkeeper.control.application.registry.LocationService
import com.screenkeeper.control.application.registry.OrganizationService
import com.screenkeeper.control.application.registry.PlayerRegistryService
import com.screenkeeper.control.config.AppConfig
import com.screenkeeper.control.http.routes.Routes
import com.screenkeeper.control.persistence.migration.Migrator
import com.screenkeeper.control.persistence.repository.EnrollmentRepository
import com.screenkeeper.control.persistence.repository.LocationRepository
import com.screenkeeper.control.persistence.repository.OrganizationRepository
import com.screenkeeper.control.persistence.repository.PlayerCredentialRepository
import com.screenkeeper.control.persistence.repository.PlayerReportRepository
import com.screenkeeper.control.persistence.repository.PlayerRepository
import io.micrometer.prometheusmetrics.PrometheusConfig
import io.micrometer.prometheusmetrics.PrometheusMeterRegistry
import org.http4k.core.HttpHandler
import org.jdbi.v3.core.Jdbi
import java.time.Clock

/** Wires the full application against TestDatabase.jdbi and exposes the
 *  composed HttpHandler -- invoked in-process as a plain function, no real
 *  server socket needed. */
class TestApp(
    val clock: Clock = Clock.systemUTC(),
    val adminToken: String = "test-admin-token",
    heartbeatIntervalSeconds: Long = 30,
    onlineMultiplier: Double = 3.0,
    enrollmentTtlMinutes: Long = 15,
) {
    val jdbi: Jdbi = TestDatabase.jdbi

    val organizationRepository = OrganizationRepository()
    val locationRepository = LocationRepository()
    val playerRepository = PlayerRepository()
    val playerCredentialRepository = PlayerCredentialRepository()
    val enrollmentRepository = EnrollmentRepository()
    val playerReportRepository = PlayerReportRepository()

    val config = AppConfig(
        host = "0.0.0.0",
        port = 0,
        databaseUrl = "",
        databaseUser = "",
        databasePassword = "",
        adminToken = adminToken,
        heartbeatIntervalSeconds = heartbeatIntervalSeconds,
        onlineMultiplier = onlineMultiplier,
        enrollmentTtlMinutes = enrollmentTtlMinutes,
        enrollmentReapAfterMinutes = 60,
        metricsEnabled = true,
        metricsHost = "127.0.0.1",
        metricsPort = 0,
    )

    val meterRegistry = PrometheusMeterRegistry(PrometheusConfig.DEFAULT)

    val enrollmentService = EnrollmentService(
        jdbi, config, clock, enrollmentRepository, playerRepository, playerCredentialRepository,
        locationRepository, organizationRepository,
    )
    val heartbeatService = HeartbeatService(jdbi, clock, playerRepository, playerReportRepository)
    val organizationService = OrganizationService(jdbi, organizationRepository)
    val locationService = LocationService(jdbi, locationRepository, organizationRepository)
    val playerRegistryService = PlayerRegistryService(
        jdbi, clock, config, playerRepository, locationRepository, organizationRepository, playerReportRepository,
    )

    val handler: HttpHandler = Routes.build(
        jdbi = jdbi,
        dataSource = TestDatabase.dataSource,
        migrator = Migrator(TestDatabase.dataSource),
        clock = clock,
        adminToken = adminToken,
        enrollmentService = enrollmentService,
        heartbeatService = heartbeatService,
        organizationService = organizationService,
        locationService = locationService,
        playerRegistryService = playerRegistryService,
        enrollmentRepository = enrollmentRepository,
        playerRepository = playerRepository,
        playerCredentialRepository = playerCredentialRepository,
        meterRegistry = meterRegistry,
    )
}
