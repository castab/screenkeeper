package com.screenkeeper.control.support

import com.screenkeeper.control.persistence.jdbi.JdbiFactory
import com.screenkeeper.control.persistence.migration.Migrator
import com.zaxxer.hikari.HikariConfig
import com.zaxxer.hikari.HikariDataSource
import org.jdbi.v3.core.Jdbi
import javax.sql.DataSource

/**
 * Connects to an externally provided Postgres instance -- no Testcontainers.
 * Locally: `docker compose -f control/compose.dev.yaml up -d postgres`.
 * In CI: a GitHub Actions `services:` Postgres container. Either way, these
 * env vars are separate from the app's own runtime vars so a test run can
 * never point at a real deployment by accident.
 */
object TestDatabase {
    val dataSource: DataSource by lazy {
        HikariDataSource(
            HikariConfig().apply {
                jdbcUrl = requiredEnv("SCREENKEEPER_TEST_DATABASE_URL")
                username = requiredEnv("SCREENKEEPER_TEST_DATABASE_USER")
                password = requiredEnv("SCREENKEEPER_TEST_DATABASE_PASSWORD")
                maximumPoolSize = 5
                poolName = "screenkeeper-control-test"
            },
        )
    }

    val jdbi: Jdbi by lazy {
        Migrator(dataSource).migrate()
        JdbiFactory.build(dataSource)
    }

    fun truncateAll() {
        jdbi.useHandle<RuntimeException> { handle ->
            handle.execute(
                "TRUNCATE TABLE player_content_reports, player_content_assignments, player_content_manifests, " +
                    "media_asset_revisions, media_assets, player_reports, enrollments, player_credentials, " +
                    "players, locations, organizations " +
                    "RESTART IDENTITY CASCADE",
            )
        }
    }

    private fun requiredEnv(name: String): String =
        System.getenv(name)?.takeIf { it.isNotBlank() }
            ?: throw IllegalStateException(
                "$name must be set to run integration tests, pointed at a running Postgres instance " +
                    "(see control/compose.dev.yaml and control/README.md).",
            )
}
