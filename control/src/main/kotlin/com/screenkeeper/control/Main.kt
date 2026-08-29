package com.screenkeeper.control

import com.screenkeeper.control.application.enrollment.EnrollmentReaper
import com.screenkeeper.control.application.enrollment.EnrollmentService
import com.screenkeeper.control.application.heartbeat.HeartbeatService
import com.screenkeeper.control.application.registry.LocationService
import com.screenkeeper.control.application.registry.OrganizationService
import com.screenkeeper.control.application.registry.PlayerRegistryService
import com.screenkeeper.control.config.AppConfig
import com.screenkeeper.control.config.ConfigError
import com.screenkeeper.control.http.routes.MetricsRoutes
import com.screenkeeper.control.http.routes.Routes
import com.screenkeeper.control.persistence.jdbi.JdbiFactory
import com.screenkeeper.control.persistence.migration.Migrator
import com.screenkeeper.control.persistence.repository.EnrollmentRepository
import com.screenkeeper.control.persistence.repository.LocationRepository
import com.screenkeeper.control.persistence.repository.OrganizationRepository
import com.screenkeeper.control.persistence.repository.PlayerCredentialRepository
import com.screenkeeper.control.persistence.repository.PlayerReportRepository
import com.screenkeeper.control.persistence.repository.PlayerRepository
import io.micrometer.core.instrument.binder.jvm.JvmGcMetrics
import io.micrometer.core.instrument.binder.jvm.JvmMemoryMetrics
import io.micrometer.core.instrument.binder.system.ProcessorMetrics
import io.micrometer.core.instrument.binder.system.UptimeMetrics
import io.micrometer.prometheusmetrics.PrometheusConfig
import io.micrometer.prometheusmetrics.PrometheusMeterRegistry
import org.eclipse.jetty.server.Server
import org.eclipse.jetty.server.ServerConnector
import org.http4k.server.Http4kServer
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

    val meterRegistry = PrometheusMeterRegistry(PrometheusConfig.DEFAULT)
    // Standard JVM/process metrics via Micrometer's own binders -- the same
    // "uptime via a standard collector, not a bespoke metric" answer as the
    // edge side's prometheus_client process collectors (see observability.py).
    JvmMemoryMetrics().bindTo(meterRegistry)
    JvmGcMetrics().bindTo(meterRegistry)
    ProcessorMetrics().bindTo(meterRegistry)
    UptimeMetrics().bindTo(meterRegistry)

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
        meterRegistry = meterRegistry,
    )

    val server = app.asServer(Jetty(config.port)).start()
    logger.info("screenkeeper-control listening on port {}", config.port)

    // A second, localhost-bound listener: config.port above is reachable over
    // WAN from every enrolled player's heartbeat, and metrics must never share
    // it. This server depends on nothing else here starting and never blocks
    // the public listener above if metrics are disabled.
    val metricsServer: Http4kServer? = if (config.metricsEnabled) {
        val loopbackConnector: (Server) -> ServerConnector = { jettyServer ->
            ServerConnector(jettyServer).apply { host = config.metricsHost }
        }
        MetricsRoutes.build(meterRegistry)
            .asServer(Jetty(config.metricsPort, loopbackConnector))
            .start()
            .also { logger.info("screenkeeper-control metrics listening on {}:{}", config.metricsHost, config.metricsPort) }
    } else {
        null
    }

    Runtime.getRuntime().addShutdownHook(
        Thread {
            logger.info("Shutting down screenkeeper-control")
            reaper.stop()
            metricsServer?.stop()
            server.stop()
        },
    )
}
