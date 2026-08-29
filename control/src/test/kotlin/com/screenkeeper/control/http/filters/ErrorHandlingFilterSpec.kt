package com.screenkeeper.control.http.filters

import com.screenkeeper.control.domain.DomainError
import com.screenkeeper.control.http.models.ErrorDto
import com.screenkeeper.control.http.models.Json.auto
import io.kotest.core.spec.style.FunSpec
import io.kotest.matchers.shouldBe
import kotlinx.serialization.Serializable
import org.http4k.core.Body
import org.http4k.core.Method
import org.http4k.core.Request
import org.http4k.core.Response
import org.http4k.core.Status
import org.http4k.core.then

@Serializable
private data class SampleDto(val requiredField: String)

private val sampleLens = Body.auto<SampleDto>().toLens()
private val errorLens = Body.auto<ErrorDto>().toLens()

class ErrorHandlingFilterSpec : FunSpec({
    fun appThrowing(error: Throwable) = ErrorHandlingFilter.then { _: Request -> throw error }

    val cases = listOf(
        DomainError.AlreadyEnrolled() to Status.CONFLICT,
        DomainError.EnrollmentNotFound() to Status.NOT_FOUND,
        DomainError.EnrollmentExpired() to Status.GONE,
        DomainError.EnrollmentAlreadyClaimed() to Status.CONFLICT,
        DomainError.OrganizationNotFound() to Status.NOT_FOUND,
        DomainError.LocationNotFound() to Status.NOT_FOUND,
        DomainError.PlayerNotFound() to Status.NOT_FOUND,
        DomainError.InvalidCredential() to Status.UNAUTHORIZED,
        DomainError.RevokedCredential() to Status.FORBIDDEN,
        DomainError.MalformedRequest() to Status.BAD_REQUEST,
        DomainError.PayloadTooLarge() to Status(413, "Payload Too Large"),
    )

    cases.forEach { (error, expectedStatus) ->
        test("${error.code} maps to $expectedStatus") {
            val response = appThrowing(error)(Request(Method.GET, "/whatever"))
            response.status shouldBe expectedStatus
            errorLens(response).error shouldBe error.code
        }
    }

    test("an unexpected exception maps to 500 with a generic body, no detail leaked") {
        val response = appThrowing(RuntimeException("password=hunter2 sql=SELECT * FROM secrets"))(
            Request(Method.GET, "/whatever"),
        )
        response.status shouldBe Status.INTERNAL_SERVER_ERROR
        errorLens(response).error shouldBe "internal_error"
        response.bodyString().contains("hunter2") shouldBe false
        response.bodyString().contains("SELECT") shouldBe false
    }

    test("a lens decode failure maps to 400 malformed_request") {
        val handler = ErrorHandlingFilter.then { request: Request ->
            sampleLens(request)
            Response(Status.OK)
        }
        val response = handler(Request(Method.POST, "/whatever").body("not json"))
        response.status shouldBe Status.BAD_REQUEST
        errorLens(response).error shouldBe "malformed_request"
    }
})
