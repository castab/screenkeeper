package com.screenkeeper.control.security

import com.screenkeeper.control.domain.Enrollment
import com.screenkeeper.control.http.RequestContext
import io.kotest.core.spec.style.FunSpec
import io.kotest.matchers.shouldBe
import org.http4k.core.Method
import org.http4k.core.Request
import org.http4k.core.Response
import org.http4k.core.Status
import org.http4k.core.then
import org.http4k.routing.bind
import org.http4k.routing.routes
import java.time.Clock
import java.time.Instant
import java.time.ZoneOffset
import java.util.UUID

class EnrollmentBearerFilterSpec : FunSpec({
    val fixedNow = Instant.parse("2026-08-29T12:00:00Z")
    val clock = Clock.fixed(fixedNow, ZoneOffset.UTC)
    val deviceToken = "a".repeat(43) // matches secrets.token_urlsafe(32) length
    val enrollmentId = UUID.fromString("019254c1-7b3a-7c9e-9f21-3a4b5c6d7e8f")

    fun enrollment(expiresAt: Instant = fixedNow.plusSeconds(60)) = Enrollment(
        id = enrollmentId,
        installationId = UUID.randomUUID(),
        deviceTokenHash = sha256(deviceToken),
        codeHash = sha256("Q7KM-4HF2"),
        expiresAt = expiresAt,
        claimedAt = null,
        playerId = null,
        screenkeeperVersion = "0.1.0",
        hostname = "menu-player-01",
        createdAt = fixedNow,
    )

    val next = { request: Request ->
        val resolved = RequestContext.enrollmentKey(request)
        Response(Status.OK).body(resolved.id.toString())
    }

    // Path.of("enrollment_id") only works on a request that has actually been
    // matched by a router (it reads the matched uri-template, not just the
    // raw path) -- so the test binds a real route rather than calling the
    // filter chain directly on a hand-built Request. No database is involved.
    fun appWith(resolve: (UUID) -> Enrollment?) = routes(
        "/api/v1/enrollments/{enrollment_id}" bind Method.GET to
            EnrollmentBearerFilter(clock, resolve).then(next),
    )

    test("unknown enrollment id is rejected with 404 before any credential check") {
        val app = appWith { null }
        val response = app(Request(Method.GET, "/api/v1/enrollments/$enrollmentId"))
        response.status shouldBe Status.NOT_FOUND
    }

    test("missing bearer token is rejected with 401") {
        val app = appWith { enrollment() }
        val response = app(Request(Method.GET, "/api/v1/enrollments/$enrollmentId"))
        response.status shouldBe Status.UNAUTHORIZED
    }

    test("wrong bearer token is rejected with 401") {
        val app = appWith { enrollment() }
        val response = app(
            Request(Method.GET, "/api/v1/enrollments/$enrollmentId").header("Authorization", "Bearer wrong-token"),
        )
        response.status shouldBe Status.UNAUTHORIZED
    }

    test("correct bearer token on an expired enrollment is rejected with 410") {
        val app = appWith { enrollment(expiresAt = fixedNow.minusSeconds(1)) }
        val response = app(
            Request(Method.GET, "/api/v1/enrollments/$enrollmentId").header("Authorization", "Bearer $deviceToken"),
        )
        response.status shouldBe Status.GONE
    }

    test("correct bearer token on a pending, unexpired enrollment is accepted") {
        val app = appWith { enrollment() }
        val response = app(
            Request(Method.GET, "/api/v1/enrollments/$enrollmentId").header("Authorization", "Bearer $deviceToken"),
        )
        response.status shouldBe Status.OK
        response.bodyString() shouldBe enrollmentId.toString()
    }

    test("a malformed enrollment id in the path is rejected with 404") {
        val app = appWith { enrollment() }
        val response = app(
            Request(Method.GET, "/api/v1/enrollments/not-a-uuid").header("Authorization", "Bearer $deviceToken"),
        )
        response.status shouldBe Status.NOT_FOUND
    }
})
