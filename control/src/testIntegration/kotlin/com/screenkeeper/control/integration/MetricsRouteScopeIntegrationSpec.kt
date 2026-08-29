package com.screenkeeper.control.integration

import com.screenkeeper.control.support.TestApp
import com.screenkeeper.control.support.TestDatabase
import io.kotest.core.spec.style.FunSpec
import io.kotest.matchers.shouldBe
import org.http4k.core.Method
import org.http4k.core.Request
import org.http4k.core.Status

/**
 * The public handler (Routes.build) is reachable over WAN from every enrolled
 * player's heartbeat -- /metrics must never be part of it. The real scrape
 * endpoint is served on a separate listener started only in Main.kt, which
 * this in-process handler never constructs.
 */
class MetricsRouteScopeIntegrationSpec : FunSpec({
    beforeTest { TestDatabase.truncateAll() }

    test("the public handler does not serve /metrics") {
        val app = TestApp()
        app.handler(Request(Method.GET, "/metrics")).status shouldBe Status.NOT_FOUND
    }
})
