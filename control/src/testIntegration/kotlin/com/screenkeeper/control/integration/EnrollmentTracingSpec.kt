package com.screenkeeper.control.integration

import com.screenkeeper.control.domain.DomainError
import com.screenkeeper.control.support.TestApp
import com.screenkeeper.control.support.TestDatabase
import io.kotest.assertions.throwables.shouldThrow
import io.kotest.core.spec.style.FunSpec
import io.kotest.matchers.collections.shouldHaveSize
import io.kotest.matchers.shouldBe
import io.opentelemetry.api.trace.StatusCode
import io.opentelemetry.sdk.OpenTelemetrySdk
import io.opentelemetry.sdk.testing.exporter.InMemorySpanExporter
import io.opentelemetry.sdk.trace.SdkTracerProvider
import io.opentelemetry.sdk.trace.export.SimpleSpanProcessor
import java.util.UUID

/**
 * Manual spans on EnrollmentService, verified end to end against a real
 * Postgres -- these methods have no in-memory fake to unit-test against (see
 * ClaimTransactionSpec, tested the same way). A fresh SdkTracerProvider is
 * built and injected per test, never touching GlobalOpenTelemetry, so this
 * stays isolated from every other spec regardless of execution order.
 */
class EnrollmentTracingSpec : FunSpec({
    beforeTest { TestDatabase.truncateAll() }

    fun tracerWithExporter(): Pair<io.opentelemetry.api.trace.Tracer, InMemorySpanExporter> {
        val exporter = InMemorySpanExporter.create()
        val provider = SdkTracerProvider.builder()
            .addSpanProcessor(SimpleSpanProcessor.create(exporter))
            .build()
        val tracer = OpenTelemetrySdk.builder().setTracerProvider(provider).build().getTracer("test")
        return tracer to exporter
    }

    test("a successful enrollment and claim each produce a span without error status") {
        val (tracer, exporter) = tracerWithExporter()
        val app = TestApp(tracer = tracer)
        val org = app.organizationService.create("Example Restaurant")
        val location = app.locationService.create(org.id, "Downtown")

        val ticket = app.enrollmentService.createOrReuse(UUID.randomUUID(), "a".repeat(43), "0.1.0", "host")
        app.enrollmentService.claim(ticket.code, location.id, "Main Menu Wall")

        val spans = exporter.finishedSpanItems
        spans shouldHaveSize 2
        spans.map { it.name } shouldBe listOf(
            "screenkeeper.enrollment.create_or_reuse",
            "screenkeeper.enrollment.claim",
        )
        // OTel convention: successful spans stay UNSET; only failures get ERROR.
        spans.forEach { it.status.statusCode shouldBe StatusCode.UNSET }
    }

    test("a rejected claim produces an ERROR span tagged with the domain error code") {
        val (tracer, exporter) = tracerWithExporter()
        val app = TestApp(tracer = tracer)
        val ticket = app.enrollmentService.createOrReuse(UUID.randomUUID(), "a".repeat(43), "0.1.0", "host")

        shouldThrow<DomainError.LocationNotFound> {
            app.enrollmentService.claim(ticket.code, UUID.randomUUID(), "Main Menu Wall")
        }

        val claimSpan = exporter.finishedSpanItems.single { it.name == "screenkeeper.enrollment.claim" }
        claimSpan.status.statusCode shouldBe StatusCode.ERROR
        claimSpan.attributes.get(io.opentelemetry.api.common.AttributeKey.stringKey("error.code")) shouldBe
            "location_not_found"
    }

    test("re-enrolling an already-claimed installation produces an ERROR span") {
        val (tracer, exporter) = tracerWithExporter()
        val app = TestApp(tracer = tracer)
        val org = app.organizationService.create("Example Restaurant")
        val location = app.locationService.create(org.id, "Downtown")
        val installationId = UUID.randomUUID()
        val ticket = app.enrollmentService.createOrReuse(installationId, "a".repeat(43), "0.1.0", "host")
        app.enrollmentService.claim(ticket.code, location.id, "Main Menu Wall")

        shouldThrow<DomainError.AlreadyEnrolled> {
            app.enrollmentService.createOrReuse(installationId, "a".repeat(43), "0.1.0", "host")
        }

        val createSpans = exporter.finishedSpanItems.filter { it.name == "screenkeeper.enrollment.create_or_reuse" }
        createSpans shouldHaveSize 2
        createSpans.last().status.statusCode shouldBe StatusCode.ERROR
    }
})
