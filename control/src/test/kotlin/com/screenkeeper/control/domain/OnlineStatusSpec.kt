package com.screenkeeper.control.domain

import io.kotest.core.spec.style.FunSpec
import io.kotest.matchers.shouldBe
import java.time.Instant

class OnlineStatusSpec : FunSpec({
    val now = Instant.parse("2026-08-29T12:00:00Z")
    val heartbeatIntervalSeconds = 30L
    val onlineMultiplier = 3.0
    // threshold = 90 seconds

    test("null last-seen is offline") {
        isOnline(null, now, heartbeatIntervalSeconds, onlineMultiplier) shouldBe false
    }

    test("last seen well within the threshold is online") {
        isOnline(now.minusSeconds(10), now, heartbeatIntervalSeconds, onlineMultiplier) shouldBe true
    }

    test("last seen exactly at the threshold is online") {
        isOnline(now.minusSeconds(90), now, heartbeatIntervalSeconds, onlineMultiplier) shouldBe true
    }

    test("last seen just past the threshold is offline") {
        isOnline(now.minusSeconds(91), now, heartbeatIntervalSeconds, onlineMultiplier) shouldBe false
    }

    test("last seen well past the threshold is offline") {
        isOnline(now.minusSeconds(3600), now, heartbeatIntervalSeconds, onlineMultiplier) shouldBe false
    }
})
