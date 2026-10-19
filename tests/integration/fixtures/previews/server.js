// A Node server for the preview scenario: the version, and what a preview is told about itself.
//
// GET /     -> "ok <version>", read from ./VERSION
// GET /env  -> the variables a preview is given, as JSON; the database URL's password is hidden.
const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");

const PORT = process.env.PORT || 3000;

function version() {
  try {
    return fs.readFileSync(path.join(__dirname, "VERSION"), "utf8").trim();
  } catch (err) {
    return "unknown";
  }
}

http
  .createServer((req, res) => {
    if (req.url === "/env") {
      const url = (process.env.DATABASE_URL || "").replace(/:[^:@/]*@/, ":***@");
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(
        JSON.stringify({
          pr: process.env.NOUST_PR_NUMBER || null,
          previewOf: process.env.NOUST_PREVIEW_OF || null,
          secretKey: process.env.SECRET_KEY || null,
          publicKey: process.env.NEXT_PUBLIC_X || null,
          previewOnly: process.env.PREVIEW_ONLY || null,
          databaseUrl: url || null,
        }),
      );
      return;
    }
    res.writeHead(200, { "Content-Type": "text/plain" });
    res.end(`ok ${version()}\n`);
  })
  .listen(PORT, () => console.log(`preview fixture listening on ${PORT}, version ${version()}`));
