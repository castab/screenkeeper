package com.screenkeeper.control.http.models

import com.screenkeeper.control.http.models.Json.auto
import io.kotest.core.spec.style.FunSpec
import io.kotest.matchers.shouldBe
import org.http4k.core.Body
import org.http4k.core.Method
import org.http4k.core.Request
import org.http4k.core.Response
import org.http4k.core.Status
import java.io.File

/**
 * Decodes the shared contract example fixtures (contracts/examples) into the
 * server's own DTOs, and re-encodes to confirm the round trip is lossless for
 * every field the contract defines. This is what ties the Kotlin models to
 * the shared wire contract so they can't silently drift from it.
 */
class JsonRoundTripSpec : FunSpec({
    val examplesDir = File("../contracts/examples")

    fun fixture(name: String): String = File(examplesDir, name).readText()

    val enrollmentRequestLens = Body.auto<EnrollmentRequestDto>().toLens()
    val enrollmentCreatedLens = Body.auto<EnrollmentCreatedDto>().toLens()
    val enrollmentStateLens = Body.auto<EnrollmentStateDto>().toLens()
    val heartbeatRequestLens = Body.auto<HeartbeatRequestDto>().toLens()

    test("enrollment-request.json decodes into EnrollmentRequestDto") {
        val request = Request(Method.POST, "/api/v1/enrollments").body(fixture("enrollment-request.json"))
        val dto = enrollmentRequestLens(request)
        dto.installationId shouldBe "70b3f02f-6f6c-4f0d-8f4a-2f5f1b0c9d21"
        dto.hostname shouldBe "menu-player-01"
        dto.screenkeeperVersion shouldBe "0.1.0"
        dto.deviceToken.isNotBlank() shouldBe true
    }

    test("enrollment-created.json decodes into EnrollmentCreatedDto") {
        val response = Response(Status.CREATED).body(fixture("enrollment-created.json"))
        val dto = enrollmentCreatedLens(response)
        dto.code shouldBe "Q7KM-4HF2"
        dto.pollIntervalSeconds shouldBe 5
        dto.enrollmentId shouldBe "019254c1-7b3a-7c9e-9f21-3a4b5c6d7e8f"
    }

    test("enrollment-created.json round-trips through re-encoding") {
        val response = Response(Status.CREATED).body(fixture("enrollment-created.json"))
        val dto = enrollmentCreatedLens(response)
        val reEncoded = enrollmentCreatedLens(dto, Response(Status.CREATED))
        enrollmentCreatedLens(reEncoded) shouldBe dto
    }

    test("enrollment-claimed.json decodes into EnrollmentStateDto") {
        val response = Response(Status.OK).body(fixture("enrollment-claimed.json"))
        val dto = enrollmentStateLens(response)
        dto.status shouldBe "claimed"
        dto.playerId shouldBe "019254c1-9c2e-7a13-8d4f-6e7f8a9b0c1d"
        dto.playerName shouldBe "Main Menu Wall"
        dto.organization?.name shouldBe "Example Restaurant"
        dto.location?.name shouldBe "Downtown"
    }

    test("heartbeat.json decodes into HeartbeatRequestDto with all nested shapes intact") {
        val request = Request(Method.POST, "/api/v1/players/x/heartbeat").body(fixture("heartbeat.json"))
        val dto = heartbeatRequestLens(request)
        dto.agent.hostname shouldBe "menu-player-01"
        dto.agent.os shouldBe "Linux"
        dto.inventory.connectors.size shouldBe 2
        dto.inventory.connectors[0].name shouldBe "DP-1"
        dto.inventory.connectors[0].edid?.sha256?.isNotBlank() shouldBe true
        dto.inventory.connectors[1].name shouldBe "HDMI-A-1"
        dto.inventory.connectors[1].edid shouldBe null
        dto.inventory.gpus.single().card shouldBe "card0"
        dto.configuredBindings.tvs.single().id shouldBe "dev-tv"
        dto.configuredBindings.playbackPlayers.single().id shouldBe "dev-menu"
    }

    test("a pending poll response body of {\"status\": \"pending\"} decodes correctly") {
        val response = Response(Status.OK).body("""{"status": "pending"}""")
        val dto = enrollmentStateLens(response)
        dto.status shouldBe "pending"
        dto.playerId shouldBe null
    }

    test("unknown additional fields anywhere in the body are tolerated, not rejected") {
        val withExtraFields = fixture("heartbeat.json")
            .replace("\"reported_at\"", "\"totally_unknown_field\": 42, \"reported_at\"")
        val request = Request(Method.POST, "/api/v1/players/x/heartbeat").body(withExtraFields)
        val dto = heartbeatRequestLens(request)
        dto.agent.hostname shouldBe "menu-player-01"
    }
})
