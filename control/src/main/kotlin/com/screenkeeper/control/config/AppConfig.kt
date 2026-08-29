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
) {
    companion object {
        // Not an env var: this is a protocol constant advertised to every
        // polling appliance, not deployment configuration.
        const val ENROLLMENT_POLL_INTERVAL_SECONDS: Int = 5

        fun fromEnv(env: Map<String, String> = System.getenv()): AppConfig {
            fun required(name: String): String =
                env[name]?.takeIf { it.isNotBlank() }
                    ?: throw ConfigError("$name is required and has no default; set it before starting screenkeeper-control")

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
            )
        }
    }

    override fun toString(): String =
        "AppConfig(host=$host, port=$port, databaseUrl=$databaseUrl, databaseUser=$databaseUser, " +
            "heartbeatIntervalSeconds=$heartbeatIntervalSeconds, onlineMultiplier=$onlineMultiplier, " +
            "enrollmentTtlMinutes=$enrollmentTtlMinutes, enrollmentReapAfterMinutes=$enrollmentReapAfterMinutes)"
}
