package com.screenkeeper.control.domain

import java.security.SecureRandom

/**
 * Short, human-typed pairing codes like "Q7KM-4HF2". Not a credential --
 * see contracts/openapi.yaml -- but generated with a CSPRNG anyway so codes
 * are neither sequential nor guessable from a timestamp.
 */
object PairingCode {
    // Crockford-ish alphabet: uppercase letters and digits with the visually
    // confusable characters (0/O, 1/I/L) removed.
    private const val ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    private const val GROUP_LENGTH = 4
    private const val GROUPS = 2

    private val random = SecureRandom()

    fun generate(): String =
        (1..GROUPS).joinToString("-") {
            (1..GROUP_LENGTH).map { ALPHABET[random.nextInt(ALPHABET.length)] }.joinToString("")
        }

    fun matchesFormat(code: String): Boolean {
        val groups = code.split("-")
        if (groups.size != GROUPS) return false
        return groups.all { group -> group.length == GROUP_LENGTH && group.all { it in ALPHABET } }
    }
}
