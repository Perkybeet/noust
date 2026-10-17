import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json, problem } from "../../../test/fakes";
import { IN_SYNC, PLACEMENT, SHOP, STATUS_MISSING, placementRoutes } from "./testFixtures";

/** The page at a phone's width (jsdom matches no media query): every table is a list of cards. */
async function serverRow(text: string): Promise<HTMLElement> {
  const list = await screen.findByRole("list", { name: `${SHOP} on each of its servers` });
  return waitFor(() => {
    const row = within(list)
      .queryAllByRole("listitem")
      .find((candidate) => within(candidate).queryAllByText(text).length > 0);
    if (row === undefined) throw new Error(`no row for ${text}`);
    return row;
  });
}

async function openMenu(user: ReturnType<typeof renderConsole>["user"]): Promise<void> {
  await user.click(await screen.findByRole("button", { name: `Actions for ${SHOP}` }));
}

describe("Fleet > On several servers", () => {
  it("compares an application across its servers and says which differs", { timeout: 30_000 }, async () => {
    fakeBackend(placementRoutes());
    const { container } = renderConsole("/fleet/placements");

    expect(await screen.findByRole("heading", { level: 3, name: SHOP })).toBeInTheDocument();
    expect(await screen.findByText("Not in sync")).toBeInTheDocument();
    expect(screen.getByText("On 2 servers")).toBeInTheDocument();
    expect(screen.getByText("Most servers run abc1234")).toBeInTheDocument();
    const web2 = await serverRow("web-2");
    expect(within(web2).getByText("Same commit")).toBeInTheDocument();
    expect(within(web2).getByText("abc1234")).toBeInTheDocument();
    expect(within(web2).getByRole("link", { name: "web-2" })).toHaveAttribute("href", `/n/web-2/apps/${SHOP}`);
    const web3 = await serverRow("web-3");
    expect(within(web3).getByText("Another commit")).toBeInTheDocument();
    expect(within(web3).getByText("def5678")).toBeInTheDocument();
    await expectNoAxeViolations(container, { page: true });
  });

  it("says in sync, with the commit, when every server agrees", { timeout: 30_000 }, async () => {
    fakeBackend(placementRoutes(IN_SYNC));
    renderConsole("/fleet/placements");

    expect(await screen.findByText("In sync on abc1234")).toBeInTheDocument();
  });

  it("says a server that lacks the application is not installed and what to do", { timeout: 30_000 }, async () => {
    fakeBackend(placementRoutes(STATUS_MISSING));
    renderConsole("/fleet/placements");

    const web3 = await serverRow("web-3");
    expect(within(web3).getAllByText("Not installed").length).toBeGreaterThan(0);
    expect(web3).toHaveTextContent("noust fleet placement copy shop.example.com");
  });

  it("explains itself when nothing is placed yet, with the command", { timeout: 30_000 }, async () => {
    fakeBackend({ ...placementRoutes(), "GET /api/fleet/placements": () => json(200, { placements: [] }) });
    renderConsole("/fleet/placements");

    expect(await screen.findByRole("heading", { level: 3, name: "No application is placed on several servers yet" })).toBeInTheDocument();
    expect(screen.getByText("noust fleet placement add shop.example.com --nodes web-1,web-2")).toBeInTheDocument();
  });

  it("shows a server it could not compare as an error with a retry, never a blank card", { timeout: 30_000 }, async () => {
    fakeBackend({ ...placementRoutes(), [`GET /api/fleet/placements/${SHOP}`]: () => problem(502, "node_unreachable", "web-2 did not answer") });
    renderConsole("/fleet/placements");

    expect(await screen.findByText("Could not compare shop.example.com across its servers")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Try again|Retry/ })).toBeInTheDocument();
  });

  it("records a new application with the servers chosen and the way an update goes out", { timeout: 30_000 }, async () => {
    const backend = fakeBackend({ ...placementRoutes(), "GET /api/fleet/placements": () => json(200, { placements: [] }) });
    backend.on("PUT /api/fleet/placements/blog.example.com", () => json(200, { ...PLACEMENT, domain: "blog.example.com" }));
    const { user } = renderConsole("/fleet/placements");

    await user.click(await screen.findByRole("button", { name: "Place an application" }));
    const dialog = await screen.findByRole("dialog", { name: "Place an application on several servers" });
    await user.type(within(dialog).getByLabelText("Application"), "Blog.Example.com");
    await user.click(within(dialog).getByRole("checkbox", { name: "web-2" }));
    await user.type(within(dialog).getByLabelText(/Servers at a time/), "50%");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo("PUT /api/fleet/placements/blog.example.com")).toHaveLength(1);
    });
    expect(backend.callsTo("PUT /api/fleet/placements/blog.example.com")[0]?.body).toEqual({
      nodes: ["web-2"],
      labels: {},
      serial: "50%",
      max_failures: null,
      canary: null,
      rollback_on_failure: true,
    });
  });

  it("refuses a domain that is not one, and a placement that names nothing, before asking", { timeout: 30_000 }, async () => {
    const backend = fakeBackend({ ...placementRoutes(), "GET /api/fleet/placements": () => json(200, { placements: [] }) });
    const { user } = renderConsole("/fleet/placements");

    await user.click(await screen.findByRole("button", { name: "Place an application" }));
    const dialog = await screen.findByRole("dialog", { name: "Place an application on several servers" });
    await user.type(within(dialog).getByLabelText("Application"), "https://x.example.com");
    await user.click(within(dialog).getByRole("button", { name: "Save" }));

    expect(await within(dialog).findByText("Write the bare domain, such as shop.example.com.")).toBeInTheDocument();
    expect(within(dialog).getByText("Choose servers, give a label, or both.")).toBeInTheDocument();
    expect(backend.calls.filter((call) => call.method === "PUT")).toHaveLength(0);
  });

  it("plans a rollout, shows who goes first, and runs it into the job", { timeout: 40_000 }, async () => {
    const backend = fakeBackend(placementRoutes());
    const { user, location } = renderConsole("/fleet/placements");

    await openMenu(user);
    await user.click(await screen.findByRole("menuitem", { name: "Roll out" }));
    const dialog = await screen.findByRole("dialog", { name: `Roll out ${SHOP}` });
    await user.click(within(dialog).getByRole("button", { name: "Show the plan" }));

    expect(await within(dialog).findByText(/web-2 goes first and alone/)).toBeInTheDocument();
    expect(backend.callsTo(`POST /api/fleet/placements/${SHOP}/rollout`)[0]?.body).toMatchObject({ plan: true, force: false, rollback_on_failure: true });

    await user.click(within(dialog).getByRole("button", { name: "Roll out on 2 servers" }));
    await waitFor(() => {
      expect(location().pathname).toBe("/fleet/jobs/fj-9");
    });
    const calls = backend.callsTo(`POST /api/fleet/placements/${SHOP}/rollout`);
    expect(calls).toHaveLength(2);
    expect(calls[1]?.body).toMatchObject({ plan: false });
  });

  it("asks for a commit that is a commit, and sends it", { timeout: 40_000 }, async () => {
    const backend = fakeBackend(placementRoutes());
    const { user } = renderConsole("/fleet/placements");

    await openMenu(user);
    await user.click(await screen.findByRole("menuitem", { name: "Roll out" }));
    const dialog = await screen.findByRole("dialog", { name: `Roll out ${SHOP}` });
    await user.click(within(dialog).getByRole("radio", { name: /A commit/ }));
    await user.click(within(dialog).getByRole("button", { name: "Show the plan" }));
    expect(await within(dialog).findByText("Write 7 to 40 hexadecimal characters.")).toBeInTheDocument();
    expect(backend.callsTo(`POST /api/fleet/placements/${SHOP}/rollout`)).toHaveLength(0);

    await user.type(within(dialog).getByLabelText("Commit"), "ABC1234");
    await user.click(within(dialog).getByRole("button", { name: "Show the plan" }));

    await waitFor(() => {
      expect(backend.callsTo(`POST /api/fleet/placements/${SHOP}/rollout`)).toHaveLength(1);
    });
    expect(backend.callsTo(`POST /api/fleet/placements/${SHOP}/rollout`)[0]?.body).toMatchObject({ commit: "abc1234" });
  });

  it("sends an image tag for an application that runs a registry image", { timeout: 40_000 }, async () => {
    const backend = fakeBackend(placementRoutes());
    const { user } = renderConsole("/fleet/placements");

    await openMenu(user);
    await user.click(await screen.findByRole("menuitem", { name: "Roll out" }));
    const dialog = await screen.findByRole("dialog", { name: `Roll out ${SHOP}` });
    await user.click(within(dialog).getByRole("radio", { name: /An image tag/ }));
    await user.type(within(dialog).getByLabelText("Image tag"), "1.4.2");
    await user.click(within(dialog).getByRole("button", { name: "Show the plan" }));

    await waitFor(() => {
      expect(backend.callsTo(`POST /api/fleet/placements/${SHOP}/rollout`)).toHaveLength(1);
    });
    expect(backend.callsTo(`POST /api/fleet/placements/${SHOP}/rollout`)[0]?.body).toMatchObject({ image_tag: "1.4.2" });
  });

  it("copies the application to the server that lacks it", { timeout: 40_000 }, async () => {
    const backend = fakeBackend(placementRoutes(STATUS_MISSING));
    const { user, location } = renderConsole("/fleet/placements");

    await openMenu(user);
    await user.click(await screen.findByRole("menuitem", { name: "Put it on a server that lacks it" }));
    const dialog = await screen.findByRole("dialog", { name: `Put ${SHOP} on a server that lacks it` });
    expect(within(dialog).getByRole("checkbox", { name: "web-3" })).toBeChecked();
    await user.click(within(dialog).getByRole("button", { name: "Show the plan" }));
    await within(dialog).findByRole("button", { name: "Copy to 1 server" });
    expect(backend.callsTo(`POST /api/fleet/placements/${SHOP}/copy`)[0]?.body).toEqual({
      from_node: "web-2",
      to: ["web-3"],
      new_domain: null,
      secrets: true,
      match_commit: true,
      plan: true,
    });

    await user.click(within(dialog).getByRole("button", { name: "Copy to 1 server" }));
    await waitFor(() => {
      expect(location().pathname).toBe("/fleet/jobs/fj-10");
    });
  });

  it("says there is nothing to copy when every server has the application", { timeout: 40_000 }, async () => {
    fakeBackend(placementRoutes(IN_SYNC));
    const { user } = renderConsole("/fleet/placements");

    await openMenu(user);
    await user.click(await screen.findByRole("menuitem", { name: "Put it on a server that lacks it" }));
    const dialog = await screen.findByRole("dialog", { name: `Put ${SHOP} on a server that lacks it` });

    expect(within(dialog).getAllByText(`Every server of ${SHOP} already has it.`).length).toBeGreaterThan(0);
  });

  it("writes the servers for the load balancer and changes the format", { timeout: 40_000 }, async () => {
    const backend = fakeBackend(placementRoutes());
    const { user } = renderConsole("/fleet/placements");

    await openMenu(user);
    await user.click(await screen.findByRole("menuitem", { name: "Servers for the load balancer" }));
    const dialog = await screen.findByRole("dialog", { name: `Servers of ${SHOP} for the load balancer` });

    expect(await within(dialog).findByText(/upstream shop_example_com \{/)).toBeInTheDocument();
    expect(within(dialog).getByText("on def5678, not abc1234")).toBeInTheDocument();
    expect(backend.callsTo(`GET /api/fleet/placements/${SHOP}/upstreams`)[0]?.search.get("format")).toBe("nginx");

    await user.clear(within(dialog).getByLabelText("Their web port"));
    await user.type(within(dialog).getByLabelText("Their web port"), "99999");
    expect(await within(dialog).findByText("Write a port from 1 to 65535.")).toBeInTheDocument();
  });

  it("stops tracking an application after a question, and touches no server", { timeout: 40_000 }, async () => {
    const backend = fakeBackend(placementRoutes());
    const { user } = renderConsole("/fleet/placements");

    await openMenu(user);
    await user.click(await screen.findByRole("menuitem", { name: "Stop tracking it here" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getByText(/Nothing is changed on any server/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Stop tracking" }));

    await waitFor(() => {
      expect(backend.callsTo(`DELETE /api/fleet/placements/${SHOP}`)).toHaveLength(1);
    });
  });

  it("is in Spanish too", { timeout: 40_000 }, async () => {
    fakeBackend(placementRoutes());
    renderConsole("/fleet/placements");
    await act(async () => {
      await setLocale("es");
    });

    expect(await screen.findByRole("heading", { level: 2, name: "Aplicaciones en varios servidores" })).toBeInTheDocument();
    expect(await screen.findByText("No está sincronizada")).toBeInTheDocument();
    await act(async () => {
      await setLocale("en");
    });
  });
});
