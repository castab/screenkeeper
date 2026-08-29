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

# No secrets are baked into the image. SCREENKEEPER_ADMIN_TOKEN,
# SCREENKEEPER_DATABASE_PASSWORD, etc. are supplied at runtime only.
USER screenkeeper
EXPOSE 8080

ENTRYPOINT ["/opt/screenkeeper-control/bin/screenkeeper-control"]
