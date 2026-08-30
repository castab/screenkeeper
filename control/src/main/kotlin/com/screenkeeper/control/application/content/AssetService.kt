package com.screenkeeper.control.application.content

import com.screenkeeper.control.domain.DomainError
import com.screenkeeper.control.domain.MediaAsset
import com.screenkeeper.control.domain.MediaAssetRevision
import com.screenkeeper.control.persistence.repository.MediaAssetRepository
import com.screenkeeper.control.storage.ObjectStore
import com.screenkeeper.control.storage.SignedObjectRequest
import kotlinx.coroutines.runBlocking
import org.jdbi.v3.core.Jdbi
import java.time.Clock
import java.util.Base64
import java.util.UUID

data class AssetDetail(val asset: MediaAsset, val revisions: List<MediaAssetRevision>)
data class PendingRevision(val revision: MediaAssetRevision, val upload: SignedObjectRequest?)

class AssetService(
    private val jdbi: Jdbi,
    private val clock: Clock,
    private val repository: MediaAssetRepository,
    private val objectStore: ObjectStore?,
    private val uploadTtlSeconds: Long,
) {
    fun create(name: String): MediaAsset {
        val clean = name.trim()
        if (clean.isEmpty() || clean.length > 200) throw DomainError.MalformedRequest("Asset name is invalid.")
        return jdbi.withHandle<MediaAsset, RuntimeException> { repository.insert(it, clean) }
    }

    fun list(): List<AssetDetail> = jdbi.withHandle<List<AssetDetail>, RuntimeException> { handle ->
        repository.list(handle).map { AssetDetail(it, repository.listRevisions(handle, it.id)) }
    }

    fun get(assetId: UUID): AssetDetail? = jdbi.withHandle<AssetDetail?, RuntimeException> { handle ->
        repository.findById(handle, assetId)?.let { AssetDetail(it, repository.listRevisions(handle, assetId)) }
    }

    fun createRevision(
        assetId: UUID,
        originalFilename: String,
        contentType: String,
        byteSize: Long,
        sha256: String,
    ): PendingRevision {
        val store = objectStore ?: throw DomainError.ObjectStorageUnavailable()
        validateRevision(originalFilename, contentType, byteSize, sha256)
        val storageKey = "assets/${sha256.take(2)}/$sha256"
        val created = jdbi.inTransaction<MediaAssetRevision, RuntimeException> { handle ->
            if (!repository.lock(handle, assetId)) throw DomainError.AssetNotFound()
            val revision = repository.insertRevision(
                handle, assetId, originalFilename.trim(), contentType.trim(), byteSize, sha256, storageKey,
            )
            val existing = repository.findAvailableByStorageKey(handle, storageKey)
            if (existing != null && existing.id != revision.id && existing.byteSize == byteSize) {
                repository.markAvailable(handle, revision.id, clock.instant())
            } else revision
        }
        if (created.status == "available") return PendingRevision(created, null)
        val upload = runBlocking {
            store.presignPut(storageKey, contentType, byteSize, sha256, uploadTtlSeconds)
        }
        return PendingRevision(created, upload)
    }

    fun complete(assetId: UUID, revisionId: UUID): MediaAssetRevision {
        val store = objectStore ?: throw DomainError.ObjectStorageUnavailable()
        val revision = jdbi.withHandle<MediaAssetRevision?, RuntimeException> {
            repository.findRevision(it, revisionId)
        } ?: throw DomainError.AssetRevisionNotFound()
        if (revision.assetId != assetId) throw DomainError.AssetRevisionNotFound()
        if (revision.status == "available") return revision
        val stored = runBlocking { store.head(revision.storageKey) }
            ?: throw DomainError.ObjectIntegrityMismatch()
        if (stored.byteSize != revision.byteSize || stored.checksumSha256 != hexToBase64(revision.sha256)) {
            throw DomainError.ObjectIntegrityMismatch()
        }
        return jdbi.withHandle<MediaAssetRevision, RuntimeException> {
            repository.markAvailable(it, revision.id, clock.instant())
        }
    }

    private fun validateRevision(filename: String, contentType: String, byteSize: Long, sha256: String) {
        if (filename.trim().isEmpty() || filename.length > 500) {
            throw DomainError.MalformedRequest("Original filename is invalid.")
        }
        if (!contentType.startsWith("video/") || contentType.length > 200) {
            throw DomainError.MalformedRequest("Content type must be video/*.")
        }
        if (byteSize <= 0) throw DomainError.MalformedRequest("Byte size must be positive.")
        if (!sha256.matches(Regex("^[0-9a-f]{64}$"))) {
            throw DomainError.MalformedRequest("SHA-256 must be 64 lowercase hexadecimal characters.")
        }
    }

    private fun hexToBase64(hex: String): String = Base64.getEncoder().encodeToString(
        ByteArray(hex.length / 2) { index -> hex.substring(index * 2, index * 2 + 2).toInt(16).toByte() },
    )
}
