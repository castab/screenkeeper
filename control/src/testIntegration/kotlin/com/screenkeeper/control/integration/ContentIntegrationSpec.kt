package com.screenkeeper.control.integration

import com.screenkeeper.control.storage.ObjectStore
import com.screenkeeper.control.storage.SignedObjectRequest
import com.screenkeeper.control.storage.StoredObject
import com.screenkeeper.control.support.TestApp
import com.screenkeeper.control.support.TestDatabase
import io.kotest.core.spec.style.FunSpec
import io.kotest.matchers.shouldBe
import io.kotest.matchers.string.shouldContain
import org.http4k.core.Method
import org.http4k.core.Request
import org.http4k.core.Status
import java.time.Instant
import java.util.Base64
import java.util.UUID

private const val SHA = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"

private class FakeObjectStore : ObjectStore {
    var stored: StoredObject? = null
    override suspend fun head(key: String) = stored
    override suspend fun presignPut(
        key: String, contentType: String, byteSize: Long, sha256: String, ttlSeconds: Long,
    ) = SignedObjectRequest(
        "PUT", "https://bucket.example/$key?upload=secret",
        mapOf("x-amz-checksum-sha256" to Base64.getEncoder().encodeToString(sha256.chunked(2).map { it.toInt(16).toByte() }.toByteArray())),
        Instant.parse("2026-08-29T13:00:00Z"),
    )
    override suspend fun presignGet(key: String, ttlSeconds: Long) = SignedObjectRequest(
        "GET", "https://bucket.example/$key?download=secret", emptyMap(),
        Instant.parse("2026-08-29T13:00:00Z"),
    )
}

class ContentIntegrationSpec : FunSpec({
    beforeTest { TestDatabase.truncateAll() }

    fun claimPlayer(app: TestApp): Pair<UUID, String> {
        val organization = app.organizationService.create("Example")
        val location = app.locationService.create(organization.id, "Downtown")
        val token = "test-token-${UUID.randomUUID()}"
        val ticket = app.enrollmentService.createOrReuse(UUID.randomUUID(), token, "0.1.0", "host")
        return app.enrollmentService.claim(ticket.code, location.id, "Menu").player.id to token
    }

    fun reportBindings(app: TestApp, playerId: UUID, token: String) {
        app.handler(
            Request(Method.POST, "/api/v1/players/$playerId/heartbeat")
                .header("Authorization", "Bearer $token")
                .body(
                    """{"reported_at":"2026-08-29T12:00:00Z","agent":{"screenkeeper_version":"0.1.0","hostname":"host","os":"Linux"},"inventory":{"gpus":[],"connectors":[]},"configured_bindings":{"tvs":[],"playback_players":[{"id":"dev-menu","name":"Menu"}]}}""",
                ),
        ).status shouldBe Status.ACCEPTED
    }

    test("asset upload finalization assignment manifest and status form one desired-state flow") {
        val storage = FakeObjectStore()
        val app = TestApp(objectStore = storage)
        val (playerId, token) = claimPlayer(app)
        reportBindings(app, playerId, token)
        val admin = "Bearer ${app.adminToken}"

        val assetResponse = app.handler(
            Request(Method.POST, "/api/v1/admin/assets").header("Authorization", admin)
                .body("""{"name":"summer-menu"}"""),
        )
        assetResponse.status shouldBe Status.CREATED
        val assetId = Regex("\"id\":\"([^\"]+)\"").find(assetResponse.bodyString())!!.groupValues[1]

        val revisionResponse = app.handler(
            Request(Method.POST, "/api/v1/admin/assets/$assetId/revisions").header("Authorization", admin)
                .body("""{"original_filename":"menu.mp4","content_type":"video/mp4","byte_size":4,"sha256":"$SHA"}"""),
        )
        revisionResponse.status shouldBe Status.CREATED
        revisionResponse.bodyString() shouldContain "https://bucket.example/assets/9f/$SHA"
        val revisionId = Regex("\"asset_revision_id\":\"([^\"]+)\"").find(revisionResponse.bodyString())!!.groupValues[1]

        storage.stored = StoredObject(3, "n4bQgYhMfWWaL+qgxVrQFaO/TxsrC4Is0V1sFbDwCgg=", "video/mp4")
        app.handler(
            Request(Method.POST, "/api/v1/admin/assets/$assetId/revisions/$revisionId/complete")
                .header("Authorization", admin),
        ).status shouldBe Status.CONFLICT

        storage.stored = StoredObject(4, "n4bQgYhMfWWaL+qgxVrQFaO/TxsrC4Is0V1sFbDwCgg=", "video/mp4")
        app.handler(
            Request(Method.POST, "/api/v1/admin/assets/$assetId/revisions/$revisionId/complete")
                .header("Authorization", admin),
        ).status shouldBe Status.OK

        val assignment = app.handler(
            Request(Method.PUT, "/api/v1/admin/players/$playerId/content/dev-menu")
                .header("Authorization", admin)
                .body("""{"asset_revision_id":"$revisionId"}"""),
        )
        assignment.status shouldBe Status.OK
        assignment.bodyString() shouldContain "\"revision\":1"
        app.handler(
            Request(Method.PUT, "/api/v1/admin/players/$playerId/content/dev-menu")
                .header("Authorization", admin)
                .body("""{"asset_revision_id":"$revisionId"}"""),
        ).bodyString() shouldContain "\"revision\":1"
        app.handler(
            Request(Method.PUT, "/api/v1/admin/players/$playerId/content/not-reported")
                .header("Authorization", admin)
                .body("""{"asset_revision_id":"$revisionId"}"""),
        ).status shouldBe Status.NOT_FOUND

        val manifest = app.handler(
            Request(Method.GET, "/api/v1/players/$playerId/content")
                .header("Authorization", "Bearer $token"),
        )
        manifest.status shouldBe Status.OK
        manifest.bodyString() shouldContain "dev-menu"
        manifest.bodyString() shouldContain "?download=secret"

        val (otherPlayerId, otherToken) = claimPlayer(app)
        val isolatedManifest = app.handler(
            Request(Method.GET, "/api/v1/players/$otherPlayerId/content")
                .header("Authorization", "Bearer $otherToken"),
        )
        isolatedManifest.status shouldBe Status.OK
        isolatedManifest.bodyString() shouldContain "\"revision\":0"
        isolatedManifest.bodyString() shouldContain "\"players\":[]"

        val status = app.handler(
            Request(Method.PUT, "/api/v1/players/$playerId/content-status")
                .header("Authorization", "Bearer $token")
                .body("""{"reported_at":"2026-08-29T12:30:00Z","manifest_revision":1,"players":[{"player_id":"dev-menu","status":"active"}]}"""),
        )
        status.status shouldBe Status.NO_CONTENT
        app.playerRegistryService.get(playerId)!!.latestContentStatusJson shouldContain "dev-menu"

        app.handler(
            Request(Method.PUT, "/api/v1/players/$playerId/content-status")
                .header("Authorization", "Bearer $token")
                .body("""{"reported_at":"2026-08-29T12:31:00Z","manifest_revision":1,"players":[{"player_id":"dev-menu","status":"failed","last_error":"checksum mismatch"}]}"""),
        ).status shouldBe Status.NO_CONTENT
        app.playerRegistryService.get(playerId)!!.latestContentStatusJson shouldContain "checksum mismatch"

        val reusedRevision = app.handler(
            Request(Method.POST, "/api/v1/admin/assets/$assetId/revisions").header("Authorization", admin)
                .body("""{"original_filename":"menu-copy.mp4","content_type":"video/mp4","byte_size":4,"sha256":"$SHA"}"""),
        )
        reusedRevision.status shouldBe Status.CREATED
        reusedRevision.bodyString() shouldContain "\"revision\":2"
        reusedRevision.bodyString() shouldContain "\"status\":\"available\""
        reusedRevision.bodyString() shouldContain "\"required\":false"
        val reusedRevisionId = Regex("\"asset_revision_id\":\"([^\"]+)\"")
            .find(reusedRevision.bodyString())!!.groupValues[1]

        app.handler(
            Request(Method.PUT, "/api/v1/admin/players/$playerId/content/dev-menu")
                .header("Authorization", admin)
                .body("""{"asset_revision_id":"$reusedRevisionId"}"""),
        ).bodyString() shouldContain "\"revision\":2"
        app.handler(
            Request(Method.DELETE, "/api/v1/admin/players/$playerId/content/dev-menu")
                .header("Authorization", admin),
        ).status shouldBe Status.NO_CONTENT
        val cleared = app.handler(
            Request(Method.GET, "/api/v1/players/$playerId/content")
                .header("Authorization", "Bearer $token"),
        )
        cleared.bodyString() shouldContain "\"revision\":3"
        cleared.bodyString() shouldContain "\"players\":[]"
    }
})
