package com.screenkeeper.control.http.routes

import io.kotest.core.spec.style.FunSpec
import io.kotest.matchers.shouldBe
import io.kotest.matchers.string.shouldContain
import io.micrometer.core.instrument.Counter
import io.micrometer.prometheusmetrics.PrometheusConfig
import io.micrometer.prometheusmetrics.PrometheusMeterRegistry
import org.http4k.core.Method
import org.http4k.core.Request
import org.http4k.core.Status

class MetricsRoutesSpec : FunSpec({
    test("/metrics returns registered meters in Prometheus exposition format") {
        val registry = PrometheusMeterRegistry(PrometheusConfig.DEFAULT)
        Counter.builder("screenkeeper_control_test_total").register(registry).increment()

        val handler = MetricsRoutes.build(registry)
        val response = handler(Request(Method.GET, "/metrics"))

        response.status shouldBe Status.OK
        response.bodyString() shouldContain "screenkeeper_control_test_total"
    }

    test("an unknown path is not handled here") {
        val registry = PrometheusMeterRegistry(PrometheusConfig.DEFAULT)
        val handler = MetricsRoutes.build(registry)

        handler(Request(Method.GET, "/whatever")).status shouldBe Status.NOT_FOUND
    }
})
