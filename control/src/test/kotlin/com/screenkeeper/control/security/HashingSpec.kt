package com.screenkeeper.control.security

import io.kotest.core.spec.style.FunSpec
import io.kotest.matchers.shouldBe

private fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it) }

class HashingSpec : FunSpec({
    test("sha256 of the empty string matches the NIST test vector") {
        sha256("").toHex() shouldBe "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    }

    test("sha256 of \"abc\" matches the NIST test vector") {
        sha256("abc").toHex() shouldBe "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    }

    test("sha256 is deterministic") {
        sha256("device-token-example") shouldBe sha256("device-token-example")
    }

    test("sha256 differs for different inputs") {
        sha256("a").contentEquals(sha256("b")) shouldBe false
    }
})
