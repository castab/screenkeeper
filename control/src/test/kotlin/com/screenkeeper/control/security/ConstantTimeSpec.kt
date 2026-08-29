package com.screenkeeper.control.security

import io.kotest.core.spec.style.FunSpec
import io.kotest.matchers.shouldBe

class ConstantTimeSpec : FunSpec({
    test("equal byte arrays compare equal") {
        ConstantTime.equals(byteArrayOf(1, 2, 3), byteArrayOf(1, 2, 3)) shouldBe true
    }

    test("different byte arrays of the same length compare unequal") {
        ConstantTime.equals(byteArrayOf(1, 2, 3), byteArrayOf(1, 2, 4)) shouldBe false
    }

    test("byte arrays of different lengths compare unequal") {
        ConstantTime.equals(byteArrayOf(1, 2, 3), byteArrayOf(1, 2)) shouldBe false
    }

    test("empty byte arrays compare equal") {
        ConstantTime.equals(ByteArray(0), ByteArray(0)) shouldBe true
    }

    test("equal strings compare equal") {
        ConstantTime.equals("admin-token", "admin-token") shouldBe true
    }

    test("different strings compare unequal") {
        ConstantTime.equals("admin-token", "wrong-token") shouldBe false
    }
})
