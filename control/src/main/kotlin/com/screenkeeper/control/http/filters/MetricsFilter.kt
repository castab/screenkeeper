package com.screenkeeper.control.http.filters

import io.micrometer.core.instrument.MeterRegistry
import org.http4k.core.Filter
import java.time.Duration
import java.time.Instant

/**
 * Records the conventional `http.server.requests` timer for the public API,
 * tagged by method/status only. This only writes into [registry]; the
 * `/metrics` scrape route that reads it back lives on a separate, localhost-
 * only listener (see Main.kt), never on this same public port.
 *
 * The raw request path is deliberately not a tag: several routes embed an
 * id (`/api/v1/enrollments/{enrollment_id}`, `.../players/{player_id}/...`),
 * and http4k's plain `routing` module does not expose the matched template
 * back to a filter -- tagging by the literal path would be unbounded
 * cardinality, one series per id ever seen.
 */
fun MetricsFilter(registry: MeterRegistry): Filter = Filter { next ->
    { request ->
        val start = Instant.now()
        val response = next(request)
        registry.timer(
            "http.server.requests",
            "method", request.method.name,
            "status", response.status.code.toString(),
        ).record(Duration.between(start, Instant.now()))
        response
    }
}
