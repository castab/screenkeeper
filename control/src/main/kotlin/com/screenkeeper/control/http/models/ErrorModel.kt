package com.screenkeeper.control.http.models

import com.screenkeeper.control.http.models.Json.auto
import kotlinx.serialization.Serializable
import org.http4k.core.Body
import org.http4k.core.Response
import org.http4k.core.Status

@Serializable
data class ErrorDto(
    val error: String,
    val message: String? = null,
)

private val errorBodyLens = Body.auto<ErrorDto>().toLens()

/** The one place an {error, message} body is written -- never a stack trace or SQL detail. */
fun errorResponse(status: Status, code: String, message: String? = null): Response =
    errorBodyLens(ErrorDto(code, message), Response(status))
