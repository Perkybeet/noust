// The same background process under another name: the Procfile of a later version declares it
// instead of the worker, and the harness checks that going back declares the worker again.
const fs = require("node:fs");
const path = require("node:path");

const label = path.basename(process.argv[1], ".js");

function version() {
  try {
    return fs.readFileSync(path.join(__dirname, "VERSION"), "utf8").trim();
  } catch (err) {
    return "unknown";
  }
}

console.log(`${label} up, version ${version()}`);
setInterval(() => console.log(`${label} heartbeat, version ${version()}`), 2000);
