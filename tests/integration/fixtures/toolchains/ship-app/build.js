// The build: stamp what it was built from. A server that only receives a release never runs this.
const fs = require("node:fs");

fs.writeFileSync("built.txt", fs.readFileSync("VERSION", "utf8"));
fs.writeFileSync("built-on.txt", `${require("node:os").hostname()}\n`);
