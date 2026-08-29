package com.screenkeeper.control.application.enrollment

import com.screenkeeper.control.persistence.repository.EnrollmentRepository
import org.jdbi.v3.core.Jdbi
import org.slf4j.LoggerFactory
import java.time.Clock
import java.time.Duration
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * Deletes pending, expired enrollment rows once they're old enough that no
 * appliance could still be legitimately polling them. Until reaped, an
 * expired-but-present row yields 410 on poll; once reaped (or never having
 * existed), it's 404 -- see EnrollmentBearerFilter.
 */
class EnrollmentReaper(
    private val jdbi: Jdbi,
    private val enrollmentRepository: EnrollmentRepository,
    private val clock: Clock,
    private val reapAfter: Duration,
    private val interval: Duration = Duration.ofMinutes(5),
) {
    private val logger = LoggerFactory.getLogger(EnrollmentReaper::class.java)
    private val executor = Executors.newSingleThreadScheduledExecutor { runnable ->
        Thread(runnable, "enrollment-reaper").apply { isDaemon = true }
    }

    fun start() {
        executor.scheduleWithFixedDelay(::reapOnce, 0, interval.toMillis(), TimeUnit.MILLISECONDS)
    }

    fun stop() {
        executor.shutdownNow()
    }

    private fun reapOnce() {
        try {
            val cutoff = clock.instant().minus(reapAfter)
            val removed = jdbi.inTransaction<Int, RuntimeException> { handle ->
                enrollmentRepository.deleteExpiredPendingBefore(handle, cutoff)
            }
            if (removed > 0) {
                logger.info("Reaped {} expired pending enrollment(s)", removed)
            }
        } catch (e: Exception) {
            logger.error("Enrollment reaper iteration failed", e)
        }
    }
}
