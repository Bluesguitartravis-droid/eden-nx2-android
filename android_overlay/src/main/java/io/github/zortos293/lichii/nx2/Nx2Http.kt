// SPDX-License-Identifier: GPL-3.0-or-later
// Eden NX2: the small HTTP/1.1 reader and writer shared by the Claude bridge (Eden's process) and
// the Claude Lab front server (":nx2lab" process, Nx2FrontService).
package io.github.zortos293.lichii.nx2

import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.io.OutputStream
import java.util.Locale

internal object Nx2Http {
    const val MAX_BODY = 1 shl 20

    class Request(val method: String, val path: String, val headers: Map<String, String>,
                          val body: ByteArray)

    private fun readLine(input: InputStream): String? {
        val out = ByteArrayOutputStream()
        while (true) {
            val b = input.read()
            if (b < 0) return if (out.size() == 0) null else out.toString("UTF-8")
            if (b == '\n'.code) break
            if (b != '\r'.code) out.write(b)
            if (out.size() > 16384) throw IllegalStateException("header line too long")
        }
        return out.toString("UTF-8")
    }

    private fun readExactly(input: InputStream, count: Int): ByteArray {
        val data = ByteArray(count)
        var offset = 0
        while (offset < count) {
            val n = input.read(data, offset, count - offset)
            if (n < 0) throw IllegalStateException("body ended early")
            offset += n
        }
        return data
    }

    fun readRequest(input: InputStream): Request? {
        val first = readLine(input) ?: return null
        val parts = first.split(' ')
        if (parts.size < 2) return null
        val headers = HashMap<String, String>()
        while (true) {
            val line = readLine(input) ?: break
            if (line.isEmpty()) break
            val colon = line.indexOf(':')
            if (colon > 0) {
                headers[line.substring(0, colon).trim().lowercase(Locale.US)] = line.substring(colon + 1).trim()
            }
        }
        val body = when {
            headers["transfer-encoding"]?.contains("chunked", ignoreCase = true) == true -> {
                val out = ByteArrayOutputStream()
                while (true) {
                    val sizeLine = readLine(input) ?: break
                    val size = sizeLine.substringBefore(';').trim().toInt(16)
                    if (size == 0) {
                        while (true) { val trailer = readLine(input) ?: break; if (trailer.isEmpty()) break }
                        break
                    }
                    if (out.size() + size > MAX_BODY) throw IllegalStateException("body too large")
                    out.write(readExactly(input, size))
                    readLine(input)
                }
                out.toByteArray()
            }
            else -> {
                val length = headers["content-length"]?.toIntOrNull() ?: 0
                if (length > MAX_BODY) throw IllegalStateException("body too large")
                readExactly(input, length)
            }
        }
        return Request(parts[0].uppercase(Locale.US), parts[1], headers, body)
    }

    fun respond(out: OutputStream, status: Int, reason: String, body: ByteArray?,
                        contentType: String? = null, extra: Map<String, String> = emptyMap()) {
        val head = StringBuilder()
        head.append("HTTP/1.1 ").append(status).append(' ').append(reason).append("\r\n")
        if (contentType != null) head.append("Content-Type: ").append(contentType).append("\r\n")
        head.append("Content-Length: ").append(body?.size ?: 0).append("\r\n")
        head.append("Cache-Control: no-store\r\n")
        for ((k, v) in extra) head.append(k).append(": ").append(v).append("\r\n")
        head.append("Connection: close\r\n\r\n")
        out.write(head.toString().toByteArray(Charsets.UTF_8))
        if (body != null) out.write(body)
        out.flush()
    }

}
