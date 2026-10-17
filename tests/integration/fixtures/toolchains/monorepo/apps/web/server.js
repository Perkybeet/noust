// Dependency-free: answers "web <VERSION> built <built.txt> secret <WS_SECRET>", read at start.
// WS_SECRET comes from the workspace's .env.production, which the unit loads.
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

if (version === "crash") {
  console.error("web: refusing to start a release marked crash");
  process.exit(1);
}

http
  .createServer((request, response) => {
    response.end(`web ${version} built ${built} secret ${process.env.WS_SECRET || "unset"}\n`);
  })
  .listen(Number(process.env.PORT || 3000), "127.0.0.1");
