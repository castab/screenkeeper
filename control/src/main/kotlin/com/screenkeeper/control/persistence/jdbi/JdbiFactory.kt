package com.screenkeeper.control.persistence.jdbi

import com.screenkeeper.control.config.AppConfig
import com.zaxxer.hikari.HikariConfig
import com.zaxxer.hikari.HikariDataSource
import org.jdbi.v3.core.Jdbi
import org.jdbi.v3.core.kotlin.KotlinPlugin
import org.jdbi.v3.postgres.PostgresPlugin
import javax.sql.DataSource

object JdbiFactory {
    fun dataSource(config: AppConfig): DataSource {
        val hikariConfig = HikariConfig().apply {
            jdbcUrl = config.databaseUrl
            username = config.databaseUser
            password = config.databasePassword
            poolName = "screenkeeper-control"
            maximumPoolSize = 10
        }
        return HikariDataSource(hikariConfig)
    }

    fun build(dataSource: DataSource): Jdbi =
        Jdbi.create(dataSource)
            .installPlugin(KotlinPlugin())
            .installPlugin(PostgresPlugin())
}
