// A Node.js server with no dependencies. Noust sets PORT and puts the web server in front of it.
const http = require("node:http");

const port = Number(process.env.PORT || 3000);

http
  .createServer((request, response) => {
    response.setHeader("Content-Type", "text/plain; charset=utf-8");
    response.end("Hello from Noust (nodejs)\n");
  })
  .listen(port, "127.0.0.1", () => console.log(`listening on 127.0.0.1:${port}`));
