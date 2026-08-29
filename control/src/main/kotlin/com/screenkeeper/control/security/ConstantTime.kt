package com.screenkeeper.control.security

import java.security.MessageDigest

/** MessageDigest.isEqual is documented time-constant regardless of where the
 *  inputs first differ, unlike ByteArray.contentEquals or String.equals. */
object ConstantTime {
    fun equals(a: ByteArray, b: ByteArray): Boolean = MessageDigest.isEqual(a, b)

    fun equals(a: String, b: String): Boolean = equals(a.utf8Bytes(), b.utf8Bytes())
}
