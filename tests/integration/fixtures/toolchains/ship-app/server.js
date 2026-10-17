// A Node server that tells the version it was built at, and refuses to start when that version is
// "crash": what the build server's release is judged on, on the server that receives it.
//
// build.js stamps built.txt with the VERSION it was built from, so a release is told apart from one
// that was built elsewhere or not rebuilt: "ship <VERSION> built <built.txt>".
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
  console.error("ship: refusing to start a release marked crash");
  process.exit(1);
}

http
  .createServer((request, response) => {
    response.end(`ship ${version} built ${built}\n`);
  })
  .listen(Number(process.env.PORT || 3000), "127.0.0.1");
