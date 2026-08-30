plugins {
    alias(libs.plugins.kotlin.jvm)
    alias(libs.plugins.kotlin.serialization)
    application
}

group = "com.screenkeeper"
version = "0.1.0"

repositories {
    mavenCentral()
}

// Java 25 is the toolchain/runtime JDK (matches the project's chosen version),
// but Kotlin 2.2.20's compiler caps its bytecode target at 24 ("Kotlin does
// not yet support 25 JDK target") and falls back automatically -- so Java's
// own compileJava target is pinned to 24 too, to keep the two consistent.
// The resulting class files still run fine on a JRE 25.
kotlin {
    jvmToolchain(25)
    compilerOptions {
        jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_24)
    }
}

java {
    sourceCompatibility = JavaVersion.VERSION_24
    targetCompatibility = JavaVersion.VERSION_24
}

application {
    mainClass.set("com.screenkeeper.control.MainKt")
}

sourceSets {
    create("testIntegration") {
        kotlin.srcDir("src/testIntegration/kotlin")
        resources.srcDir("src/testIntegration/resources")
        compileClasspath += sourceSets.main.get().output + sourceSets.test.get().output
        runtimeClasspath += sourceSets.main.get().output + sourceSets.test.get().output
    }
}

configurations.named("testIntegrationImplementation") {
    extendsFrom(configurations.getByName("testImplementation"))
}
configurations.named("testIntegrationRuntimeOnly") {
    extendsFrom(configurations.getByName("testRuntimeOnly"))
}

dependencies {
    // Required at runtime by Jdbi's KotlinPlugin (KotlinMapper reflects on
    // data class constructors) -- kotlin-stdlib alone is not enough.
    implementation(libs.kotlin.reflect)

    implementation(platform(libs.http4k.bom))
    implementation(libs.http4k.core)
    implementation(libs.http4k.server.jetty)
    implementation(libs.http4k.format.kotlinx.serialization)

    implementation(libs.kotlinx.serialization.json)
    implementation(libs.kotlinx.datetime)
    implementation(libs.kotlinx.coroutines.core)

    implementation(libs.jdbi.core)
    implementation(libs.jdbi.postgres)
    implementation(libs.jdbi.kotlin)

    implementation(libs.flyway.core)
    implementation(libs.flyway.postgresql)

    implementation(libs.postgresql.driver)
    implementation(libs.hikaricp)

    implementation(libs.logback.classic)

    implementation(libs.micrometer.core)
    implementation(libs.micrometer.registry.prometheus)

    implementation(libs.opentelemetry.api)
    implementation(libs.aws.sdk.s3)
    implementation(libs.aws.smithy.http.okhttp)

    testImplementation(platform(libs.kotest.bom))
    testImplementation(libs.kotest.runner.junit5)
    testImplementation(libs.kotest.assertions.core)
    testImplementation(libs.opentelemetry.sdk.testing)
}

tasks.test {
    useJUnitPlatform()
}

val integrationTest = tasks.register<Test>("integrationTest") {
    description = "Runs Postgres-backed integration tests against an externally provided database."
    group = "verification"
    testClassesDirs = sourceSets["testIntegration"].output.classesDirs
    classpath = sourceSets["testIntegration"].runtimeClasspath
    useJUnitPlatform()
    shouldRunAfter(tasks.test)
    doFirst {
        val required = listOf(
            "SCREENKEEPER_TEST_DATABASE_URL",
            "SCREENKEEPER_TEST_DATABASE_USER",
            "SCREENKEEPER_TEST_DATABASE_PASSWORD",
        )
        val missing = required.filter { System.getenv(it).isNullOrBlank() }
        if (missing.isNotEmpty()) {
            throw GradleException(
                "integrationTest requires ${missing.joinToString(", ")} to be set, pointed at a running " +
                    "Postgres instance (see control/compose.dev.yaml or control/README.md)."
            )
        }
    }
}

// integrationTest is intentionally NOT wired into `check` — it requires a live
// Postgres instance and is invoked explicitly (see control/README.md and CI).
