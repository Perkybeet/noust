// A Deno server. Noust runs the `start` task with PORT and HOST set.
const hostname = Deno.env.get("HOST") ?? "127.0.0.1";
const port = Number(Deno.env.get("PORT") ?? "8000");

Deno.serve({ hostname, port }, () => new Response("Hello from Noust (deno)\n"));
