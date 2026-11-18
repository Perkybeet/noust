/**
 * The page of a section a central cannot do, reached by an address that was typed or kept: it says why in the server's own sentence and
 * leads to where that is mended. On a hub that was not told to deploy on its machine's Docker the Docker zone is such a section, and the
 * sentence is the server's (`features.needs_deploy_here`), not a guess of the console's about Docker.
 */

import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../test/axe";
import { renderConsole } from "../test/console";
import { fakeBackend, signedInRoutes } from "../test/fakes";
import { hubSession, nasSession } from "../test/nas";
import { screenWidth } from "../features/app/testRoutes";

describe("the Docker zone of a hub that was not told to deploy here", () => {
  it("says the server's sentence and its fix when the address is typed, and leads to the page that says how it is turned on", async () => {
    screenWidth(1440);
    fakeBackend(signedInRoutes(hubSession()));
    const { container } = renderConsole("/server/docker/stacks");
    const page = await screen.findByRole("heading", { level: 2, name: "Not available on nas-casa" });
    const main = page.closest("main") ?? document.body;
    expect(within(main).getByText("The Docker zone: this central does not use its machine's Docker until it is told to")).toBeInTheDocument();
    expect(within(main).getByText(/NOUST_DEPLOY_HERE=containers/)).toBeInTheDocument();
    // Not a "try again" that could never work, and not the table of Docker that was never read.
    expect(within(main).queryByRole("button", { name: /try again/i })).not.toBeInTheDocument();
    expect(within(main).getByRole("link", { name: "Deploy on this machine" })).toHaveAttribute("href", "/server/deploy-here");
    await expectNoAxeViolations(container);
  });

  it("opens the zone as it always did on a hub that was told to", async () => {
    screenWidth(1440);
    fakeBackend(signedInRoutes(nasSession()));
    renderConsole("/server/docker/stacks");
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByText(/does not use its machine's Docker/)).not.toBeInTheDocument();
  });
});
