package com.screenkeeper.control.integration

import com.screenkeeper.control.support.TestApp
import com.screenkeeper.control.support.TestDatabase
import io.kotest.core.spec.style.FunSpec
import io.kotest.matchers.shouldBe
import org.http4k.core.Method
import org.http4k.core.Request
import org.http4k.core.Response
import java.util.UUID
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

class EnrollmentConcurrencySpec : FunSpec({
    beforeTest { TestDatabase.truncateAll() }

    test("concurrent identical enrollment requests for one installation never create duplicate pending rows") {
        val app = TestApp()
        val installationId = UUID.randomUUID()
        val deviceToken = "a".repeat(43)
        val body = """
            {"installation_id":"$installationId","device_token":"$deviceToken","screenkeeper_version":"0.1.0","hostname":"h"}
        """.trimIndent()

        val threadCount = 16
        val executor = Executors.newFixedThreadPool(threadCount)
        val responses: List<Response> = try {
            val futures = (1..threadCount).map {
                executor.submit<Response> { app.handler(Request(Method.POST, "/api/v1/enrollments").body(body)) }
            }
            futures.map { it.get(30, TimeUnit.SECONDS) }
        } finally {
            executor.shutdown()
        }

        // no request should have thrown / returned an unhandled error
        responses.all { it.status.code == 201 } shouldBe true

        val pendingCount = app.jdbi.withHandle<Int, RuntimeException> { handle ->
            handle.createQuery("SELECT count(*) FROM enrollments WHERE installation_id = :id AND claimed_at IS NULL")
                .bind("id", installationId)
                .mapTo(Int::class.java)
                .one()
        }
        pendingCount shouldBe 1
    }
})
