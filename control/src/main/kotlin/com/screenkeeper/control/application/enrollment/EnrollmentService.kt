package com.screenkeeper.control.application.enrollment

import com.screenkeeper.control.config.AppConfig
import com.screenkeeper.control.domain.DomainError
import com.screenkeeper.control.domain.Enrollment
import com.screenkeeper.control.domain.Location
import com.screenkeeper.control.domain.Organization
import com.screenkeeper.control.domain.PairingCode
import com.screenkeeper.control.domain.Player
import com.screenkeeper.control.persistence.repository.EnrollmentRepository
import com.screenkeeper.control.persistence.repository.LocationRepository
import com.screenkeeper.control.persistence.repository.OrganizationRepository
import com.screenkeeper.control.persistence.repository.PlayerCredentialRepository
import com.screenkeeper.control.persistence.repository.PlayerRepository
import com.screenkeeper.control.security.sha256
import org.jdbi.v3.core.Handle
import org.jdbi.v3.core.Jdbi
import org.jdbi.v3.core.statement.UnableToExecuteStatementException
import java.sql.SQLException
import java.time.Clock
import java.time.Duration
import java.time.Instant
import java.util.UUID

data class EnrollmentTicket(
    val enrollmentId: UUID,
    val code: String,
    val expiresAt: Instant,
    val pollIntervalSeconds: Int,
)

data class EnrollmentClaimedInfo(
    val playerId: UUID,
    val playerName: String,
    val organization: Organization,
    val location: Location,
)

sealed class EnrollmentPollResult {
    data object Pending : EnrollmentPollResult()
    data class Claimed(val info: EnrollmentClaimedInfo) : EnrollmentPollResult()
}

data class ClaimResult(
    val player: Player,
    val location: Location,
    val organization: Organization,
)

private const val UNIQUE_VIOLATION_SQLSTATE = "23505"

class EnrollmentService(
    private val jdbi: Jdbi,
    private val config: AppConfig,
    private val clock: Clock,
    private val enrollmentRepository: EnrollmentRepository,
    private val playerRepository: PlayerRepository,
    private val playerCredentialRepository: PlayerCredentialRepository,
    private val locationRepository: LocationRepository,
    private val organizationRepository: OrganizationRepository,
) {
    /**
     * Creates a pending enrollment, or supersedes the existing pending one for
     * this installation_id in place. Already-claimed installations always
     * hit AlreadyEnrolled, regardless of whether the device token matches --
     * the appliance is expected to stop and report this, never mint a new
     * identity. The upsert itself is race-safe via the partial unique index
     * (see EnrollmentRepository); no elevated isolation level is needed here
     * since Postgres's ON CONFLICT already serializes concurrent writers.
     */
    fun createOrReuse(
        installationId: UUID,
        deviceToken: String,
        screenkeeperVersion: String,
        hostname: String,
    ): EnrollmentTicket =
        jdbi.inTransaction<EnrollmentTicket, RuntimeException> { handle ->
            if (playerRepository.existsByInstallationId(handle, installationId)) {
                throw DomainError.AlreadyEnrolled()
            }
            val deviceTokenHash = sha256(deviceToken)
            val code = PairingCode.generate()
            val codeHash = sha256(code)
            val expiresAt = clock.instant().plus(Duration.ofMinutes(config.enrollmentTtlMinutes))

            val row = enrollmentRepository.upsertPending(
                handle, installationId, deviceTokenHash, codeHash, expiresAt, screenkeeperVersion, hostname,
            )
            EnrollmentTicket(row.id, code, row.expiresAt, AppConfig.ENROLLMENT_POLL_INTERVAL_SECONDS)
        }

    /** Resolves the enrollment for the bearer-auth filter; independent of any write flow. */
    fun findById(handle: Handle, enrollmentId: UUID): Enrollment? = enrollmentRepository.findById(handle, enrollmentId)

    fun findByCode(code: String): Enrollment? =
        jdbi.withHandle<Enrollment?, RuntimeException> { handle -> enrollmentRepository.findByCodeHash(handle, sha256(code)) }

    fun pollState(enrollment: Enrollment): EnrollmentPollResult {
        val playerId = enrollment.playerId
        if (!enrollment.isClaimed || playerId == null) return EnrollmentPollResult.Pending
        return jdbi.withHandle<EnrollmentPollResult, RuntimeException> { handle ->
            val player = playerRepository.findById(handle, playerId) ?: return@withHandle EnrollmentPollResult.Pending
            val location = locationRepository.findById(handle, player.locationId) ?: return@withHandle EnrollmentPollResult.Pending
            val organization = organizationRepository.findById(handle, location.organizationId)
                ?: return@withHandle EnrollmentPollResult.Pending
            EnrollmentPollResult.Claimed(EnrollmentClaimedInfo(player.id, player.name, organization, location))
        }
    }

    /**
     * Claims a pending, unexpired enrollment and assigns it to a location.
     * One transaction: create the player, activate its credential from the
     * enrollment's device_token_hash, and mark the enrollment claimed. The
     * players.installation_id unique constraint is the backstop against two
     * concurrent claims of the same code racing past the claimed_at check.
     */
    fun claim(code: String, locationId: UUID, name: String): ClaimResult =
        jdbi.inTransaction<ClaimResult, RuntimeException> { handle ->
            val enrollment = enrollmentRepository.findByCodeHash(handle, sha256(code)) ?: throw DomainError.EnrollmentNotFound()
            if (enrollment.isClaimed) throw DomainError.EnrollmentAlreadyClaimed()
            if (enrollment.isExpiredAt(clock.instant())) throw DomainError.EnrollmentExpired()
            val location = locationRepository.findById(handle, locationId) ?: throw DomainError.LocationNotFound()
            val organization = organizationRepository.findById(handle, location.organizationId)
                ?: throw DomainError.OrganizationNotFound()

            val player = try {
                playerRepository.insert(
                    handle, enrollment.installationId, locationId, name,
                    enrollment.screenkeeperVersion, enrollment.hostname,
                )
            } catch (e: UnableToExecuteStatementException) {
                if (e.isUniqueViolation()) throw DomainError.EnrollmentAlreadyClaimed() else throw e
            }
            playerCredentialRepository.insert(handle, player.id, enrollment.deviceTokenHash)
            enrollmentRepository.markClaimed(handle, enrollment.id, player.id, clock.instant())

            ClaimResult(player, location, organization)
        }

    private fun UnableToExecuteStatementException.isUniqueViolation(): Boolean {
        var cause: Throwable? = this
        while (cause != null) {
            if (cause is SQLException && cause.sqlState == UNIQUE_VIOLATION_SQLSTATE) return true
            cause = cause.cause
        }
        return false
    }
}
