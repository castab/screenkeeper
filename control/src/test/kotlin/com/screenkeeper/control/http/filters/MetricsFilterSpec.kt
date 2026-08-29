package com.screenkeeper.control.http.filters

import io.kotest.core.spec.style.FunSpec
import io.kotest.matchers.shouldBe
import io.micrometer.prometheusmetrics.PrometheusConfig
import io.micrometer.prometheusmetrics.PrometheusMeterRegistry
import org.http4k.core.Method
import org.http4k.core.Request
import org.http4k.core.Response
import org.http4k.core.Status
import org.http4k.core.then

class MetricsFilterSpec : FunSpec({
    test("records a request timer tagged by method and status") {
        val registry = PrometheusMeterRegistry(PrometheusConfig.DEFAULT)
        val app = MetricsFilter(registry).then { _: Request -> Response(Status.OK) }

        app(Request(Method.GET, "/api/v1/players/abc-123/heartbeat"))

        val timer = registry.find("http.server.requests").timer()
        timer shouldBe registry.find("http.server.requests").tags("method", "GET", "status", "200").timer()
        timer?.count() shouldBe 1L
    }

    test("does not tag by the raw request path") {
        val registry = PrometheusMeterRegistry(PrometheusConfig.DEFAULT)
        val app = MetricsFilter(registry).then { _: Request -> Response(Status.OK) }

        app(Request(Method.GET, "/api/v1/players/one/heartbeat"))
        app(Request(Method.GET, "/api/v1/players/two/heartbeat"))

        // Both requests collapse into the same series; a per-id label would
        // instead have produced two.
        registry.find("http.server.requests").timer()?.count() shouldBe 2L
    }
})
