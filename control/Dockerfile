# syntax=docker/dockerfile:1

# Screenkeeper Control -- server-side only. The Screenkeeper Edge/mpv player
# is never containerized; this Dockerfile packages the control plane alone.

FROM eclipse-temurin:25-jdk AS build
WORKDIR /workspace

# Cache the Gradle wrapper/dependency resolution layer separately from source.
COPY gradlew gradlew.bat ./
COPY gradle ./gradle
COPY settings.gradle.kts build.gradle.kts gradle.properties ./
RUN ./gradlew --version

COPY src ./src
RUN ./gradlew installDist --no-daemon --console=plain

FROM eclipse-temurin:25-jre-alpine AS runtime

RUN addgroup -S screenkeeper && adduser -S screenkeeper -G screenkeeper
WORKDIR /opt/screenkeeper-control

COPY --from=build /workspace/build/install/screenkeeper-control/ ./

# The OTel Java agent, baked in but inert: JAVA_TOOL_OPTIONS is unset below,
# so nothing loads it unless an operator sets that env var at deploy time --
# the "no application code change" auto-instrumentation path documented in
# docs/architecture/observability.md. Verify the current release and its
# published checksum at https://repo1.maven.org/maven2/io/opentelemetry/javaagent/opentelemetry-javaagent/
# before bumping this pin.
ADD --chmod=0644 --checksum=sha256:1b0246d3e60b608b07836a9656e1a97bb7d084b088111ef34ecd47483acebcf5 \
    https://repo1.maven.org/maven2/io/opentelemetry/javaagent/opentelemetry-javaagent/2.16.0/opentelemetry-javaagent-2.16.0.jar \
    /opt/opentelemetry-javaagent.jar

# No secrets are baked into the image. SCREENKEEPER_ADMIN_TOKEN,
# SCREENKEEPER_DATABASE_PASSWORD, etc. are supplied at runtime only.
USER screenkeeper
EXPOSE 8080

ENTRYPOINT ["/opt/screenkeeper-control/bin/screenkeeper-control"]
