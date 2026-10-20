// A Rust server with the standard library only. Noust builds it with the server's cargo and runs the binary.
use std::io::{Read, Write};
use std::net::TcpListener;

fn main() -> std::io::Result<()> {
    let port = std::env::var("PORT").unwrap_or_else(|_| "8080".to_string());
    let listener = TcpListener::bind(format!("127.0.0.1:{port}"))?;
    for stream in listener.incoming() {
        let mut stream = stream?;
        let mut buffer = [0u8; 1024];
        let _ = stream.read(&mut buffer);
        let body = "Hello from Noust (rust)\n";
        write!(
            stream,
            "HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
            body.len(),
            body
        )?;
    }
    Ok(())
}
