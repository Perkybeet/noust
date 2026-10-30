import { describe, expect, it, vi } from "vitest";

import type { SearchHit } from "../api/queries/views";
import { fleetSearchCommands } from "./fleetSearchCommands";

function hit(patch: Partial<SearchHit> = {}): SearchHit {
  return { kind: "app", name: "shop.example.com", node: "web-2", local: false, page: "/apps/shop.example.com", href: "/n/web-2/apps/shop.example.com", detail: "Next.js", labels: { env: "prod" }, ...patch };
}

describe("what the palette finds on the other servers of the fleet", () => {
  it("makes a command of each hit, in the group of its kind, saying which server it is on", () => {
    const commands = fleetSearchCommands(
      [
        hit(),
        hit({ kind: "database", name: "shop_db", detail: "PostgreSQL", page: "/databases/postgresql/shop_db", href: "/n/web-2/databases/postgresql/shop_db" }),
        hit({ kind: "site", name: "shop.example.com", detail: "nginx", page: "/domains/sites/shop.example.com", href: "/n/web-2/domains/sites/shop.example.com" }),
        hit({ kind: "certificate", name: "shop.example.com", detail: "21 days left", page: "/domains", href: "/n/web-2/domains" }),
      ],
      "shop",
      () => undefined,
    );
    expect(commands.map((command) => command.group)).toEqual(["Applications", "Databases", "Domains", "Certificates"]);
    expect(commands[0]).toMatchObject({ label: "shop.example.com", detail: "web-2, Next.js", kind: "navigate" });
    // Two things of one name on one server are two commands.
    expect(new Set(commands.map((command) => command.id)).size).toBe(4);
  });

  it("opens the thing on its own server, not on the one the console is on", () => {
    const navigate = vi.fn();
    const [command] = fleetSearchCommands([hit()], "shop", navigate);
    command?.run();
    expect(navigate).toHaveBeenCalledWith({ to: "/apps/shop.example.com", search: { node: "web-2" } });
  });

  it("is found by what the server matched it on, which its label does not show", () => {
    const [command] = fleetSearchCommands([hit()], "prod", () => undefined);
    expect(command?.keywords).toContain("prod");
    expect(command?.keywords).toContain("web-2");
  });

  it("offers nothing the palette already has: what is on this server, a server (the register lists them) and a kind it has no group for", () => {
    const commands = fleetSearchCommands([hit({ local: true }), hit({ kind: "server", name: "web-2" }), hit({ kind: "cron", name: "nightly" })], "web", () => undefined);
    expect(commands).toEqual([]);
  });

  it("says only the server when a hit has nothing else to say", () => {
    const [command] = fleetSearchCommands([hit({ detail: null })], "shop", () => undefined);
    expect(command?.detail).toBe("web-2");
  });
});
