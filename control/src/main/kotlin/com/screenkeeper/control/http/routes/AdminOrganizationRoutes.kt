package com.screenkeeper.control.http.routes

import com.screenkeeper.control.application.registry.OrganizationService
import com.screenkeeper.control.domain.Organization
import com.screenkeeper.control.http.models.CreateOrganizationRequestDto
import com.screenkeeper.control.http.models.Json.auto
import com.screenkeeper.control.http.models.OrganizationDto
import kotlinx.datetime.toKotlinInstant
import org.http4k.core.Body
import org.http4k.core.Method
import org.http4k.core.Response
import org.http4k.core.Status
import org.http4k.routing.bind
import org.http4k.routing.routes

private val createRequestLens = Body.auto<CreateOrganizationRequestDto>().toLens()
private val organizationLens = Body.auto<OrganizationDto>().toLens()
private val organizationListLens = Body.auto<List<OrganizationDto>>().toLens()

object AdminOrganizationRoutes {
    fun build(service: OrganizationService) = "/api/v1/admin/organizations" bind routes(
        Method.POST to { request ->
            val dto = createRequestLens(request)
            organizationLens(service.create(dto.name).toDto(), Response(Status.CREATED))
        },
        Method.GET to { _ ->
            organizationListLens(service.list().map { it.toDto() }, Response(Status.OK))
        },
    )

    private fun Organization.toDto() = OrganizationDto(id.toString(), name, createdAt.toKotlinInstant())
}
