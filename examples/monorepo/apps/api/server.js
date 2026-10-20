const http = require("node:http");

http
  .createServer((request, response) => {
    response.end("Hello from Noust (monorepo: api)\n");
  })
  .listen(Number(process.env.PORT || 3001), "127.0.0.1");
