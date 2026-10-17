// Dependency-free: answers "api <VERSION> built <built.txt>", read at start.
const fs = require("node:fs");
const http = require("node:http");

const read = (name) => {
  try {
    return fs.readFileSync(name, "utf8").trim();
  } catch {
    return "none";
  }
};
const version = read("VERSION");
const built = read("built.txt");

// A release that cannot start: what the health gate exists to catch.
if (version === "crash") {
  console.error("api: refusing to start a release marked crash");
  process.exit(1);
}

http
  .createServer((request, response) => {
    response.end(`api ${version} built ${built}\n`);
  })
  .listen(Number(process.env.PORT || 3000), "127.0.0.1");
