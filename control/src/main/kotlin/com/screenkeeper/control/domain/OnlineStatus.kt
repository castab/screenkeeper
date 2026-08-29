package com.screenkeeper.control.domain

import java.time.Instant

/**
 * online iff a heartbeat has been received within (heartbeatIntervalSeconds *
 * onlineMultiplier) of `now`. Computed at read time only -- never persisted,
 * so it can never go stale between heartbeats.
 */
fun isOnline(
    lastSeenAt: Instant?,
    now: Instant,
    heartbeatIntervalSeconds: Long,
    onlineMultiplier: Double,
): Boolean {
    if (lastSeenAt == null) return false
    val thresholdSeconds = (heartbeatIntervalSeconds * onlineMultiplier)
    val ageSeconds = java.time.Duration.between(lastSeenAt, now).seconds.toDouble()
    return ageSeconds <= thresholdSeconds
}
