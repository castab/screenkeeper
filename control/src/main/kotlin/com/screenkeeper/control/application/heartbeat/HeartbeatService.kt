package com.screenkeeper.control.application.heartbeat

import com.screenkeeper.control.persistence.repository.PlayerReportRepository
import com.screenkeeper.control.persistence.repository.PlayerRepository
import org.jdbi.v3.core.Jdbi
import java.time.Clock
import java.time.Instant
import java.util.UUID

class HeartbeatService(
    private val jdbi: Jdbi,
    private val clock: Clock,
    private val playerRepository: PlayerRepository,
    private val playerReportRepository: PlayerReportRepository,
) {
    /**
     * One transaction: touch the player's liveness/version/hostname and
     * replace its latest report. [receivedAt] uses the server's clock, which
     * is authoritative for liveness -- [reportedAt] (the appliance's own
     * clock) is stored for display only.
     */
    fun record(
        playerId: UUID,
        screenkeeperVersion: String,
        hostname: String,
        reportedAt: Instant,
        rawReportJson: String,
    ) {
        jdbi.useTransaction<RuntimeException> { handle ->
            val receivedAt = clock.instant()
            playerRepository.touch(handle, playerId, screenkeeperVersion, hostname, receivedAt)
            playerReportRepository.upsert(handle, playerId, reportedAt, receivedAt, rawReportJson)
        }
    }
}
