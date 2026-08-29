package com.screenkeeper.control.integration

import com.screenkeeper.control.support.TestApp
import com.screenkeeper.control.support.TestDatabase
import io.kotest.core.spec.style.FunSpec
import io.kotest.matchers.shouldBe
import org.http4k.core.Method
import org.http4k.core.Request
import org.http4k.core.Status

/**
 * TestDatabase.jdbi migrates on first access (shared by every spec in this
 * source set), so a "before migration" 503 isn't representable here without
 * a throwaway schema -- this spec covers the steady-state reachable-and-
 * migrated case, which is what /readyz is for in normal operation.
 */
class HealthCheckIntegrationSpec : FunSpec({
    beforeTest { TestDatabase.truncateAll() }

    test("healthz reports the process alive without touching the database") {
        val app = TestApp()
        app.handler(Request(Method.GET, "/healthz")).status shouldBe Status.OK
    }

    test("readyz reports ready once Postgres is reachable and migrations are applied") {
        val app = TestApp()
        val response = app.handler(Request(Method.GET, "/readyz"))
        response.status shouldBe Status.OK
        response.bodyString() shouldBe "ready"
    }

    test("health endpoints require no authentication") {
        val app = TestApp()
        app.handler(Request(Method.GET, "/healthz")).status shouldBe Status.OK
        app.handler(Request(Method.GET, "/readyz")).status shouldBe Status.OK
    }
})
