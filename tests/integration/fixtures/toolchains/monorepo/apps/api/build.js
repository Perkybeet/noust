// A build that leaves something behind a release can be told apart by: the version it was built from.
const fs = require("node:fs");

fs.writeFileSync("built.txt", fs.readFileSync("VERSION", "utf8"));
