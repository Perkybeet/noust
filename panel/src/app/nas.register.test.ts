/**
 * What the console offers on a machine that only has Docker (items 187 and 188): the one filter (`offers`) read with the table that machine
 * publishes, for the bar, the columns and the palette. A destination that needs systemd or a web server is not there, one that needs only
 * Docker is, and a hub that was not told to deploy has what it always had plus the way in to Deploy on this machine.
 */

import { describe, expect, it } from "vitest";

import { offers } from "../features/central/features";
import { SESSION } from "../test/fakes";
import { hubSession, nasSession } from "../test/nas";
import { areaLanding, dest, paletteEntries, sidebarFor, tabsById } from "./nav";
import type { NavContext } from "./nav";

const context = (session: Parameters<typeof offers>[0]): NavContext => ({ features: (key) => offers(session, key), can: () => true });

const names = (list: readonly { id: string }[]): string[] => list.map((entry) => entry.id);
const paths = (list: readonly { to: string }[]): string[] => list.map((tab) => tab.to);
const bar = (session: Parameters<typeof offers>[0]): string[] => sidebarFor(context(session)).flatMap((group) => names(group));

describe("the bar of a hub that deploys containers on its machine's Docker", () => {
  const items = bar(nasSession());

  it("has what the table says it can do: applications, their logs, the catalog, tunnels, backups and the Docker of the machine", () => {
    for (const id of ["apps", "logs", "catalog", "network", "backups", "server", "alerts", "activity"]) expect(items, `the bar lacks ${id}`).toContain(id);
  });

  it("has none of what needs systemd or a web server of the machine, nor Databases, whose pages ask the machine's own databases", () => {
    for (const id of ["cron", "security", "databases"]) expect(items, `the bar has ${id}`).not.toContain(id);
  });

  it("opens Logs at the logs of the applications, since the machine has no journal to open it at", () => {
    const landing = areaLanding(dest("logs"), context(nasSession()));
    expect(landing.search).not.toEqual({ source: "system" });
  });
});

describe("the Server column of that machine", () => {
  const column = paths(tabsById("server", context(nasSession())));

  it("says what Docker is for here, and offers the machine's own sections that work", () => {
    expect(column).toContain("/server/deploy-here");
    // The Docker zone, which the table gives a machine that has Docker whether or not it deploys on it.
    for (const to of ["/server/docker", "/server/docker/containers", "/server/docker/engine"]) expect(column, `${to} is not there`).toContain(to);
    for (const to of ["/server/folders", "/server/monitor"]) expect(column, `${to} is not there`).toContain(to);
  });

  it("does not offer a service, the web server, the software, a console of its own or a terminal", () => {
    for (const to of ["/server/services", "/server/webserver", "/server/software", "/server/console", "/server/terminal"]) expect(column, `${to} is there`).not.toContain(to);
  });
});

describe("the sections of an application there", () => {
  const sections = paths(tabsById("app", context(nasSession())));

  it("leave out the domains, the databases of the machine, the protection of a site and the diagnosis, which need what it does not have", () => {
    for (const section of ["domains", "database", "access"]) expect(sections, `${section} is there`).not.toContain(`/apps/$domain/${section}`);
  });

  it("keep what only Docker is needed for: its deployments, its logs, its variables and where it is deployed", () => {
    for (const section of ["deployments", "logs", "environment", "delete"]) expect(sections, `${section} is not there`).toContain(`/apps/$domain/${section}`);
  });
});

describe("a hub that was not told to deploy", () => {
  it("has no applications, databases, domains or scheduled tasks in its bar, as it never had", () => {
    const items = bar(hubSession());
    for (const id of ["apps", "databases", "domains", "cron"]) expect(items, `the bar has ${id}`).not.toContain(id);
  });

  it("offers the way in to Deploy on this machine, in its Server column and in the palette", () => {
    const hub = context(hubSession());
    expect(paths(tabsById("server", hub))).toContain("/server/deploy-here");
    const found = paletteEntries({ ...hub, t: (key) => key, scope: "server" }).map((entry) => entry.id);
    expect(found).toContain("server.deploy-here");
  });
});

describe("a server", () => {
  it("is not offered the page that turns on the machine's Docker: it deploys without being asked", () => {
    const server = context(SESSION);
    // A server that publishes no table is offered what its role always allowed, and the page that says how to turn on Docker for a hub is not one of them.
    expect(paths(tabsById("server", server))).not.toContain("/server/deploy-here");
  });
});
