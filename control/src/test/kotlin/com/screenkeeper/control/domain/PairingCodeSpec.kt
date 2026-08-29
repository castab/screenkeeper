package com.screenkeeper.control.domain

import io.kotest.core.spec.style.FunSpec
import io.kotest.matchers.shouldBe

class PairingCodeSpec : FunSpec({
    test("generated codes match the XXXX-XXXX format") {
        repeat(200) {
            val code = PairingCode.generate()
            PairingCode.matchesFormat(code) shouldBe true
        }
    }

    test("generated codes never contain visually confusable characters") {
        val confusable = setOf('0', 'O', '1', 'I', 'L')
        repeat(200) {
            val code = PairingCode.generate()
            code.none { it in confusable } shouldBe true
        }
    }

    test("generated codes are not sequential or repeating across calls") {
        val codes = (1..500).map { PairingCode.generate() }
        codes.toSet().size shouldBe codes.size
    }

    test("matchesFormat rejects malformed input") {
        PairingCode.matchesFormat("") shouldBe false
        PairingCode.matchesFormat("ABCD") shouldBe false
        PairingCode.matchesFormat("ABCD-EFG") shouldBe false
        PairingCode.matchesFormat("ABCD-EFGH-IJKL") shouldBe false
        PairingCode.matchesFormat("abcd-efgh") shouldBe false
        PairingCode.matchesFormat("0000-0000") shouldBe false
    }
})
