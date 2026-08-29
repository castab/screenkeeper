package com.screenkeeper.control.security

import java.nio.charset.StandardCharsets
import java.security.MessageDigest

fun String.utf8Bytes(): ByteArray = toByteArray(StandardCharsets.UTF_8)

fun sha256(bytes: ByteArray): ByteArray = MessageDigest.getInstance("SHA-256").digest(bytes)

fun sha256(text: String): ByteArray = sha256(text.utf8Bytes())
