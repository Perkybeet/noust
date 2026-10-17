package app

import com.sun.net.httpserver.HttpServer
import java.net.InetSocketAddress

const val VERSION = "v1"

fun main() {
    val host = System.getenv("HOST") ?: "127.0.0.1"
    val port = (System.getenv("PORT") ?: "8080").toInt()
    val body = "kotlin-gradle $VERSION\n".toByteArray()
    val server = HttpServer.create(InetSocketAddress(host, port), 0)
    server.createContext("/") { exchange ->
        exchange.responseHeaders.add("Content-Type", "text/plain; charset=utf-8")
        exchange.sendResponseHeaders(200, body.size.toLong())
        exchange.responseBody.use { it.write(body) }
    }
    server.start()
}
