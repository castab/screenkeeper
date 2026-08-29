package com.screenkeeper.control.http.filters

import org.http4k.core.Filter
import org.slf4j.LoggerFactory
import java.time.Duration
import java.time.Instant

private val logger = LoggerFactory.getLogger("RequestLog")

/** Method/path/status/duration only -- never headers or the body, which may carry credentials. */
val RequestLoggingFilter: Filter = Filter { next ->
    { request ->
        val start = Instant.now()
        val response = next(request)
        val durationMs = Duration.between(start, Instant.now()).toMillis()
        logger.info("{} {} -> {} ({} ms)", request.method, request.uri.path, response.status.code, durationMs)
        response
    }
}
