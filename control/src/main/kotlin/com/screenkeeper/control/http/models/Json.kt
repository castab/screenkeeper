package com.screenkeeper.control.http.models

import org.http4k.format.ConfigurableKotlinxSerialization

/**
 * Shared serializer for every route. ignoreUnknownKeys implements the
 * contract's "servers should tolerate unknown request fields" rule for
 * every nested object in one place.
 */
object Json : ConfigurableKotlinxSerialization({
    ignoreUnknownKeys = true
    explicitNulls = false
    encodeDefaults = true
})
