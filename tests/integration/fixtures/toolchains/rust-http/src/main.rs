use std::env;

// The line the harness changes between releases.
const VERSION: &str = "rust v1";

fn main() {
    let port = env::var("PORT").unwrap_or_else(|_| "8080".to_string());
    let host = env::var("HOST").unwrap_or_else(|_| "127.0.0.1".to_string());
    let server = tiny_http::Server::http(format!("{host}:{port}")).expect("cannot bind");
    for request in server.incoming_requests() {
        let _ = request.respond(tiny_http::Response::from_string(format!("{VERSION}\n")));
    }
}
