package com.screenkeeper.control.persistence.repository

import com.screenkeeper.control.domain.Location
import org.jdbi.v3.core.Handle
import org.jdbi.v3.core.kotlin.mapTo
import java.util.UUID

private const val COLUMNS = "id, organization_id AS organizationId, name, created_at AS createdAt"

class LocationRepository {
    fun insert(handle: Handle, organizationId: UUID, name: String): Location =
        handle.createUpdate("INSERT INTO locations (organization_id, name) VALUES (:organizationId, :name)")
            .bind("organizationId", organizationId)
            .bind("name", name)
            .executeAndReturnGeneratedKeys("id", "organization_id", "name", "created_at")
            .mapTo<Location>()
            .one()

    fun findById(handle: Handle, id: UUID): Location? =
        handle.createQuery("SELECT $COLUMNS FROM locations WHERE id = :id")
            .bind("id", id)
            .mapTo<Location>()
            .findFirst()
            .orElse(null)

    fun list(handle: Handle): List<Location> =
        handle.createQuery("SELECT $COLUMNS FROM locations ORDER BY created_at")
            .mapTo<Location>()
            .list()
}
