package com.screenkeeper.control.integration

import com.screenkeeper.control.domain.DomainError
import com.screenkeeper.control.support.TestApp
import com.screenkeeper.control.support.TestDatabase
import io.kotest.assertions.throwables.shouldThrow
import io.kotest.core.spec.style.FunSpec
import io.kotest.matchers.shouldBe
import io.kotest.matchers.shouldNotBe
import java.util.UUID

class ClaimTransactionSpec : FunSpec({
    beforeTest { TestDatabase.truncateAll() }

    test("a valid claim creates a player, assigns the location, and activates the credential") {
        val app = TestApp()
        val org = app.organizationService.create("Example Restaurant")
        val location = app.locationService.create(org.id, "Downtown")

        val installationId = UUID.randomUUID()
        val deviceToken = "a".repeat(43)
        val ticket = app.enrollmentService.createOrReuse(installationId, deviceToken, "0.1.0", "menu-player-01")

        val result = app.enrollmentService.claim(ticket.code, location.id, "Main Menu Wall")

        result.player.installationId shouldBe installationId
        result.player.locationId shouldBe location.id
        result.location.id shouldBe location.id
        result.organization.id shouldBe org.id

        val credential = app.jdbi.withHandle<com.screenkeeper.control.domain.PlayerCredential?, RuntimeException> { handle ->
            app.playerCredentialRepository.findByPlayerId(handle, result.player.id)
        }
        credential shouldNotBe null
        credential!!.isRevoked shouldBe false
    }

    test("a second claim of the same code is rejected") {
        val app = TestApp()
        val org = app.organizationService.create("Example Restaurant")
        val location = app.locationService.create(org.id, "Downtown")
        val ticket = app.enrollmentService.createOrReuse(UUID.randomUUID(), "a".repeat(43), "0.1.0", "host")

        app.enrollmentService.claim(ticket.code, location.id, "Main Menu Wall")

        shouldThrow<DomainError.EnrollmentAlreadyClaimed> {
            app.enrollmentService.claim(ticket.code, location.id, "Second Attempt")
        }
    }

    test("claiming with an unknown location is rejected and nothing is created") {
        val app = TestApp()
        val ticket = app.enrollmentService.createOrReuse(UUID.randomUUID(), "a".repeat(43), "0.1.0", "host")

        shouldThrow<DomainError.LocationNotFound> {
            app.enrollmentService.claim(ticket.code, UUID.randomUUID(), "Main Menu Wall")
        }

        val players = app.jdbi.withHandle<List<com.screenkeeper.control.domain.Player>, RuntimeException> { handle ->
            app.playerRepository.list(handle)
        }
        players shouldBe emptyList()
    }

    test("claiming an unknown code is rejected") {
        val app = TestApp()
        val org = app.organizationService.create("Example Restaurant")
        val location = app.locationService.create(org.id, "Downtown")

        shouldThrow<DomainError.EnrollmentNotFound> {
            app.enrollmentService.claim("ZZZZ-ZZZZ", location.id, "Main Menu Wall")
        }
    }

    test("a rejected claim leaves the enrollment claimable again (transaction rolled back)") {
        val app = TestApp()
        val ticket = app.enrollmentService.createOrReuse(UUID.randomUUID(), "a".repeat(43), "0.1.0", "host")

        shouldThrow<DomainError.LocationNotFound> {
            app.enrollmentService.claim(ticket.code, UUID.randomUUID(), "Main Menu Wall")
        }

        val org = app.organizationService.create("Example Restaurant")
        val location = app.locationService.create(org.id, "Downtown")
        val result = app.enrollmentService.claim(ticket.code, location.id, "Main Menu Wall")
        result.player.name shouldBe "Main Menu Wall"
    }
})
