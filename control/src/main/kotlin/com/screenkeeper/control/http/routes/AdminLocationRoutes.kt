package com.screenkeeper.control.http.routes

import com.screenkeeper.control.application.registry.LocationService
import com.screenkeeper.control.domain.DomainError
import com.screenkeeper.control.domain.Location
import com.screenkeeper.control.http.models.CreateLocationRequestDto
import com.screenkeeper.control.http.models.Json.auto
import com.screenkeeper.control.http.models.LocationDto
import kotlinx.datetime.toKotlinInstant
import org.http4k.core.Body
import org.http4k.core.Method
import org.http4k.core.Response
import org.http4k.core.Status
import org.http4k.routing.bind
import org.http4k.routing.routes
import java.util.UUID

private val createRequestLens = Body.auto<CreateLocationRequestDto>().toLens()
private val locationLens = Body.auto<LocationDto>().toLens()
private val locationListLens = Body.auto<List<LocationDto>>().toLens()

object AdminLocationRoutes {
    fun build(service: LocationService) = "/api/v1/admin/locations" bind routes(
        Method.POST to { request ->
            val dto = createRequestLens(request)
            val organizationId = runCatching { UUID.fromString(dto.organizationId) }
                .getOrElse { throw DomainError.MalformedRequest("organization_id must be a UUID.") }
            locationLens(service.create(organizationId, dto.name).toDto(), Response(Status.CREATED))
        },
        Method.GET to { _ ->
            locationListLens(service.list().map { it.toDto() }, Response(Status.OK))
        },
    )

    private fun Location.toDto() = LocationDto(id.toString(), organizationId.toString(), name, createdAt.toKotlinInstant())
}
