package com.screenkeeper.control.http.routes

import io.micrometer.prometheusmetrics.PrometheusMeterRegistry
import org.http4k.core.Method
import org.http4k.core.Response
import org.http4k.core.Status
import org.http4k.routing.bind
import org.http4k.routing.routes

/**
 * The local-only Prometheus scrape endpoint.
 *
 * Deliberately never added to [Routes.build]: that handler is reachable over
 * WAN from every enrolled player's heartbeat, and metrics must not be. This
 * is served on its own listener bound to `metricsHost`/`metricsPort` -- see
 * Main.kt.
 */
object MetricsRoutes {
    fun build(registry: PrometheusMeterRegistry) = routes(
        "/metrics" bind Method.GET to {
            Response(Status.OK)
                .header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
                .body(registry.scrape())
        },
    )
}
