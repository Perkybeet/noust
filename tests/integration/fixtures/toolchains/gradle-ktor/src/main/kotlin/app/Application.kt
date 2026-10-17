package app

import io.ktor.server.application.call
import io.ktor.server.engine.embeddedServer
import io.ktor.server.netty.Netty
import io.ktor.server.response.respondText
import io.ktor.server.routing.get
import io.ktor.server.routing.routing

fun main() {
    val host = System.getenv("HOST") ?: "127.0.0.1"
    val port = (System.getenv("PORT") ?: "8080").toInt()
    embeddedServer(Netty, port = port, host = host) {
        routing {
            get("/") { call.respondText("ktor-gradle v1\n") }
        }
    }.start(wait = true)
}
