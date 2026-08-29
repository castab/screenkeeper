package com.screenkeeper.control.application.registry

import com.screenkeeper.control.domain.Organization
import com.screenkeeper.control.persistence.repository.OrganizationRepository
import org.jdbi.v3.core.Jdbi

class OrganizationService(
    private val jdbi: Jdbi,
    private val organizationRepository: OrganizationRepository,
) {
    fun create(name: String): Organization =
        jdbi.inTransaction<Organization, RuntimeException> { handle -> organizationRepository.insert(handle, name) }

    fun list(): List<Organization> =
        jdbi.withHandle<List<Organization>, RuntimeException> { handle -> organizationRepository.list(handle) }
}
