package app;

import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.InputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;

/** The smallest JVM web program: answers "jvm-maven <version>", the version read from the jar. */
public final class Main {
    private Main() {}

    public static void main(String[] args) throws IOException {
        String host = System.getenv().getOrDefault("HOST", "127.0.0.1");
        int port = Integer.parseInt(System.getenv().getOrDefault("PORT", "8080"));
        String body = "jvm-maven " + version() + "\n";
        HttpServer server = HttpServer.create(new InetSocketAddress(host, port), 0);
        server.createContext("/", exchange -> {
            byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().add("Content-Type", "text/plain; charset=utf-8");
            exchange.sendResponseHeaders(200, bytes.length);
            exchange.getResponseBody().write(bytes);
            exchange.close();
        });
        server.start();
    }

    private static String version() throws IOException {
        try (InputStream in = Main.class.getResourceAsStream("/version.txt")) {
            return in == null ? "unknown" : new String(in.readAllBytes(), StandardCharsets.UTF_8).trim();
        }
    }
}
