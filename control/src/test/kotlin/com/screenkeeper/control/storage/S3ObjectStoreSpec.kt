package com.screenkeeper.control.storage

import com.screenkeeper.control.config.ObjectStorageConfig
import io.kotest.core.spec.style.FunSpec
import io.kotest.matchers.shouldBe
import io.kotest.matchers.string.shouldContain

private const val SHA256 = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"

private fun storageConfig(pathStyle: Boolean) = ObjectStorageConfig(
    endpoint = "https://objects.example.com",
    bucket = "screenkeeper-media",
    accessKeyId = "test-access-key",
    secretAccessKey = "test-secret-key",
    region = "us-east-1",
    pathStyle = pathStyle,
    uploadUrlTtlSeconds = 3600,
    downloadUrlTtlSeconds = 3600,
)

class S3ObjectStoreSpec : FunSpec({
    test("converts the manifest hex SHA-256 into the S3 checksum header encoding") {
        S3ObjectStore.hexToBase64(SHA256) shouldBe "n4bQgYhMfWWaL+qgxVrQFaO/TxsrC4Is0V1sFbDwCgg="
    }

    test("presigns a checksum-bound PUT for a generic virtual-hosted endpoint") {
        S3ObjectStore(storageConfig(pathStyle = false)).use { store ->
            val signed = store.presignPut("assets/9f/$SHA256", "video/mp4", 123, SHA256, 3600)
            signed.method shouldBe "PUT"
            signed.url shouldContain "screenkeeper-media.objects.example.com"
            val headers = signed.headers.mapKeys { it.key.lowercase() }
            headers["content-type"] shouldBe "video/mp4"
            headers["content-length"] shouldBe "123"
            headers["x-amz-checksum-sha256"] shouldBe "n4bQgYhMfWWaL+qgxVrQFaO/TxsrC4Is0V1sFbDwCgg="
            headers["if-none-match"] shouldBe "*"
        }
    }

    test("presigns a GET using path-style addressing when configured") {
        S3ObjectStore(storageConfig(pathStyle = true)).use { store ->
            val signed = store.presignGet("assets/9f/$SHA256", 3600)
            signed.method shouldBe "GET"
            signed.url shouldContain "objects.example.com/screenkeeper-media/assets/9f/$SHA256"
        }
    }
})
