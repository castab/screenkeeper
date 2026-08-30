package com.screenkeeper.control.config

class ConfigError(message: String) : RuntimeException(message)

data class AppConfig(
    val host: String,
    val port: Int,
    val databaseUrl: String,
    val databaseUser: String,
    val databasePassword: String,
    val adminToken: String,
    val heartbeatIntervalSeconds: Long,
    val onlineMultiplier: Double,
    val enrollmentTtlMinutes: Long,
    val enrollmentReapAfterMinutes: Long,
    val metricsEnabled: Boolean,
    val metricsHost: String,
    val metricsPort: Int,
    val objectStorage: ObjectStorageConfig?,
) {
    companion object {
        // Not an env var: this is a protocol constant advertised to every
        // polling appliance, not deployment configuration.
        const val ENROLLMENT_POLL_INTERVAL_SECONDS: Int = 5

        fun fromEnv(env: Map<String, String> = System.getenv()): AppConfig {
            fun required(name: String): String =
                env[name]?.takeIf { it.isNotBlank() }
                    ?: throw ConfigError("$name is required and has no default; set it before starting screenkeeper-control")

            val s3Names = listOf(
                "S3_ENDPOINT", "S3_BUCKET", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_REGION",
            )
            val configuredS3Names = s3Names.filter { !env[it].isNullOrBlank() }
            val objectStorage = if (configuredS3Names.isEmpty()) {
                null
            } else {
                val missing = s3Names - configuredS3Names.toSet()
                if (missing.isNotEmpty()) {
                    throw ConfigError("Incomplete S3 configuration; missing ${missing.joinToString(", ")}")
                }
                ObjectStorageConfig(
                    endpoint = env.getValue("S3_ENDPOINT").trim(),
                    bucket = env.getValue("S3_BUCKET").trim(),
                    accessKeyId = env.getValue("S3_ACCESS_KEY_ID").trim(),
                    secretAccessKey = env.getValue("S3_SECRET_ACCESS_KEY").trim(),
                    region = env.getValue("S3_REGION").trim(),
                    pathStyle = env["S3_PATH_STYLE"]?.toBooleanStrictOrNull() ?: false,
                    uploadUrlTtlSeconds = env["S3_UPLOAD_URL_TTL_SECONDS"]?.toLongOrNull()?.takeIf { it > 0 } ?: 3600L,
                    downloadUrlTtlSeconds = env["S3_DOWNLOAD_URL_TTL_SECONDS"]?.toLongOrNull()?.takeIf { it > 0 } ?: 3600L,
                )
            }

            return AppConfig(
                host = env["SCREENKEEPER_CONTROL_HOST"]?.takeIf { it.isNotBlank() } ?: "0.0.0.0",
                port = env["SCREENKEEPER_CONTROL_PORT"]?.toIntOrNull() ?: 8080,
                databaseUrl = required("SCREENKEEPER_DATABASE_URL"),
                databaseUser = required("SCREENKEEPER_DATABASE_USER"),
                databasePassword = required("SCREENKEEPER_DATABASE_PASSWORD"),
                adminToken = required("SCREENKEEPER_ADMIN_TOKEN"),
                heartbeatIntervalSeconds = env["SCREENKEEPER_HEARTBEAT_INTERVAL_SECONDS"]?.toLongOrNull() ?: 30L,
                onlineMultiplier = env["SCREENKEEPER_ONLINE_MULTIPLIER"]?.toDoubleOrNull() ?: 3.0,
                enrollmentTtlMinutes = env["SCREENKEEPER_ENROLLMENT_TTL_MINUTES"]?.toLongOrNull() ?: 15L,
                enrollmentReapAfterMinutes = env["SCREENKEEPER_ENROLLMENT_REAP_AFTER_MINUTES"]?.toLongOrNull() ?: 60L,
                // Local-only Prometheus exposition for a host agent to scrape. Unlike
                // `port` above (reachable over WAN from edge heartbeats), this never
                // shares a listener with the public API -- see Main.kt.
                metricsEnabled = env["SCREENKEEPER_CONTROL_METRICS_ENABLED"]?.toBooleanStrictOrNull() ?: true,
                metricsHost = env["SCREENKEEPER_CONTROL_METRICS_HOST"]?.takeIf { it.isNotBlank() } ?: "127.0.0.1",
                metricsPort = env["SCREENKEEPER_CONTROL_METRICS_PORT"]?.toIntOrNull() ?: 9464,
                objectStorage = objectStorage,
            )
        }
    }

    override fun toString(): String =
        "AppConfig(host=$host, port=$port, databaseUrl=$databaseUrl, databaseUser=$databaseUser, " +
            "heartbeatIntervalSeconds=$heartbeatIntervalSeconds, onlineMultiplier=$onlineMultiplier, " +
            "enrollmentTtlMinutes=$enrollmentTtlMinutes, enrollmentReapAfterMinutes=$enrollmentReapAfterMinutes, " +
            "metricsEnabled=$metricsEnabled, metricsHost=$metricsHost, metricsPort=$metricsPort, " +
            "objectStorage=${objectStorage?.redacted()})"
}

data class ObjectStorageConfig(
    val endpoint: String,
    val bucket: String,
    val accessKeyId: String,
    val secretAccessKey: String,
    val region: String,
    val pathStyle: Boolean,
    val uploadUrlTtlSeconds: Long,
    val downloadUrlTtlSeconds: Long,
) {
    fun redacted(): String =
        "ObjectStorageConfig(endpoint=$endpoint, bucket=$bucket, region=$region, pathStyle=$pathStyle, " +
            "uploadUrlTtlSeconds=$uploadUrlTtlSeconds, downloadUrlTtlSeconds=$downloadUrlTtlSeconds)"

    override fun toString(): String = redacted()
}
