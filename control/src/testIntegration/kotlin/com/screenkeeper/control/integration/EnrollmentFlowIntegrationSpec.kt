package com.screenkeeper.control.integration

import com.screenkeeper.control.support.TestApp
import com.screenkeeper.control.support.TestDatabase
import io.kotest.core.spec.style.FunSpec
import io.kotest.matchers.shouldBe
import io.kotest.matchers.shouldNotBe
import org.http4k.core.Method
import org.http4k.core.Request
import org.http4k.core.Status
import java.time.Clock
import java.time.Instant
import java.time.ZoneOffset
import java.util.UUID

class EnrollmentFlowIntegrationSpec : FunSpec({
    beforeTest { TestDatabase.truncateAll() }

    fun enrollBody(installationId: UUID, deviceToken: String) = """
        {"installation_id":"$installationId","device_token":"$deviceToken","screenkeeper_version":"0.1.0","hostname":"menu-player-01"}
    """.trimIndent()

    test("creates a pending enrollment and it can be polled with the device token") {
        val app = TestApp()
        val installationId = UUID.randomUUID()
        val deviceToken = "a".repeat(43)

        val created = app.handler(Request(Method.POST, "/api/v1/enrollments").body(enrollBody(installationId, deviceToken)))
        created.status shouldBe Status.CREATED

        val enrollmentId = Regex(""""enrollment_id":"([^"]+)"""").find(created.bodyString())!!.groupValues[1]

        val poll = app.handler(
            Request(Method.GET, "/api/v1/enrollments/$enrollmentId").header("Authorization", "Bearer $deviceToken"),
        )
        poll.status shouldBe Status.OK
        poll.bodyString() shouldBe """{"status":"pending"}"""
    }

    test("repeated enrollment requests for a pending installation supersede in place") {
        val app = TestApp()
        val installationId = UUID.randomUUID()
        val firstToken = "a".repeat(43)
        val secondToken = "b".repeat(43)

        val first = app.handler(Request(Method.POST, "/api/v1/enrollments").body(enrollBody(installationId, firstToken)))
        val firstId = Regex(""""enrollment_id":"([^"]+)"""").find(first.bodyString())!!.groupValues[1]
        val firstCode = Regex(""""code":"([^"]+)"""").find(first.bodyString())!!.groupValues[1]

        val second = app.handler(Request(Method.POST, "/api/v1/enrollments").body(enrollBody(installationId, secondToken)))
        val secondId = Regex(""""enrollment_id":"([^"]+)"""").find(second.bodyString())!!.groupValues[1]
        val secondCode = Regex(""""code":"([^"]+)"""").find(second.bodyString())!!.groupValues[1]

        secondId shouldBe firstId // same row, superseded in place
        secondCode shouldNotBe firstCode

        // the old device token no longer authenticates the (superseded) enrollment
        val pollWithOldToken = app.handler(
            Request(Method.GET, "/api/v1/enrollments/$firstId").header("Authorization", "Bearer $firstToken"),
        )
        pollWithOldToken.status shouldBe Status.UNAUTHORIZED

        val pollWithNewToken = app.handler(
            Request(Method.GET, "/api/v1/enrollments/$firstId").header("Authorization", "Bearer $secondToken"),
        )
        pollWithNewToken.status shouldBe Status.OK
    }

    test("an already-enrolled installation is rejected with 409, even with the original device token") {
        val app = TestApp()
        val installationId = UUID.randomUUID()
        val deviceToken = "a".repeat(43)

        val created = app.handler(Request(Method.POST, "/api/v1/enrollments").body(enrollBody(installationId, deviceToken)))
        val code = Regex(""""code":"([^"]+)"""").find(created.bodyString())!!.groupValues[1]

        val org = app.organizationService.create("Example Restaurant")
        val location = app.locationService.create(org.id, "Downtown")
        app.enrollmentService.claim(code, location.id, "Main Menu Wall")

        val secondAttempt = app.handler(Request(Method.POST, "/api/v1/enrollments").body(enrollBody(installationId, deviceToken)))
        secondAttempt.status shouldBe Status.CONFLICT
    }

    test("an expired enrollment is rejected on poll with 410") {
        val fixedNow = Instant.parse("2026-08-29T12:00:00Z")
        val app = TestApp(clock = Clock.fixed(fixedNow, ZoneOffset.UTC), enrollmentTtlMinutes = 0)
        val installationId = UUID.randomUUID()
        val deviceToken = "a".repeat(43)

        val created = app.handler(Request(Method.POST, "/api/v1/enrollments").body(enrollBody(installationId, deviceToken)))
        val enrollmentId = Regex(""""enrollment_id":"([^"]+)"""").find(created.bodyString())!!.groupValues[1]

        // fixed clock, TTL of 0 minutes -- expires_at == created_at, so "now" (unchanged) is already past it
        val laterApp = TestApp(clock = Clock.fixed(fixedNow.plusSeconds(1), ZoneOffset.UTC))
        val poll = laterApp.handler(
            Request(Method.GET, "/api/v1/enrollments/$enrollmentId").header("Authorization", "Bearer $deviceToken"),
        )
        poll.status shouldBe Status.GONE
    }

    test("an unknown enrollment id is rejected with 404") {
        val app = TestApp()
        val response = app.handler(
            Request(Method.GET, "/api/v1/enrollments/${UUID.randomUUID()}").header("Authorization", "Bearer irrelevant"),
        )
        response.status shouldBe Status.NOT_FOUND
    }

    test("the reaper removes expired pending enrollments, after which poll returns 404 instead of 410") {
        val fixedNow = Instant.parse("2026-08-29T12:00:00Z")
        val app = TestApp(clock = Clock.fixed(fixedNow, ZoneOffset.UTC), enrollmentTtlMinutes = 0)
        val installationId = UUID.randomUUID()
        val deviceToken = "a".repeat(43)
        app.handler(Request(Method.POST, "/api/v1/enrollments").body(enrollBody(installationId, deviceToken)))

        val reapCutoffApp = TestApp(clock = Clock.fixed(fixedNow.plusSeconds(1), ZoneOffset.UTC))
        val removed = reapCutoffApp.jdbi.inTransaction<Int, RuntimeException> { handle ->
            app.enrollmentRepository.deleteExpiredPendingBefore(handle, fixedNow.plusSeconds(1))
        }
        removed shouldBe 1
    }
})
