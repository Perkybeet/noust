import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend } from "../../../test/fakes";
import { BUILDER, SHOP, placementRoutes } from "./testFixtures";

async function openMenu(user: ReturnType<typeof renderConsole>["user"]): Promise<void> {
  await user.click(await screen.findByRole("button", { name: `Actions for ${SHOP}` }));
}

async function openShip(user: ReturnType<typeof renderConsole>["user"]): Promise<HTMLElement> {
  await openMenu(user);
  await user.click(await screen.findByRole("menuitem", { name: "Build on one server, run on the others" }));
  return screen.findByRole("dialog", { name: `Build ${SHOP} on one server, run it on the others` });
}

describe("Fleet > On several servers > Build on one server, run on the others", () => {
  it("asks which server builds before it plans anything", { timeout: 40_000 }, async () => {
    const backend = fakeBackend(placementRoutes());
    const { user } = renderConsole("/fleet/placements");

    const dialog = await openShip(user);
    await user.click(within(dialog).getByRole("button", { name: "Show the plan" }));

    expect(await within(dialog).findByText("Choose the server that builds it.")).toBeInTheDocument();
    expect(backend.callsTo(`POST /api/fleet/placements/${SHOP}/ship`)).toHaveLength(0);
  });

  it("plans with the builder and the servers chosen, then remembers the builder and runs", { timeout: 60_000 }, async () => {
    const backend = fakeBackend(placementRoutes());
    const { user, location } = renderConsole("/fleet/placements");

    const dialog = await openShip(user);
    expect(within(dialog).getByRole("checkbox", { name: "web-2" })).toBeChecked();
    expect(within(dialog).getByRole("checkbox", { name: "web-3" })).toBeChecked();
    await user.click(within(dialog).getByRole("combobox", { name: "Build it on" }));
    await user.click(await screen.findByRole("option", { name: "db-1" }));
    await user.click(within(dialog).getByRole("button", { name: "Show the plan" }));

    expect(await within(dialog).findByText(/db-1 builds shop.example.com once/)).toBeInTheDocument();
    expect(within(dialog).getByText(/web-2 goes first and alone/)).toBeInTheDocument();
    expect(backend.callsTo(`POST /api/fleet/placements/${SHOP}/ship`)[0]?.body).toMatchObject({
      builder: "db-1",
      to: ["web-2", "web-3"],
      commit: null,
      plan: true,
      force: false,
      rollback_on_failure: true,
    });
    expect(backend.callsTo(`PUT /api/fleet/builders/${SHOP}`)).toHaveLength(0);

    await user.click(within(dialog).getByRole("button", { name: "Ship to 2 servers" }));
    await waitFor(() => {
      expect(location().pathname).toBe("/fleet/jobs/fj-11");
    });
    expect(backend.callsTo(`PUT /api/fleet/builders/${SHOP}`)[0]?.body).toEqual({ node: "db-1" });
    expect(backend.callsTo(`POST /api/fleet/placements/${SHOP}/ship`)[1]?.body).toMatchObject({ plan: false });
  });

  it("uses the builder already recorded, and says on the card where it builds", { timeout: 60_000 }, async () => {
    const backend = fakeBackend(placementRoutes(undefined, [BUILDER]));
    const { user } = renderConsole("/fleet/placements");

    expect(await screen.findByText("Built on db-1")).toBeInTheDocument();
    const dialog = await openShip(user);
    expect(await within(dialog).findByRole("combobox", { name: "Build it on" })).toHaveTextContent("db-1");
    expect(within(dialog).queryByRole("checkbox", { name: /Remember it as the build server/ })).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Show the plan" }));

    await waitFor(() => {
      expect(backend.callsTo(`POST /api/fleet/placements/${SHOP}/ship`)).toHaveLength(1);
    });
    expect(backend.callsTo(`POST /api/fleet/placements/${SHOP}/ship`)[0]?.body).toMatchObject({ builder: "db-1" });
  });

  it("asks for a commit that is a commit, and sends it", { timeout: 60_000 }, async () => {
    const backend = fakeBackend(placementRoutes(undefined, [BUILDER]));
    const { user } = renderConsole("/fleet/placements");

    const dialog = await openShip(user);
    await user.type(within(dialog).getByLabelText(/Build this commit/), "nope");
    await user.click(within(dialog).getByRole("button", { name: "Show the plan" }));
    expect(await within(dialog).findByText("Write 7 to 40 hexadecimal characters.")).toBeInTheDocument();
    expect(backend.callsTo(`POST /api/fleet/placements/${SHOP}/ship`)).toHaveLength(0);

    await user.clear(within(dialog).getByLabelText(/Build this commit/));
    await user.type(within(dialog).getByLabelText(/Build this commit/), "ABC1234");
    await user.click(within(dialog).getByRole("button", { name: "Show the plan" }));

    await waitFor(() => {
      expect(backend.callsTo(`POST /api/fleet/placements/${SHOP}/ship`)).toHaveLength(1);
    });
    expect(backend.callsTo(`POST /api/fleet/placements/${SHOP}/ship`)[0]?.body).toMatchObject({ commit: "abc1234" });
  });

  it("will not send it to nobody", { timeout: 60_000 }, async () => {
    const backend = fakeBackend(placementRoutes(undefined, [BUILDER]));
    const { user } = renderConsole("/fleet/placements");

    const dialog = await openShip(user);
    await user.click(within(dialog).getByRole("checkbox", { name: "web-2" }));
    await user.click(within(dialog).getByRole("checkbox", { name: "web-3" }));
    await user.click(within(dialog).getByRole("button", { name: "Show the plan" }));

    expect(await within(dialog).findByText("Choose at least one server to run it on.")).toBeInTheDocument();
    expect(backend.callsTo(`POST /api/fleet/placements/${SHOP}/ship`)).toHaveLength(0);
  });

  it("has nothing for an axe check to find", { timeout: 60_000 }, async () => {
    fakeBackend(placementRoutes(undefined, [BUILDER]));
    const { user } = renderConsole("/fleet/placements");

    const dialog = await openShip(user);

    await expectNoAxeViolations(dialog, {});
  });

  it("chooses and forgets the build server without touching a server", { timeout: 60_000 }, async () => {
    const backend = fakeBackend(placementRoutes(undefined, [BUILDER]));
    const { user } = renderConsole("/fleet/placements");

    await openMenu(user);
    await user.click(await screen.findByRole("menuitem", { name: "Choose the build server" }));
    const dialog = await screen.findByRole("dialog", { name: `Build server of ${SHOP}` });
    expect(within(dialog).getByRole("combobox", { name: "Build server" })).toHaveTextContent("db-1");
    await user.click(within(dialog).getByRole("combobox", { name: "Build server" }));
    await user.click(await screen.findByRole("option", { name: "Built where it runs" }));
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo(`DELETE /api/fleet/builders/${SHOP}`)).toHaveLength(1);
    });
  });

  it("is in Spanish too", { timeout: 60_000 }, async () => {
    fakeBackend(placementRoutes(undefined, [BUILDER]));
    const { user } = renderConsole("/fleet/placements");
    await act(async () => {
      await setLocale("es");
    });

    await user.click(await screen.findByRole("button", { name: `Acciones de ${SHOP}` }));
    await user.click(await screen.findByRole("menuitem", { name: "Compilar en un servidor y ejecutar en los demás" }));

    expect(await screen.findByRole("dialog", { name: `Compilar ${SHOP} en un servidor y ejecutarla en los demás` })).toBeInTheDocument();
    await act(async () => {
      await setLocale("en");
    });
  });
});
