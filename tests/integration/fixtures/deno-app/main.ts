// A Deno program as an operator deploys one: it listens where the unit tells it to, and says
// which version it is, so the harness can tell a rollback from an update.
const version = Deno.readTextFileSync("VERSION").trim();
const port = Number(Deno.env.get("PORT") ?? "8000");
const hostname = Deno.env.get("HOST") ?? "127.0.0.1";

if (version === "broken") {
  // The health gate has to refuse a version that never answers.
  throw new Error("this version cannot start");
}

Deno.serve({ hostname, port }, () => new Response(`deno ok ${version}\n`));
console.log(`noust-it-deno-app listening on ${port}, version ${version}`);
