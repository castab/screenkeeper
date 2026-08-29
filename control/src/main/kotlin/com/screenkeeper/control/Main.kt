package com.screenkeeper.control

import com.screenkeeper.control.application.enrollment.EnrollmentReaper
import com.screenkeeper.control.application.enrollment.EnrollmentService
import com.screenkeeper.control.application.heartbeat.HeartbeatService
import com.screenkeeper.control.application.registry.LocationService
import com.screenkeeper.control.application.registry.OrganizationService
import com.screenkeeper.control.application.registry.PlayerRegistryService
import com.screenkeeper.control.config.AppConfig
import com.screenkeeper.control.config.ConfigError
import com.screenkeeper.control.http.routes.Routes
import com.screenkeeper.control.persistence.jdbi.JdbiFactory
import com.screenkeeper.control.persistence.migration.Migrator
import com.screenkeeper.control.persistence.repository.EnrollmentRepository
import com.screenkeeper.control.persistence.repository.LocationRepository
import com.screenkeeper.control.persistence.repository.OrganizationRepository
import com.screenkeeper.control.persistence.repository.PlayerCredentialRepository
import com.screenkeeper.control.persistence.repository.PlayerReportRepository
import com.screenkeeper.control.persistence.repository.PlayerRepository
import org.http4k.server.Jetty
import org.http4k.server.asServer
import org.slf4j.LoggerFactory
import java.time.Clock
import java.time.Duration
import kotlin.system.exitProcess

private val logger = LoggerFactory.getLogger("Main")

fun main() {
    val config = try {
        AppConfig.fromEnv()
    } catch (e: ConfigError) {
        System.err.println("screenkeeper-control: ${e.message}")
        exitProcess(1)
    }

    val dataSource = JdbiFactory.dataSource(config)
    val migrator = Migrator(dataSource)
    try {
        migrator.migrate()
    } catch (e: Exception) {
        logger.error("Database migration failed; refusing to start", e)
        exitProcess(1)
    }

    val jdbi = JdbiFactory.build(dataSource)
    val clock = Clock.systemUTC()

    val organizationRepository = OrganizationRepository()
    val locationRepository = LocationRepository()
    val playerRepository = PlayerRepository()
    val playerCredentialRepository = PlayerCredentialRepository()
    val enrollmentRepository = EnrollmentRepository()
    val playerReportRepository = PlayerReportRepository()

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

    val reaper = EnrollmentReaper(
        jdbi, enrollmentRepository, clock, Duration.ofMinutes(config.enrollmentReapAfterMinutes),
    )
    reaper.start()

    val app = Routes.build(
        jdbi = jdbi,
        dataSource = dataSource,
        migrator = migrator,
        clock = clock,
        adminToken = config.adminToken,
        enrollmentService = enrollmentService,
        heartbeatService = heartbeatService,
        organizationService = organizationService,
        locationService = locationService,
        playerRegistryService = playerRegistryService,
        enrollmentRepository = enrollmentRepository,
        playerRepository = playerRepository,
        playerCredentialRepository = playerCredentialRepository,
    )

    val server = app.asServer(Jetty(config.port)).start()
    logger.info("screenkeeper-control listening on port {}", config.port)

    Runtime.getRuntime().addShutdownHook(
        Thread {
            logger.info("Shutting down screenkeeper-control")
            reaper.stop()
            server.stop()
        },
    )
}
