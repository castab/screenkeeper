package com.screenkeeper.control.storage

import aws.sdk.kotlin.services.s3.S3Client
import aws.sdk.kotlin.services.s3.model.ChecksumAlgorithm
import aws.sdk.kotlin.services.s3.model.ChecksumMode
import aws.sdk.kotlin.services.s3.model.GetObjectRequest
import aws.sdk.kotlin.services.s3.model.HeadObjectRequest
import aws.sdk.kotlin.services.s3.model.NoSuchKey
import aws.sdk.kotlin.services.s3.model.PutObjectRequest
import aws.sdk.kotlin.services.s3.presigners.presignGetObject
import aws.sdk.kotlin.services.s3.presigners.presignPutObject
import aws.smithy.kotlin.runtime.auth.awscredentials.Credentials
import aws.smithy.kotlin.runtime.http.engine.okhttp.OkHttpEngine
import aws.sdk.kotlin.runtime.auth.credentials.StaticCredentialsProvider
import aws.smithy.kotlin.runtime.net.url.Url
import com.screenkeeper.control.config.ObjectStorageConfig
import java.time.Clock
import java.util.Base64
import kotlin.time.Duration.Companion.seconds

class S3ObjectStore(
    private val config: ObjectStorageConfig,
    private val client: S3Client = buildClient(config),
    private val clock: Clock = Clock.systemUTC(),
) : ObjectStore {
    override suspend fun head(key: String): StoredObject? = try {
        val response = client.headObject(
            HeadObjectRequest {
                bucket = config.bucket
                this.key = key
                checksumMode = ChecksumMode.Enabled
            },
        )
        StoredObject(
            byteSize = response.contentLength ?: 0L,
            checksumSha256 = response.checksumSha256,
            contentType = response.contentType,
        )
    } catch (_: NoSuchKey) {
        null
    }

    override suspend fun presignPut(
        key: String,
        contentType: String,
        byteSize: Long,
        sha256: String,
        ttlSeconds: Long,
    ): SignedObjectRequest {
        val request = PutObjectRequest {
            bucket = config.bucket
            this.key = key
            this.contentType = contentType
            contentLength = byteSize
            checksumAlgorithm = ChecksumAlgorithm.Sha256
            checksumSha256 = hexToBase64(sha256)
            // Content-addressed objects are immutable. A concurrent upload of
            // the same hash may reuse the object, but can never overwrite it.
            ifNoneMatch = "*"
        }
        val signed = client.presignPutObject(request, ttlSeconds.seconds)
        return SignedObjectRequest(
            method = "PUT",
            url = signed.url.toString(),
            headers = buildMap {
                signed.headers.forEach { name, values -> put(name, values.joinToString(", ")) }
            },
            expiresAt = clock.instant().plusSeconds(ttlSeconds),
        )
    }

    override suspend fun presignGet(key: String, ttlSeconds: Long): SignedObjectRequest {
        val signed = client.presignGetObject(
            GetObjectRequest {
                bucket = config.bucket
                this.key = key
            },
            ttlSeconds.seconds,
        )
        return SignedObjectRequest(
            method = "GET",
            url = signed.url.toString(),
            headers = buildMap {
                signed.headers.forEach { name, values -> put(name, values.joinToString(", ")) }
            },
            expiresAt = clock.instant().plusSeconds(ttlSeconds),
        )
    }

    override fun close() = client.close()

    companion object {
        private fun buildClient(config: ObjectStorageConfig): S3Client = S3Client {
            region = config.region
            endpointUrl = Url.parse(config.endpoint)
            forcePathStyle = config.pathStyle
            httpClient = OkHttpEngine {
                connectTimeout = 10.seconds
                connectionAcquireTimeout = 10.seconds
                socketReadTimeout = 30.seconds
                socketWriteTimeout = 30.seconds
                maxConcurrency = 64u
                maxConcurrencyPerHost = 32u
            }
            credentialsProvider = StaticCredentialsProvider(
                Credentials(config.accessKeyId, config.secretAccessKey),
            )
        }

        fun hexToBase64(hex: String): String {
            val bytes = ByteArray(hex.length / 2) { index ->
                hex.substring(index * 2, index * 2 + 2).toInt(16).toByte()
            }
            return Base64.getEncoder().encodeToString(bytes)
        }
    }
}
