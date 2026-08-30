package com.screenkeeper.control.application.content

import com.screenkeeper.control.domain.ContentAssignment
import com.screenkeeper.control.domain.DomainError
import com.screenkeeper.control.persistence.repository.ContentRepository
import com.screenkeeper.control.persistence.repository.MediaAssetRepository
import com.screenkeeper.control.persistence.repository.PlayerReportRepository
import com.screenkeeper.control.persistence.repository.PlayerRepository
import com.screenkeeper.control.storage.ObjectStore
import com.screenkeeper.control.storage.SignedObjectRequest
import kotlinx.coroutines.runBlocking
import org.jdbi.v3.core.Jdbi
import java.time.Clock
import java.time.Instant
import java.util.UUID

data class DesiredContent(
    val revision: Long,
    val assignments: List<Pair<ContentAssignment, SignedObjectRequest>>,
)

class ContentService(
    private val jdbi: Jdbi,
    private val clock: Clock,
    private val playerRepository: PlayerRepository,
    private val playerReportRepository: PlayerReportRepository,
    private val assetRepository: MediaAssetRepository,
    private val contentRepository: ContentRepository,
    private val objectStore: ObjectStore?,
    private val downloadTtlSeconds: Long,
) {
    fun assign(playerId: UUID, playbackPlayerId: String, assetRevisionId: UUID): Long =
        jdbi.inTransaction<Long, RuntimeException> { handle ->
            if (playerRepository.findById(handle, playerId) == null) throw DomainError.PlayerNotFound()
            if (!playerReportRepository.hasPlaybackPlayer(handle, playerId, playbackPlayerId)) {
                throw DomainError.PlaybackPlayerNotFound()
            }
            val assetRevision = assetRepository.findRevision(handle, assetRevisionId)
                ?: throw DomainError.AssetRevisionNotFound()
            if (assetRevision.status != "available") throw DomainError.AssetRevisionUnavailable()
            if (contentRepository.assignedRevisionId(handle, playerId, playbackPlayerId) == assetRevisionId) {
                return@inTransaction contentRepository.manifestRevision(handle, playerId)
            }
            val now = clock.instant()
            contentRepository.assign(handle, playerId, playbackPlayerId, assetRevisionId, now)
            contentRepository.bumpManifest(handle, playerId, now)
        }

    fun clear(playerId: UUID, playbackPlayerId: String): Long =
        jdbi.inTransaction<Long, RuntimeException> { handle ->
            if (playerRepository.findById(handle, playerId) == null) throw DomainError.PlayerNotFound()
            if (!contentRepository.clear(handle, playerId, playbackPlayerId)) {
                return@inTransaction contentRepository.manifestRevision(handle, playerId)
            }
            contentRepository.bumpManifest(handle, playerId, clock.instant())
        }

    fun desired(playerId: UUID): DesiredContent {
        val store = objectStore ?: throw DomainError.ObjectStorageUnavailable()
        val snapshot = jdbi.withHandle<Pair<Long, List<ContentAssignment>>, RuntimeException> { handle ->
            contentRepository.manifestRevision(handle, playerId) to contentRepository.assignments(handle, playerId)
        }
        val signed = snapshot.second.map { assignment ->
            assignment to runBlocking {
                store.presignGet(assignment.assetRevision.storageKey, downloadTtlSeconds)
            }
        }
        return DesiredContent(snapshot.first, signed)
    }

    fun recordStatus(playerId: UUID, reportedAt: Instant, rawJson: String) {
        jdbi.useHandle<RuntimeException> { handle ->
            contentRepository.upsertReport(handle, playerId, reportedAt, clock.instant(), rawJson)
        }
    }
}
