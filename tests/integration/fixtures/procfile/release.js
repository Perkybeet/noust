// The Procfile's `release` process: runs before a new version serves.
const fs = require("node:fs");
const path = require("node:path");

const version = fs.readFileSync(path.join(__dirname, "VERSION"), "utf8").trim();
console.log(`RELEASE_PROCESS_RAN for version ${version}`);
