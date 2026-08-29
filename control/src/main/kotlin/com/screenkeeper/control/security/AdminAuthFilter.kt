package com.screenkeeper.control.security

import com.screenkeeper.control.http.models.errorResponse
import org.http4k.core.Filter
import org.http4k.core.Status

/**
 * MVP-only administration authentication: a single static bearer token from
 * SCREENKEEPER_ADMIN_TOKEN. No users, no OAuth, no RBAC. Applied only to the
 * admin route subtree (/api/v1/admin/...) -- never to health checks or the
 * player-facing enrollment/heartbeat routes, which authenticate differently or not at all.
 */
fun AdminAuthFilter(adminToken: String): Filter = Filter { next ->
    { request ->
        val header = request.header("Authorization")
        val provided = header?.takeIf { it.startsWith("Bearer ") }?.removePrefix("Bearer ")
        if (provided == null || !ConstantTime.equals(provided, adminToken)) {
            errorResponse(Status.UNAUTHORIZED, "invalid_credential", "Missing or invalid admin credential.")
        } else {
            next(request)
        }
    }
}
