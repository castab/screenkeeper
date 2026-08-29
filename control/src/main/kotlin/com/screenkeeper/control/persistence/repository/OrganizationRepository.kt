package com.screenkeeper.control.persistence.repository

import com.screenkeeper.control.domain.Organization
import org.jdbi.v3.core.Handle
import org.jdbi.v3.core.kotlin.mapTo
import java.util.UUID

class OrganizationRepository {
    fun insert(handle: Handle, name: String): Organization =
        handle.createUpdate("INSERT INTO organizations (name) VALUES (:name)")
            .bind("name", name)
            .executeAndReturnGeneratedKeys("id", "name", "created_at")
            .mapTo<Organization>()
            .one()

    fun findById(handle: Handle, id: UUID): Organization? =
        handle.createQuery("SELECT id, name, created_at FROM organizations WHERE id = :id")
            .bind("id", id)
            .mapTo<Organization>()
            .findFirst()
            .orElse(null)

    fun list(handle: Handle): List<Organization> =
        handle.createQuery("SELECT id, name, created_at FROM organizations ORDER BY created_at")
            .mapTo<Organization>()
            .list()
}
