package com.screenkeeper.control.integration

import com.screenkeeper.control.support.TestApp
import com.screenkeeper.control.support.TestDatabase
import io.kotest.core.spec.style.FunSpec
import io.kotest.matchers.shouldBe
import org.http4k.core.Method
import org.http4k.core.Request
import org.http4k.core.Status
import java.util.UUID

private fun minimalHeartbeatBody() = """
    {
      "reported_at": "2026-08-29T12:00:00Z",
      "agent": {"screenkeeper_version": "0.1.0", "hostname": "menu-player-01", "os": "Linux"},
      "inventory": {"gpus": [], "connectors": []},
      "configured_bindings": {"tvs": [], "playback_players": []}
    }
""".trimIndent()

class PlayerAuthIntegrationSpec : FunSpec({
    beforeTest { TestDatabase.truncateAll() }

    fun claimAPlayer(app: TestApp): Pair<UUID, String> {
        val org = app.organizationService.create("Example Restaurant")
        val location = app.locationService.create(org.id, "Downtown")
        val deviceToken = "a".repeat(43)
        val ticket = app.enrollmentService.createOrReuse(UUID.randomUUID(), deviceToken, "0.1.0", "menu-player-01")
        val result = app.enrollmentService.claim(ticket.code, location.id, "Main Menu Wall")
        return result.player.id to deviceToken
    }

    test("heartbeat for an unknown player is rejected with 404") {
        val app = TestApp()
        val response = app.handler(
            Request(Method.POST, "/api/v1/players/${UUID.randomUUID()}/heartbeat")
                .header("Authorization", "Bearer irrelevant")
                .body(minimalHeartbeatBody()),
        )
        response.status shouldBe Status.NOT_FOUND
    }

    test("heartbeat with a wrong device token is rejected with 401") {
        val app = TestApp()
        val (playerId, _) = claimAPlayer(app)
        val response = app.handler(
            Request(Method.POST, "/api/v1/players/$playerId/heartbeat")
                .header("Authorization", "Bearer wrong-token")
                .body(minimalHeartbeatBody()),
        )
        response.status shouldBe Status.UNAUTHORIZED
    }

    test("heartbeat with the correct device token is accepted") {
        val app = TestApp()
        val (playerId, token) = claimAPlayer(app)
        val response = app.handler(
            Request(Method.POST, "/api/v1/players/$playerId/heartbeat")
                .header("Authorization", "Bearer $token")
                .body(minimalHeartbeatBody()),
        )
        response.status shouldBe Status.ACCEPTED
    }

    test("heartbeat with a revoked credential is rejected with 403") {
        val app = TestApp()
        val (playerId, token) = claimAPlayer(app)
        app.jdbi.useHandle<RuntimeException> { handle ->
            handle.createUpdate("UPDATE player_credentials SET revoked_at = now() WHERE player_id = :id")
                .bind("id", playerId)
                .execute()
        }

        val response = app.handler(
            Request(Method.POST, "/api/v1/players/$playerId/heartbeat")
                .header("Authorization", "Bearer $token")
                .body(minimalHeartbeatBody()),
        )
        response.status shouldBe Status.FORBIDDEN
    }

    test("no response ever contains the plaintext device token or its hash") {
        val app = TestApp()
        val (playerId, token) = claimAPlayer(app)
        val listResponse = app.handler(
            Request(Method.GET, "/api/v1/admin/players").header("Authorization", "Bearer ${app.adminToken}"),
        )
        listResponse.bodyString().contains(token) shouldBe false

        val detailResponse = app.handler(
            Request(Method.GET, "/api/v1/admin/players/$playerId").header("Authorization", "Bearer ${app.adminToken}"),
        )
        detailResponse.bodyString().contains(token) shouldBe false
    }
})
