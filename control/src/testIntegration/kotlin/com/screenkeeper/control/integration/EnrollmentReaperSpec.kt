package com.screenkeeper.control.integration

import com.screenkeeper.control.application.enrollment.EnrollmentReaper
import com.screenkeeper.control.support.TestApp
import com.screenkeeper.control.support.TestDatabase
import io.kotest.core.spec.style.FunSpec
import io.kotest.matchers.shouldBe
import io.opentelemetry.api.common.AttributeKey
import io.opentelemetry.api.trace.StatusCode
import io.opentelemetry.sdk.OpenTelemetrySdk
import io.opentelemetry.sdk.testing.exporter.InMemorySpanExporter
import io.opentelemetry.sdk.trace.SdkTracerProvider
import io.opentelemetry.sdk.trace.export.SimpleSpanProcessor
import org.http4k.core.Method
import org.http4k.core.Request
import java.time.Clock
import java.time.Duration
import java.time.Instant
import java.time.ZoneOffset
import java.util.UUID

/**
 * EnrollmentReaper.reapOnce() is a background job with no HTTP-triggered
 * parent span, so it's the one class in control/ that needs its own manual
 * span regardless of whether OTel auto-instrumentation covers the rest.
 * `reapOnce()` is private; start()/stop() drive it the same way Main.kt does.
 */
class EnrollmentReaperSpec : FunSpec({
    beforeTest { TestDatabase.truncateAll() }

    fun tracerWithExporter(): Pair<io.opentelemetry.api.trace.Tracer, InMemorySpanExporter> {
        val exporter = InMemorySpanExporter.create()
        val provider = SdkTracerProvider.builder()
            .addSpanProcessor(SimpleSpanProcessor.create(exporter))
            .build()
        val tracer = OpenTelemetrySdk.builder().setTracerProvider(provider).build().getTracer("test")
        return tracer to exporter
    }

    test("a reap iteration with expired enrollments emits a span with the reaped count") {
        val fixedNow = Instant.parse("2026-08-29T12:00:00Z")
        val app = TestApp(clock = Clock.fixed(fixedNow, ZoneOffset.UTC), enrollmentTtlMinutes = 0)
        app.handler(
            Request(Method.POST, "/api/v1/enrollments").body(
                """{"installation_id":"${UUID.randomUUID()}","device_token":"${"a".repeat(43)}","screenkeeper_version":"0.1.0","hostname":"host"}""",
            ),
        )

        val (tracer, exporter) = tracerWithExporter()
        val reaper = EnrollmentReaper(
            TestDatabase.jdbi,
            app.enrollmentRepository,
            Clock.fixed(fixedNow.plusSeconds(1), ZoneOffset.UTC),
            reapAfter = Duration.ZERO,
            meterRegistry = app.meterRegistry,
            interval = Duration.ofMillis(20),
            tracer = tracer,
        )

        reaper.start()
        val deadline = System.currentTimeMillis() + 5_000
        while (exporter.finishedSpanItems.isEmpty() && System.currentTimeMillis() < deadline) {
            Thread.sleep(10)
        }
        reaper.stop()

        val span = exporter.finishedSpanItems.first { it.name == "screenkeeper.enrollment.reap" }
        // OTel convention: successful spans stay UNSET; only failures get ERROR.
        span.status.statusCode shouldBe StatusCode.UNSET
        span.attributes.get(AttributeKey.longKey("enrollment.reaped_count")) shouldBe 1L

        val runsCount = app.meterRegistry.find("screenkeeper.enrollment.reap.runs")
            .tags("result", "success").counter()?.count() ?: 0.0
        (runsCount >= 1.0) shouldBe true
        val removedCount = app.meterRegistry.find("screenkeeper.enrollment.reap.removed").counter()?.count() ?: 0.0
        (removedCount >= 1.0) shouldBe true
    }
})
