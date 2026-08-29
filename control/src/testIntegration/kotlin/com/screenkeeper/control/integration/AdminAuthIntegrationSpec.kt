package com.screenkeeper.control.integration

import com.screenkeeper.control.support.TestApp
import com.screenkeeper.control.support.TestDatabase
import io.kotest.core.spec.style.FunSpec
import io.kotest.matchers.shouldBe
import org.http4k.core.Method
import org.http4k.core.Request
import org.http4k.core.Status

class AdminAuthIntegrationSpec : FunSpec({
    beforeTest { TestDatabase.truncateAll() }

    test("missing admin token is rejected") {
        val app = TestApp()
        val response = app.handler(Request(Method.GET, "/api/v1/admin/organizations"))
        response.status shouldBe Status.UNAUTHORIZED
    }

    test("wrong admin token is rejected") {
        val app = TestApp()
        val response = app.handler(
            Request(Method.GET, "/api/v1/admin/organizations").header("Authorization", "Bearer wrong-token"),
        )
        response.status shouldBe Status.UNAUTHORIZED
    }

    test("correct admin token is accepted") {
        val app = TestApp(adminToken = "correct-token")
        val response = app.handler(
            Request(Method.GET, "/api/v1/admin/organizations").header("Authorization", "Bearer correct-token"),
        )
        response.status shouldBe Status.OK
    }

    test("health and enrollment-create routes never require the admin token") {
        val app = TestApp()
        app.handler(Request(Method.GET, "/healthz")).status shouldBe Status.OK
        app.handler(
            Request(Method.POST, "/api/v1/enrollments").body(
                """{"installation_id":"${java.util.UUID.randomUUID()}","device_token":"${"a".repeat(43)}","screenkeeper_version":"0.1.0","hostname":"h"}""",
            ),
        ).status shouldBe Status.CREATED
    }
})
