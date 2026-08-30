package com.screenkeeper.control.storage

import java.time.Instant

data class StoredObject(
    val byteSize: Long,
    val checksumSha256: String?,
    val contentType: String?,
)

data class SignedObjectRequest(
    val method: String,
    val url: String,
    val headers: Map<String, String>,
    val expiresAt: Instant,
)

interface ObjectStore : AutoCloseable {
    suspend fun head(key: String): StoredObject?
    suspend fun presignPut(
        key: String,
        contentType: String,
        byteSize: Long,
        sha256: String,
        ttlSeconds: Long,
    ): SignedObjectRequest
    suspend fun presignGet(key: String, ttlSeconds: Long): SignedObjectRequest
    override fun close() {}
}
