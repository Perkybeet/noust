import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import type { SessionInfo } from "../api/queries/auth";
import { renderConsole } from "../test/console";
import { NODES, SESSION, fakeBackend, json, onNode, signedInRoutes } from "../test/fakes";
import { forgetServer, rememberServer } from "./lastServer";

/**
 * "Todos los servidores" en toda la consola (items 196 y 187), en el navegador simulado: el router, el marco y el servidor de mentira. Cada
 * caso del dueño tiene su prueba con sus palabras en el nombre.
 */

/** The functions of a hub that has systemd and the rest of a VPS: what it cannot do is what needs the role of server. */
const HUB_LACKS = new Set([
  "applications",
  "native_deploy",
  "container_deploy",
  "releases",
  "rollbacks",
  "preview_environments",
  "importing_applications",
  "recipes",
  "application_environments",
  "application_diagnosis",
  "process_monitor",
  "server_health",
  "server_setup",
  "domains",
  "sites",
  "certificates",
  "console_exposure",
  "status_page",
  "published_ports",
  "tunnels",
  "noust_dns",
  "dns_records",
  "dns_accounts",
  "databases",
  "database_instances",
  "public_database_access",
  "database_backups",
  "point_in_time_recovery",
  "application_backups",
  "volume_backups",
  "restoring_a_server",
  "moving_a_server",
  "checking_a_moved_server",
  "scheduled_jobs",
  "services",
  "software",
  "log_destinations",
]);

/** The session as a hub or a server publishes it: only what a hub cannot do needs an entry, and a function the table does not name is offered. */
function sessionOf(role: "hub" | "server"): SessionInfo {
  const features = Object.fromEntries(
    [...HUB_LACKS].map((key) => [
      key,
      role === "hub"
        ? { available: false, reason: { code: "features.hub_role", params: { feature: key, command: "noust config set central.role server" }, text: `${key}: not available on this central, which is a hub` } }
        : { available: true, reason: null },
    ]),
  );
  return { ...SESSION, central: { role, sealed: false, locked: false, features: features as never } };
}

function fleet(role: "hub" | "server") {
  const session = sessionOf(role);
  return fakeBackend({
    // The central's own answers (its session among them) come last: what a node's routes say of the central is the default.
    ...onNode("web-2", signedInRoutes()),
    ...onNode("db-1", signedInRoutes()),
    ...signedInRoutes(session),
    "GET /api/nodes": () => json(200, { items: NODES }),
  });
}

async function consoleAt(path: string, role: "hub" | "server") {
  const backend = fleet(role);
  const harness = renderConsole(path);
  await screen.findByRole("heading", { level: 1 });
  return { ...harness, backend };
}

const selector = () => screen.findByRole("button", { name: /^Server: / });
const bar = () => screen.getByRole("navigation", { name: "Main" });
/** The names of the links of a list, without the count that a section of a column may carry after its name. */
const names = (list: HTMLElement): string[] => within(list).getAllByRole("link").map((link) => link.textContent.replace(/\s+/g, " ").replace(/\d+$/, "").trim());

beforeEach(() => {
  forgetServer();
});

describe("con Todos los servidores elegido", () => {
  for (const role of ["hub", "server"] as const) {
    it(`Bases de datos y Tareas programadas abren su página, con la barra donde estaba y el selector sin cambiar (${role})`, async () => {
      const { location } = await consoleAt("/fleet/apps", role);
      expect(await selector()).toHaveAccessibleName("Server: all servers");

      for (const [name, path] of [
        ["Databases", "/fleet/databases"],
        ["Scheduled tasks", "/fleet/cron"],
      ] as const) {
        fireEvent.click(within(bar()).getByRole("link", { name: new RegExp(`^${name}`) }));
        await waitFor(() => {
          expect(location().pathname).toBe(path);
        });
        // The page is drawn (a heading and the line that says whose it is), the bar is on the area that was pressed, and the selector did not move.
        expect(await screen.findByRole("heading", { level: 1 })).toBeInTheDocument();
        expect(await screen.findByText(/servers that have it|server that has it/)).toBeInTheDocument();
        expect(within(bar()).getByRole("link", { name: new RegExp(`^${name}`) })).toHaveAttribute("aria-current", "page");
        expect(await selector()).toHaveAccessibleName("Server: all servers");
      }
    });

    it(`pulsar Catálogo no cambia el selector ni quita opciones de la barra (${role})`, async () => {
      const { location } = await consoleAt("/fleet/apps", role);
      const before = names(bar());
      fireEvent.click(within(bar()).getByRole("link", { name: /^Catalog/ }));
      await waitFor(() => {
        expect(location().pathname).toBe("/fleet/catalog");
      });
      expect(await screen.findByRole("heading", { level: 1, name: "Catalog" })).toBeInTheDocument();
      expect(await selector()).toHaveAccessibleName("Server: all servers");
      expect(names(bar())).toEqual(before);
      // The recipes are the same on every server, and the page says so.
      expect(await screen.findByText(/The same on every server/)).toBeInTheDocument();
    });

    it(`Copias con Todos los servidores enseña las secciones que enseña con un servidor (${role})`, async () => {
      const { location, history } = await consoleAt("/n/web-2/backups", role);
      const onServer = names(await screen.findByRole("navigation", { name: "Backups sections" }));
      expect(onServer).toEqual(expect.arrayContaining(["Backups", "Schedules", "Volumes", "Noust", "Destinations"]));

      history.push("/fleet/backups");
      await waitFor(() => {
        expect(location().pathname).toBe("/fleet/backups");
      });
      const column = await screen.findByRole("navigation", { name: "Backups sections" });
      await waitFor(() => {
        expect(names(column)).toEqual(expect.arrayContaining(onServer));
      });
      expect(await selector()).toHaveAccessibleName("Server: all servers");
    });

    it(`ningún clic de la barra ni de la columna cambia el selector (${role})`, async () => {
      const { location } = await consoleAt("/fleet/apps", role);
      const targets = within(bar())
        .getAllByRole("link")
        .map((link) => link.getAttribute("href") ?? "")
        .filter((href) => href.startsWith("/fleet"));
      expect(targets.length).toBeGreaterThanOrEqual(12);
      for (const href of targets) {
        fireEvent.click(within(bar()).getByRole("link", { name: (_, element) => element.getAttribute("href") === href }));
        await waitFor(() => {
          expect(location().pathname).toBe(href);
        });
        expect(await selector()).toHaveAccessibleName("Server: all servers");
      }
    });
  }

  it("el ámbito sobrevive a pasar por Ajustes: la barra de vuelta sigue siendo la de Todos los servidores", async () => {
    const { location } = await consoleAt("/fleet/apps", "server");
    const settings = screen.getAllByRole("link", { name: "Settings" })[0];
    if (settings === undefined) throw new Error("the bar has no link to Settings");
    fireEvent.click(settings);
    await waitFor(() => {
      expect(location().pathname).toBe("/settings");
    });
    // The selector is a label there; the bar still opens every area at its page of All servers, and Databases is one of them.
    await waitFor(() => {
      expect(within(bar()).getByRole("link", { name: /^Databases/ })).toHaveAttribute("href", "/fleet/databases");
    });
  });
});

describe("un hub elegido en el selector", () => {
  beforeEach(() => {
    // The operator chose the hub itself: its home is its home, and it offers what its table says.
    rememberServer(null);
  });

  it("tiene en la barra lo que un hub puede hacer, y nada que no pueda", async () => {
    await consoleAt("/", "hub");
    const links = names(bar());
    // This hub is a VPS: it has its machine (systemd, the journal, a firewall) and no role of server.
    for (const gone of [/^Applications/, /^Databases/, /^Scheduled tasks/, /^Domains/]) {
      expect(links.some((name) => gone.test(name)), `the bar of a hub has ${String(gone)}: ${links.join(" | ")}`).toBe(false);
    }
    for (const kept of [/^Home/, /^Catalog/, /^Network/, /^Logs/, /^Alerts/, /^Activity/, /^Backups/, /^Security/, /^Server/]) {
      expect(links.some((name) => kept.test(name)), `the bar of a hub lacks ${String(kept)}: ${links.join(" | ")}`).toBe(true);
    }
    // Backups opens at the one copy a hub can make, its own; Network at what the machine has, and Logs at the system's (it has no applications).
    expect(within(bar()).getByRole("link", { name: /^Backups/ })).toHaveAttribute("href", "/backups/noust");
    expect(within(bar()).getByRole("link", { name: /^Network/ })).toHaveAttribute("href", "/network/firewall");
    expect(within(bar()).getByRole("link", { name: /^Logs/ })).toHaveAttribute("href", "/logs?source=system");
  });

  it("dice una vez, sin Reintentar, que una dirección escrita no existe en él", async () => {
    await consoleAt("/databases", "hub");
    expect(await screen.findByRole("heading", { level: 2, name: /not available on/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /try again/i })).toBeNull();
    // It offers the way to where it works.
    expect(screen.getByRole("link", { name: "See it on every server" })).toHaveAttribute("href", "/fleet/databases");
  });

  it("no pide a la central lo que un hub no hace al abrir la paleta (ni las bases de datos ni los servicios)", async () => {
    const { backend, user } = await consoleAt("/", "hub");
    await user.keyboard("{Control>}k{/Control}");
    await screen.findByRole("combobox");
    // What the palette reads is read once it is open: none of it is asked of a server that cannot do it.
    await waitFor(() => {
      expect(backend.calls.length).toBeGreaterThan(0);
    });
    expect(backend.calls.filter((call) => call.path === "/api/databases/databases" || call.path === "/api/services")).toHaveLength(0);
  });
});

describe("un servidor solo", () => {
  it("no ve selector, ni columna de servidor, ni la línea que dice de quién es una página", async () => {
    fakeBackend({ ...signedInRoutes(), "GET /api/nodes": () => json(200, { items: [] }) });
    renderConsole("/catalog");
    expect(await screen.findByRole("heading", { level: 1, name: "Catalog" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Server: / })).toBeNull();
    // The catalog's line ("the same on every server") is for a fleet: a lone server has nothing to tell apart.
    expect(screen.queryByText(/The same on every server/)).toBeNull();
    // The bar is what it always was: every area opens its own page, never an address of All servers.
    const links = within(bar())
      .getAllByRole("link")
      .map((link) => link.getAttribute("href") ?? "");
    expect(links.filter((href) => href.startsWith("/fleet"))).toEqual([]);
  });
});
