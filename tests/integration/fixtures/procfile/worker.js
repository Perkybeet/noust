// A background process for the Noust integration harness: it says which release it runs from.
//
// It reads VERSION beside this file, so a worker left on the code of an earlier release says so
// in its journal (the harness reads it with `noust app process logs`).
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
