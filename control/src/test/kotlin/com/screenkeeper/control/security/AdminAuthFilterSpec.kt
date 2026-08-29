package com.screenkeeper.control.security

import io.kotest.core.spec.style.FunSpec
import io.kotest.matchers.shouldBe
import org.http4k.core.Method
import org.http4k.core.Request
import org.http4k.core.Response
import org.http4k.core.Status
import org.http4k.core.then

class AdminAuthFilterSpec : FunSpec({
    val adminToken = "a-strong-admin-token"
    val next = { _: Request -> Response(Status.OK).body("ok") }
    val app = AdminAuthFilter(adminToken).then(next)

    test("missing Authorization header is rejected with 401") {
        val response = app(Request(Method.GET, "/api/v1/admin/organizations"))
        response.status shouldBe Status.UNAUTHORIZED
    }

    test("wrong bearer token is rejected with 401") {
        val response = app(
            Request(Method.GET, "/api/v1/admin/organizations").header("Authorization", "Bearer wrong-token"),
        )
        response.status shouldBe Status.UNAUTHORIZED
    }

    test("malformed Authorization header is rejected with 401") {
        val response = app(
            Request(Method.GET, "/api/v1/admin/organizations").header("Authorization", adminToken),
        )
        response.status shouldBe Status.UNAUTHORIZED
    }

    test("correct bearer token is accepted") {
        val response = app(
            Request(Method.GET, "/api/v1/admin/organizations").header("Authorization", "Bearer $adminToken"),
        )
        response.status shouldBe Status.OK
    }

    test("rejection body never echoes the configured admin token") {
        val response = app(Request(Method.GET, "/api/v1/admin/organizations"))
        response.bodyString().contains(adminToken) shouldBe false
    }
})
