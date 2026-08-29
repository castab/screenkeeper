package com.screenkeeper.control.application.registry

import com.screenkeeper.control.domain.DomainError
import com.screenkeeper.control.domain.Location
import com.screenkeeper.control.persistence.repository.LocationRepository
import com.screenkeeper.control.persistence.repository.OrganizationRepository
import org.jdbi.v3.core.Jdbi
import java.util.UUID

class LocationService(
    private val jdbi: Jdbi,
    private val locationRepository: LocationRepository,
    private val organizationRepository: OrganizationRepository,
) {
    fun create(organizationId: UUID, name: String): Location =
        jdbi.inTransaction<Location, RuntimeException> { handle ->
            organizationRepository.findById(handle, organizationId) ?: throw DomainError.OrganizationNotFound()
            locationRepository.insert(handle, organizationId, name)
        }

    fun list(): List<Location> =
        jdbi.withHandle<List<Location>, RuntimeException> { handle -> locationRepository.list(handle) }
}
