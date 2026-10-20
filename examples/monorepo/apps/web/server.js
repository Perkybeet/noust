const http = require("node:http");

http
  .createServer((request, response) => {
    response.end("Hello from Noust (monorepo: web)\n");
  })
  .listen(Number(process.env.PORT || 3000), "127.0.0.1");
