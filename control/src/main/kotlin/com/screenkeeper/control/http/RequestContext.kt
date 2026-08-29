package com.screenkeeper.control.http

import com.screenkeeper.control.domain.Enrollment
import com.screenkeeper.control.domain.Player
import org.http4k.lens.RequestKey

/**
 * The auth filters resolve an Enrollment/Player once and attach it here so
 * route handlers never re-query for the identity their own filter already
 * authenticated.
 */
object RequestContext {
    val enrollmentKey = RequestKey.required<Enrollment>("enrollment")
    val playerKey = RequestKey.required<Player>("player")
}
