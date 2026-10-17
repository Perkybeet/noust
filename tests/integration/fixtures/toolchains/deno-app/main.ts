// A Deno server with one dependency from JSR, so that "deno install" has something to cache in the
// release and the unit starts without a network.
import { join } from "@std/path";

const version = (await Deno.readTextFile(join(import.meta.dirname ?? ".", "VERSION"))).trim();
const hostname = Deno.env.get("HOST") ?? "127.0.0.1";
const port = Number(Deno.env.get("PORT") ?? "8000");

Deno.serve({ hostname, port }, () => new Response(`deno-app ${version}\n`));
