// Fixture server for the network scenarios of the Noust integration harness.
//
// Dependency-free (node:http only). Every request is answered with a JSON document that says
// which service answered and what it received: the method, the path as it arrived and the
// headers. That lets a scenario prove what nginx did to a request (which prefix reached which
// service, whether the prefix was stripped, which identity headers were set) from the outside.
//
// ECHO_NAME names the service; PORT is where it listens.

const http = require("node:http");

const PORT = process.env.PORT || 3000;
const NAME = process.env.ECHO_NAME || "app";

// What an application that is not WordPress, phpMyAdmin or a router answers a scanner: not found.
// The botsearch jail of fail2ban counts exactly these 404s.
const NOT_HERE =
  /^\/(?:wp-[^/]*|phpmyadmin|pma|mysqladmin|webmail|roundcube|cgi-bin|xmlrpc\.php|\.env)(?:\/|$)|\.php$/i;

const server = http.createServer((req, res) => {
  const path = (req.url || "/").split("?")[0];
  if (NOT_HERE.test(path)) {
    res.writeHead(404, { "Content-Type": "text/plain" });
    res.end("not found\n");
    return;
  }
  const body = JSON.stringify({
    service: NAME,
    method: req.method,
    url: req.url,
    headers: req.headers,
  });
  res.writeHead(200, { "Content-Type": "application/json" });
  res.end(body + "\n");
});

server.listen(PORT, "127.0.0.1");
