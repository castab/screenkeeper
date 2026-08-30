package com.screenkeeper.control.persistence.repository

import com.screenkeeper.control.domain.MediaAsset
import com.screenkeeper.control.domain.MediaAssetRevision
import org.jdbi.v3.core.Handle
import org.jdbi.v3.core.kotlin.mapTo
import java.time.Instant
import java.util.UUID

private const val ASSET_COLUMNS = "id, name, created_at AS createdAt"
private const val REVISION_COLUMNS = """
    id, asset_id AS assetId, revision, original_filename AS originalFilename,
    content_type AS contentType, byte_size AS byteSize, sha256, storage_key AS storageKey,
    status, created_at AS createdAt, available_at AS availableAt
"""

class MediaAssetRepository {
    fun insert(handle: Handle, name: String): MediaAsset =
        handle.createUpdate("INSERT INTO media_assets (name) VALUES (:name)")
            .bind("name", name)
            .executeAndReturnGeneratedKeys("id", "name", "created_at")
            .mapTo<MediaAsset>()
            .one()

    fun list(handle: Handle): List<MediaAsset> =
        handle.createQuery("SELECT $ASSET_COLUMNS FROM media_assets ORDER BY created_at")
            .mapTo<MediaAsset>()
            .list()

    fun findById(handle: Handle, id: UUID): MediaAsset? =
        handle.createQuery("SELECT $ASSET_COLUMNS FROM media_assets WHERE id = :id")
            .bind("id", id)
            .mapTo<MediaAsset>()
            .findFirst()
            .orElse(null)

    fun lock(handle: Handle, id: UUID): Boolean =
        handle.createQuery("SELECT id FROM media_assets WHERE id = :id FOR UPDATE")
            .bind("id", id)
            .mapTo<UUID>()
            .findFirst()
            .isPresent

    fun insertRevision(
        handle: Handle,
        assetId: UUID,
        originalFilename: String,
        contentType: String,
        byteSize: Long,
        sha256: String,
        storageKey: String,
    ): MediaAssetRevision =
        handle.createUpdate(
            """
            INSERT INTO media_asset_revisions
                (asset_id, revision, original_filename, content_type, byte_size, sha256, storage_key)
            SELECT :assetId, COALESCE(MAX(revision), 0) + 1, :originalFilename,
                   :contentType, :byteSize, :sha256, :storageKey
            FROM media_asset_revisions WHERE asset_id = :assetId
            """.trimIndent(),
        )
            .bind("assetId", assetId)
            .bind("originalFilename", originalFilename)
            .bind("contentType", contentType)
            .bind("byteSize", byteSize)
            .bind("sha256", sha256)
            .bind("storageKey", storageKey)
            .executeAndReturnGeneratedKeys(
                "id", "asset_id", "revision", "original_filename", "content_type", "byte_size",
                "sha256", "storage_key", "status", "created_at", "available_at",
            )
            .mapTo<MediaAssetRevision>()
            .one()

    fun listRevisions(handle: Handle, assetId: UUID): List<MediaAssetRevision> =
        handle.createQuery(
            "SELECT $REVISION_COLUMNS FROM media_asset_revisions WHERE asset_id = :assetId ORDER BY revision",
        )
            .bind("assetId", assetId)
            .mapTo<MediaAssetRevision>()
            .list()

    fun findRevision(handle: Handle, id: UUID): MediaAssetRevision? =
        handle.createQuery("SELECT $REVISION_COLUMNS FROM media_asset_revisions WHERE id = :id")
            .bind("id", id)
            .mapTo<MediaAssetRevision>()
            .findFirst()
            .orElse(null)

    fun findAvailableByStorageKey(handle: Handle, storageKey: String): MediaAssetRevision? =
        handle.createQuery(
            "SELECT $REVISION_COLUMNS FROM media_asset_revisions " +
                "WHERE storage_key = :storageKey AND status = 'available' LIMIT 1",
        )
            .bind("storageKey", storageKey)
            .mapTo<MediaAssetRevision>()
            .findFirst()
            .orElse(null)

    fun markAvailable(handle: Handle, id: UUID, availableAt: Instant): MediaAssetRevision =
        handle.createUpdate(
            "UPDATE media_asset_revisions SET status = 'available', available_at = :availableAt WHERE id = :id",
        )
            .bind("id", id)
            .bind("availableAt", availableAt)
            .executeAndReturnGeneratedKeys(
                "id", "asset_id", "revision", "original_filename", "content_type", "byte_size",
                "sha256", "storage_key", "status", "created_at", "available_at",
            )
            .mapTo<MediaAssetRevision>()
            .one()
}
