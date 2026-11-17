import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { nasSession } from "../../../test/nas";
import { screenWidth } from "../../app/testRoutes";
import { CONTAINER_ENGINE, databaseRoutes } from "../testFixtures";
import { BUDGET, CATALOG, INSTANCE, INSTANCE_SERVER } from "../instances/instanceFixtures";

/**
 * The database engines of a machine that only has Docker (items 187 and 188): the table says the system's own engines are not here, and the
 * server's lists answer with the instances Noust runs in containers and nothing else. The page lists them, says why there is no engine of the
 * system in the server's own words, and offers only what works: another engine in a container, never an install with the package manager.
 */

/** What `GET /api/databases/*` says of the system's engines on the central of a NAS: the table's sentence for `databases`. */
const NO_SYSTEM = {
  available: false,
  reason: {
    code: "features.containers_only",
    params: { feature: "Databases" },
    text: "Databases: not available here, which deploys containers and nothing else",
    hint: "This central deploys containers on its machine's Docker. Deploy it on a server of your fleet instead.",
  },
};

async function engineList(extra: Record<string, RouteHandler> = {}) {
  screenWidth(1440);
  const backend = fakeBackend(
    databaseRoutes({
      "GET /api/auth/session": () => json(200, nasSession()),
      "GET /api/databases/servers": () => json(200, { servers: [INSTANCE_SERVER], problems: [], system: NO_SYSTEM }),
      "GET /api/databases/engines": () => json(200, { engines: [CONTAINER_ENGINE], system: NO_SYSTEM }),
      "GET /api/databases/databases": () => json(200, { databases: [], problems: [], total: 0, system: NO_SYSTEM }),
      "GET /api/databases/instances": () => json(200, { instances: [INSTANCE], budget: BUDGET }),
      "GET /api/databases/instances/catalog": () => json(200, CATALOG),
      ...extra,
    }),
  );
  const harness = renderConsole("/databases/engines");
  await screen.findByRole("heading", { level: 1, name: "Engines" });
  const list = await screen.findByRole("list", { name: "Engines" });
  return { ...harness, backend, list };
}

describe("the engines of a machine that only has Docker", () => {
  it("lists the instances and says, in the server's words, why the system has no engines of its own", async () => {
    const { list } = await engineList();
    expect(within(list).getByRole("heading", { level: 2, name: "PostgreSQL 16" })).toBeInTheDocument();
    const why = await screen.findByText(/Databases: not available here, which deploys containers and nothing else/);
    expect(why).toBeInTheDocument();
    expect(screen.getByText(/Deploy it on a server of your fleet instead/)).toBeInTheDocument();
    // The line under the title says only what there is, not "the system's own" engines the machine does not have.
    expect(screen.getByText("The programs that hold the databases: the instances Noust runs in containers, in other versions.")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("goes from Add an engine straight to making one in a container: nothing asks where it comes from, and nothing installs a package", async () => {
    const { user } = await engineList();
    await user.click(screen.getByRole("button", { name: "Add an engine" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByText("On the system")).not.toBeInTheDocument();
    expect(within(dialog).getByRole("heading", { name: "Add an engine" })).toBeInTheDocument();
    // The form of the container is the one that opened: its engines come from the instances' catalog.
    expect(await within(dialog).findByText("Engine")).toBeInTheDocument();
  });

  it("says an engine of a stack of which no application is known without a link to nothing", async () => {
    const orphan = { ...INSTANCE_SERVER, key: "postgresql@catalogo.postgres", runs_in: "app_container", name: null, app: null };
    const { list } = await engineList({ "GET /api/databases/servers": () => json(200, { servers: [orphan], problems: [], system: NO_SYSTEM }) });
    expect(await within(list).findByText("In the container of a stack")).toBeInTheDocument();
    expect(within(list).queryAllByRole("link", { name: "" })).toHaveLength(0);
  });

  it("does not say anything about the system when the machine has engines of its own, or when the server does not say", async () => {
    const { list } = await engineList({ "GET /api/databases/servers": () => json(200, { servers: [INSTANCE_SERVER], problems: [] }) });
    expect(within(list).getByRole("heading", { level: 2, name: "PostgreSQL 16" })).toBeInTheDocument();
    expect(screen.queryByText(/not available here/)).not.toBeInTheDocument();
    expect(screen.getByText("The programs that hold the databases: the system's own, and the instances Noust runs in containers, in other versions.")).toBeInTheDocument();
  });
});

describe("the databases of a machine that only has Docker", () => {
  it("are listed, and with none running says to make an engine in a container", async () => {
    screenWidth(1440);
    fakeBackend(
      databaseRoutes({
        "GET /api/auth/session": () => json(200, nasSession()),
        "GET /api/databases/servers": () => json(200, { servers: [], problems: [], system: NO_SYSTEM }),
        "GET /api/databases/engines": () => json(200, { engines: [], system: NO_SYSTEM }),
        "GET /api/databases/databases": () => json(200, { databases: [], problems: [], total: 0, system: NO_SYSTEM }),
        "GET /api/databases/instances": () => json(200, { instances: [], budget: BUDGET }),
      }),
    );
    renderConsole("/databases");
    expect(await screen.findByText("No database engine is running here. Make one in a container, then create a database on it.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Add an engine" })).toBeInTheDocument();
  });
});
