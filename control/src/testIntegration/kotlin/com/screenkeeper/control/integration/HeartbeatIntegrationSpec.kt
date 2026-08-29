package com.screenkeeper.control.integration

import com.screenkeeper.control.support.TestApp
import com.screenkeeper.control.support.TestDatabase
import io.kotest.core.spec.style.FunSpec
import io.kotest.matchers.shouldBe
import io.kotest.matchers.string.shouldContain
import org.http4k.core.Method
import org.http4k.core.Request
import org.http4k.core.Status
import java.time.Clock
import java.time.Instant
import java.time.ZoneOffset
import java.util.UUID

private fun heartbeatBody(reportedAt: String = "2026-08-29T12:00:00Z", connectorName: String = "DP-1") = """
    {
      "reported_at": "$reportedAt",
      "agent": {"screenkeeper_version": "0.1.0", "hostname": "menu-player-01", "os": "Linux"},
      "inventory": {"gpus": [], "connectors": [{"name": "$connectorName", "status": "connected"}]},
      "configured_bindings": {"tvs": [], "playback_players": []}
    }
""".trimIndent()

class HeartbeatIntegrationSpec : FunSpec({
    beforeTest { TestDatabase.truncateAll() }

    fun claimAPlayer(app: TestApp): Pair<UUID, String> {
        val org = app.organizationService.create("Example Restaurant")
        val location = app.locationService.create(org.id, "Downtown")
        val deviceToken = "a".repeat(43)
        val ticket = app.enrollmentService.createOrReuse(UUID.randomUUID(), deviceToken, "0.1.0", "menu-player-01")
        val result = app.enrollmentService.claim(ticket.code, location.id, "Main Menu Wall")
        return result.player.id to deviceToken
    }

    test("a heartbeat updates last_seen_at using the server clock, not the reported_at field") {
        val serverNow = Instant.parse("2026-08-29T15:00:00Z")
        val app = TestApp(clock = Clock.fixed(serverNow, ZoneOffset.UTC))
        val (playerId, token) = claimAPlayer(app)

        app.handler(
            Request(Method.POST, "/api/v1/players/$playerId/heartbeat")
                .header("Authorization", "Bearer $token")
                .body(heartbeatBody(reportedAt = "2020-01-01T00:00:00Z")),
        )

        val detail = app.playerRegistryService.get(playerId)!!
        detail.summary.player.lastSeenAt shouldBe serverNow
    }

    test("a second heartbeat replaces the previous report rather than accumulating history") {
        val app = TestApp()
        val (playerId, token) = claimAPlayer(app)

        app.handler(
            Request(Method.POST, "/api/v1/players/$playerId/heartbeat")
                .header("Authorization", "Bearer $token")
                .body(heartbeatBody(connectorName = "DP-1")),
        )
        app.handler(
            Request(Method.POST, "/api/v1/players/$playerId/heartbeat")
                .header("Authorization", "Bearer $token")
                .body(heartbeatBody(connectorName = "HDMI-1")),
        )

        val detail = app.playerRegistryService.get(playerId)!!
        val report = detail.latestReportJson!!
        report shouldContain "HDMI-1"
        (report.contains("DP-1")) shouldBe false
    }

    test("player version and hostname are updated from the heartbeat's agent info") {
        val app = TestApp()
        val (playerId, token) = claimAPlayer(app)

        app.handler(
            Request(Method.POST, "/api/v1/players/$playerId/heartbeat")
                .header("Authorization", "Bearer $token")
                .body(
                    """
                    {
                      "reported_at": "2026-08-29T12:00:00Z",
                      "agent": {"screenkeeper_version": "0.2.0", "hostname": "renamed-host", "os": "Linux"},
                      "inventory": {"gpus": [], "connectors": []},
                      "configured_bindings": {"tvs": [], "playback_players": []}
                    }
                    """.trimIndent(),
                ),
        )

        val detail = app.playerRegistryService.get(playerId)!!
        detail.summary.player.screenkeeperVersion shouldBe "0.2.0"
        detail.summary.player.hostname shouldBe "renamed-host"
    }

    test("malformed JSON is rejected with 400, not a 500") {
        val app = TestApp()
        val (playerId, token) = claimAPlayer(app)

        val response = app.handler(
            Request(Method.POST, "/api/v1/players/$playerId/heartbeat")
                .header("Authorization", "Bearer $token")
                .body("not json at all"),
        )
        response.status shouldBe Status.BAD_REQUEST
    }

    test("an oversized payload is rejected with 400 according to the documented limit") {
        val app = TestApp()
        val (playerId, token) = claimAPlayer(app)

        val hugeConnectorName = "x".repeat(300_000)
        val response = app.handler(
            Request(Method.POST, "/api/v1/players/$playerId/heartbeat")
                .header("Authorization", "Bearer $token")
                .body(heartbeatBody(connectorName = hugeConnectorName)),
        )
        response.status.code shouldBe 413
    }

    test("an empty connector inventory is accepted") {
        val app = TestApp()
        val (playerId, token) = claimAPlayer(app)

        val response = app.handler(
            Request(Method.POST, "/api/v1/players/$playerId/heartbeat")
                .header("Authorization", "Bearer $token")
                .body(
                    """
                    {
                      "reported_at": "2026-08-29T12:00:00Z",
                      "agent": {"screenkeeper_version": "0.1.0", "hostname": "h", "os": "Linux"},
                      "inventory": {"gpus": [], "connectors": []},
                      "configured_bindings": {"tvs": [], "playback_players": []}
                    }
                    """.trimIndent(),
                ),
        )
        response.status shouldBe Status.ACCEPTED
    }
})
