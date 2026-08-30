package com.screenkeeper.control.domain

/**
 * Errors application services raise. http/filters/ErrorHandlingFilter.kt maps
 * every one of these to an HTTP status and the shared {error, message} body --
 * this hierarchy never leaks a stack trace or SQL detail on its own.
 */
sealed class DomainError(val code: String, message: String) : RuntimeException(message) {
    class AlreadyEnrolled :
        DomainError("already_enrolled", "This installation is already enrolled.")

    class EnrollmentNotFound :
        DomainError("enrollment_not_found", "Unknown enrollment.")

    class EnrollmentExpired :
        DomainError("enrollment_expired", "This enrollment has expired.")

    class EnrollmentAlreadyClaimed :
        DomainError("enrollment_already_claimed", "This enrollment has already been claimed.")

    class OrganizationNotFound :
        DomainError("organization_not_found", "Unknown organization.")

    class LocationNotFound :
        DomainError("location_not_found", "Unknown location.")

    class PlayerNotFound :
        DomainError("player_not_found", "Unknown player.")

    class AssetNotFound :
        DomainError("asset_not_found", "Unknown media asset.")

    class AssetRevisionNotFound :
        DomainError("asset_revision_not_found", "Unknown media asset revision.")

    class AssetRevisionUnavailable :
        DomainError("asset_revision_unavailable", "The media asset revision is not available.")

    class PlaybackPlayerNotFound :
        DomainError("playback_player_not_found", "The device has not reported that playback player.")

    class ObjectStorageUnavailable :
        DomainError("object_storage_unavailable", "S3 object storage is not configured or unavailable.")

    class ObjectIntegrityMismatch :
        DomainError("object_integrity_mismatch", "The stored object does not match its declared size and SHA-256.")

    class InvalidCredential :
        DomainError("invalid_credential", "Missing or invalid credential.")

    class RevokedCredential :
        DomainError("revoked_credential", "This credential has been revoked.")

    class MalformedRequest(detail: String = "The request is invalid.") :
        DomainError("malformed_request", detail)

    class PayloadTooLarge :
        DomainError("payload_too_large", "The request body exceeds the configured size limit.")
}
