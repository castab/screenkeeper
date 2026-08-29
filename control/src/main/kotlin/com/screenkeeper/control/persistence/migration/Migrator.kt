package com.screenkeeper.control.persistence.migration

import org.flywaydb.core.Flyway
import javax.sql.DataSource

class Migrator(private val dataSource: DataSource) {
    private val flyway: Flyway = Flyway.configure()
        .dataSource(dataSource)
        .locations("classpath:db/migration")
        .load()

    /** Fails startup loudly if migrations don't apply cleanly -- see Main.kt. */
    fun migrate() {
        flyway.migrate()
    }

    /** Used by /readyz: true only once every known migration has been applied. */
    fun isUpToDate(): Boolean =
        flyway.info().pending().isEmpty()
}
