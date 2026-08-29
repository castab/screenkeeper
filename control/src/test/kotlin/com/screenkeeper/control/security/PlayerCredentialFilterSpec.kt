package com.screenkeeper.control.security

import com.screenkeeper.control.domain.Player
import com.screenkeeper.control.domain.PlayerCredential
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
import java.time.Instant
import java.util.UUID

class PlayerCredentialFilterSpec : FunSpec({
    val now = Instant.parse("2026-08-29T12:00:00Z")
    val deviceToken = "b".repeat(43)
    val playerId = UUID.fromString("019254c1-9c2e-7a13-8d4f-6e7f8a9b0c1d")

    val player = Player(
        id = playerId,
        installationId = UUID.randomUUID(),
        locationId = UUID.randomUUID(),
        name = "Main Menu Wall",
        screenkeeperVersion = "0.1.0",
        hostname = "menu-player-01",
        lastSeenAt = now,
        createdAt = now,
        updatedAt = now,
    )

    fun credential(revokedAt: Instant? = null) = PlayerCredential(
        playerId = playerId,
        tokenHash = sha256(deviceToken),
        createdAt = now,
        revokedAt = revokedAt,
    )

    val next = { request: Request ->
        val resolved = RequestContext.playerKey(request)
        Response(Status.OK).body(resolved.id.toString())
    }

    // Same reasoning as EnrollmentBearerFilterSpec: Path.of("player_id") needs
    // an actually-routed request, so tests bind a real route rather than
    // invoking the filter chain directly. Still no database involved.
    fun appWith(resolvePlayer: (UUID) -> Player?, resolveCredential: (UUID) -> PlayerCredential?) = routes(
        "/api/v1/players/{player_id}/heartbeat" bind Method.POST to
            PlayerCredentialFilter(resolvePlayer, resolveCredential).then(next),
    )

    test("unknown player is rejected with 404") {
        val app = appWith({ null }, { null })
        val response = app(Request(Method.POST, "/api/v1/players/$playerId/heartbeat"))
        response.status shouldBe Status.NOT_FOUND
    }

    test("missing credential row for a known player is rejected with 404") {
        val app = appWith({ player }, { null })
        val response = app(Request(Method.POST, "/api/v1/players/$playerId/heartbeat"))
        response.status shouldBe Status.NOT_FOUND
    }

    test("missing bearer token is rejected with 401") {
        val app = appWith({ player }, { credential() })
        val response = app(Request(Method.POST, "/api/v1/players/$playerId/heartbeat"))
        response.status shouldBe Status.UNAUTHORIZED
    }

    test("wrong bearer token is rejected with 401") {
        val app = appWith({ player }, { credential() })
        val response = app(
            Request(Method.POST, "/api/v1/players/$playerId/heartbeat").header("Authorization", "Bearer wrong"),
        )
        response.status shouldBe Status.UNAUTHORIZED
    }

    test("revoked credential with the correct token is rejected with 403") {
        val app = appWith({ player }, { credential(revokedAt = now.minusSeconds(60)) })
        val response = app(
            Request(Method.POST, "/api/v1/players/$playerId/heartbeat")
                .header("Authorization", "Bearer $deviceToken"),
        )
        response.status shouldBe Status.FORBIDDEN
    }

    test("valid, active credential is accepted") {
        val app = appWith({ player }, { credential() })
        val response = app(
            Request(Method.POST, "/api/v1/players/$playerId/heartbeat")
                .header("Authorization", "Bearer $deviceToken"),
        )
        response.status shouldBe Status.OK
        response.bodyString() shouldBe playerId.toString()
    }
})
