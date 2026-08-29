package com.screenkeeper.control.http.routes

import com.screenkeeper.control.persistence.migration.Migrator
import org.http4k.core.Method
import org.http4k.core.Response
import org.http4k.core.Status
import org.http4k.routing.bind
import org.http4k.routing.routes
import javax.sql.DataSource

object HealthRoutes {
    fun build(dataSource: DataSource, migrator: Migrator) = routes(
        "/healthz" bind Method.GET to {
            Response(Status.OK).body("ok")
        },
        "/readyz" bind Method.GET to {
            val ready = try {
                dataSource.connection.use { it.isValid(2) } && migrator.isUpToDate()
            } catch (e: Exception) {
                false
            }
            if (ready) Response(Status.OK).body("ready") else Response(Status.SERVICE_UNAVAILABLE).body("not ready")
        },
    )
}
