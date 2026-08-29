package com.screenkeeper.control.config

import io.kotest.assertions.throwables.shouldThrow
import io.kotest.core.spec.style.FunSpec
import io.kotest.matchers.shouldBe
import io.kotest.matchers.string.shouldContain

class AppConfigSpec : FunSpec({
    val validEnv = mapOf(
        "SCREENKEEPER_DATABASE_URL" to "jdbc:postgresql://localhost:5432/screenkeeper_control",
        "SCREENKEEPER_DATABASE_USER" to "screenkeeper",
        "SCREENKEEPER_DATABASE_PASSWORD" to "secret",
        "SCREENKEEPER_ADMIN_TOKEN" to "a-strong-admin-token",
    )

    test("fails startup when SCREENKEEPER_ADMIN_TOKEN is missing") {
        val error = shouldThrow<ConfigError> { AppConfig.fromEnv(validEnv - "SCREENKEEPER_ADMIN_TOKEN") }
        error.message shouldContain "SCREENKEEPER_ADMIN_TOKEN"
    }

    test("fails startup when a required database var is missing") {
        val error = shouldThrow<ConfigError> { AppConfig.fromEnv(validEnv - "SCREENKEEPER_DATABASE_URL") }
        error.message shouldContain "SCREENKEEPER_DATABASE_URL"
    }

    test("fails startup when a required var is blank") {
        val error = shouldThrow<ConfigError> {
            AppConfig.fromEnv(validEnv + ("SCREENKEEPER_ADMIN_TOKEN" to "   "))
        }
        error.message shouldContain "SCREENKEEPER_ADMIN_TOKEN"
    }

    test("applies documented defaults for optional vars") {
        val config = AppConfig.fromEnv(validEnv)
        config.host shouldBe "0.0.0.0"
        config.port shouldBe 8080
        config.heartbeatIntervalSeconds shouldBe 30L
        config.onlineMultiplier shouldBe 3.0
        config.enrollmentTtlMinutes shouldBe 15L
        config.enrollmentReapAfterMinutes shouldBe 60L
        config.metricsEnabled shouldBe true
        config.metricsHost shouldBe "127.0.0.1"
        config.metricsPort shouldBe 9464
    }

    test("honors overrides for optional vars") {
        val config = AppConfig.fromEnv(
            validEnv + mapOf(
                "SCREENKEEPER_CONTROL_HOST" to "127.0.0.1",
                "SCREENKEEPER_CONTROL_PORT" to "9090",
                "SCREENKEEPER_HEARTBEAT_INTERVAL_SECONDS" to "45",
                "SCREENKEEPER_ONLINE_MULTIPLIER" to "2.5",
            ),
        )
        config.host shouldBe "127.0.0.1"
        config.port shouldBe 9090
        config.heartbeatIntervalSeconds shouldBe 45L
        config.onlineMultiplier shouldBe 2.5
    }

    test("honors overrides for metrics vars") {
        val config = AppConfig.fromEnv(
            validEnv + mapOf(
                "SCREENKEEPER_CONTROL_METRICS_ENABLED" to "false",
                "SCREENKEEPER_CONTROL_METRICS_HOST" to "0.0.0.0",
                "SCREENKEEPER_CONTROL_METRICS_PORT" to "9999",
            ),
        )
        config.metricsEnabled shouldBe false
        config.metricsHost shouldBe "0.0.0.0"
        config.metricsPort shouldBe 9999
    }

    test("toString never includes the database password or admin token") {
        val config = AppConfig.fromEnv(validEnv)
        val text = config.toString()
        text shouldContain "AppConfig("
        (text.contains("secret") || text.contains("a-strong-admin-token")) shouldBe false
    }
})
