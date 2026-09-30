// Serves what the postinstall managed, so the health gate has something to ask.
require("http")
  .createServer((request, response) => {
    let report = "{}";
    try {
      report = require("fs").readFileSync(__dirname + "/sandbox-report.json", "utf8");
    } catch (error) {
      report = JSON.stringify({ missing: String(error) });
    }
    response.setHeader("Content-Type", "application/json");
    response.end(report);
  })
  .listen(process.env.PORT || 3000);
