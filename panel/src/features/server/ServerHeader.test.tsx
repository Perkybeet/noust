import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, signedInRoutes } from "../../test/fakes";
import { WEB2, centralSession, fleetRoutes } from "../fleet/testFixtures";
import { onDesktop, serverRoutes } from "./testing";

/**
 * What every section of the Server draws over its content: the section's own name as the title (Status is the machine's),
 * the machine it is about on the line under it, the open ports as a way to where they are looked at, and the one
 * action that is about the machine as a whole, which is not drawn where it would be a distraction.
 */

beforeEach(onDesktop);

function server(path: string) {
  fakeBackend({ ...signedInRoutes(), ...serverRoutes() });
  return renderConsole(path);
}

describe("the header of a section of the Server", () => {
  it("says on Status how many ports are open to the outside, and leads to where they are looked at", async () => {
    server("/server");
    const link = await screen.findByRole("link", { name: /ports? open to the outside/ });
    expect(link).toHaveAttribute("href", "/network/listening");
    expect(link).toHaveTextContent(/^\d+ ports? open to the outside$/);
  });

  it("titles every other section with its own name and names the machine under it, not the ports", async () => {
    server("/server/processes");
    expect(await screen.findByRole("heading", { level: 1, name: "Processes" })).toBeInTheDocument();
    const header = document.querySelector<HTMLElement>("[data-slot=header]");
    if (header === null) throw new Error("no header");
    await waitFor(() => {
      expect(within(header).getByText("web-01")).toBeInTheDocument();
    });
    expect(within(header).queryByRole("link", { name: /open to the outside/ })).not.toBeInTheDocument();
  });

  it.each(["/server", "/server/machine"])("offers to reboot on %s, where the machine is looked at as a whole", async (path) => {
    server(path);
    expect(await screen.findByRole("button", { name: "Reboot" })).toBeInTheDocument();
  });

  it.each(["/server/processes", "/server/services", "/server/files"])("does not offer to reboot on %s, where it would be a distraction", async (path) => {
    server(path);
    await screen.findByRole("navigation", { name: "Server sections" });
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByRole("button", { name: "Reboot" })).not.toBeInTheDocument();
  });

  it("marks the central once, as a tag beside the state, on a fleet's own machine", async () => {
    fakeBackend({ ...serverRoutes(), ...fleetRoutes(centralSession(), [WEB2]) });
    renderConsole("/server");
    await screen.findByRole("heading", { level: 1, name: "web-01" });
    const header = document.querySelector<HTMLElement>("[data-slot=header]");
    if (header === null) throw new Error("no header");
    expect(await within(header).findByText("central")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("does not mark a lone server as a central", async () => {
    server("/server");
    await screen.findByRole("heading", { level: 1, name: "web-01" });
    expect(screen.queryByText("central", { exact: true })).not.toBeInTheDocument();
  });
});
