import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { api } from "../api/client";
import { setLocale } from "../app/locale";
import { expectNoAxeViolations } from "../test/axe";
import { renderConsole } from "../test/console";
import { APPS, FakeEventSource, MACHINE, NODES, fakeBackend, json, onNode, problem, signedInRoutes } from "../test/fakes";
import type { RouteHandler } from "../test/fakes";

/** What web-2 runs: other applications than this server, so a page shows whose data it is. */
const WEB2_APPS = [{ ...APPS[0], domain: "api.web2.example", name: "api" }];

function fleet(nodes: unknown[] = NODES, extra: Record<string, RouteHandler> = {}) {
  return fakeBackend({
    ...signedInRoutes(),
    ...onNode("web-2", signedInRoutes()),
    ...onNode("db-1", signedInRoutes()),
    "GET /api/nodes/web-2/api/apps": () => json(200, { total: WEB2_APPS.length, apps: WEB2_APPS }),
    "GET /api/nodes/web-2/api/system/machine": () => json(200, { ...MACHINE, hostname: "web-2.internal" }),
    "GET /api/nodes": () => json(200, { items: nodes }),
    ...extra,
  });
}

async function consoleAt(path: string, backend = fleet()) {
  const harness = renderConsole(path);
  await screen.findByRole("heading", { level: 1 });
  return { ...harness, backend };
}

function trigger(name: RegExp | string) {
  return screen.findByRole("button", { name });
}

describe("a page on a node", () => {
  it("reads everything from the node through the central, and its own session from the central", async () => {
    const { backend } = await consoleAt("/n/web-2/apps");
    expect(await screen.findByRole("link", { name: "api.web2.example" })).toBeInTheDocument();
    expect(screen.queryByText("shop.example.com")).toBeNull();
    expect(backend.callsTo("GET /api/nodes/web-2/api/apps").length).toBeGreaterThan(0);
    expect(backend.callsTo("GET /api/apps")).toHaveLength(0);
    // The operator's credentials are the central's: never forwarded.
    expect(backend.callsTo("GET /api/auth/session").length).toBeGreaterThan(0);
    expect(backend.calls.some((call) => call.path.startsWith("/api/nodes/web-2/api/auth"))).toBe(false);
  });

  it("keeps every link on the node, and the central's own pages off it", async () => {
    const { history, location } = await consoleAt("/n/web-2/apps");
    const app = await screen.findByRole("link", { name: "api.web2.example" });
    expect(app).toHaveAttribute("href", "/n/web-2/apps/api.web2.example");
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(within(nav).getByRole("link", { name: "Databases" })).toHaveAttribute("href", "/n/web-2/databases");
    expect(within(nav).getByRole("link", { name: "Overview" })).toHaveAttribute("href", "/n/web-2");
    expect(screen.getAllByRole("link", { name: "Settings" })[0]).toHaveAttribute("href", "/settings");

    fireEvent.click(within(nav).getByRole("link", { name: /^Services/ }));
    await screen.findByRole("heading", { level: 1, name: "Services" });
    expect(history.location.pathname).toBe("/n/web-2/services");
    expect(location().pathname).toBe("/services");
    expect(location().search).toMatchObject({ node: "web-2" });
  });

  it("opens a shared link to a node's application tab, titled with the node", async () => {
    fleet(NODES, {
      "GET /api/nodes/web-2/api/apps/api.web2.example": () => json(200, WEB2_APPS[0]),
    });
    renderConsole("/n/web-2/apps/api.web2.example/logs");
    expect(await screen.findByRole("heading", { level: 1, name: "api.web2.example" })).toBeInTheDocument();
    await waitFor(() => {
      expect(document.title).toBe("Logs - api.web2.example - web-2 - Noust");
    });
  });

  it("listens to the node's event stream, and writes what it says into the node's entries", async () => {
    await consoleAt("/n/web-2/apps");
    const source = FakeEventSource.latest();
    expect(source.url).toBe("/api/nodes/web-2/events");
    const strip = screen.getByRole("group", { name: "This machine" });
    await within(strip).findByText("web-2.internal");
    act(() => {
      source.open();
      source.emit("machine", { ...MACHINE, hostname: "web-2.renamed" });
    });
    expect(await within(strip).findByText("web-2.renamed")).toBeInTheDocument();
  });

  it("listens to this server's own stream on this server", async () => {
    await consoleAt("/apps");
    expect(FakeEventSource.latest().url).toBe("/events");
  });

  it("confirms it's you with the central, then retries the node's action", async () => {
    let elevated = false;
    const backend = fleet(NODES, {
      "POST /api/auth/elevate": () => {
        elevated = true;
        return json(200, { elevated_until: "2026-09-25T18:10:00+00:00" });
      },
      "DELETE /api/nodes/web-2/api/apps/api.web2.example": () =>
        elevated ? json(202, { job_id: "j1", status: "pending" }) : problem(403, "elevation_required", "Confirm it's you to continue"),
    });
    const { user } = await consoleAt("/n/web-2/apps", backend);
    const deletion = api("DELETE", "/api/apps/api.web2.example");
    const dialog = await screen.findByRole("dialog", { name: "Confirm it's you" });
    await user.type(within(dialog).getByLabelText("Authentication code"), "123456");
    await user.click(within(dialog).getByRole("button", { name: "Confirm" }));
    await expect(deletion).resolves.toMatchObject({ job_id: "j1" });
    expect(backend.callsTo("POST /api/auth/elevate")).toHaveLength(1);
    expect(backend.calls.some((call) => call.path === "/api/nodes/web-2/api/auth/elevate")).toBe(false);
    expect(backend.callsTo("DELETE /api/nodes/web-2/api/apps/api.web2.example")).toHaveLength(2);
  });
});

describe("a node the central cannot use", () => {
  const unreachable = () =>
    problem(502, "node_unreachable", "Node web-2 did not answer through its tunnel.", {
      hint: "Check the node from Settings, or run `noust node test web-2`.",
      output: "ConnectError: [Errno 111] Connection refused",
    });

  it("says which node, with the central's hint and the tunnel's own words verbatim", async () => {
    await consoleAt("/n/web-2/apps", fleet(NODES, { "GET /api/nodes/web-2/api/apps": unreachable }));
    expect(await screen.findByText("web-2 is not answering")).toBeInTheDocument();
    expect(screen.getByText("Check the node from Settings, or run `noust node test web-2`.")).toBeInTheDocument();
    expect(screen.getByText("Node web-2 did not answer through its tunnel.")).toBeInTheDocument();
    expect(screen.getByText("ConnectError: [Errno 111] Connection refused")).toBeInTheDocument();
    expect(screen.queryByText("Could not load applications")).toBeNull();
  });

  it("says a refusal as one, in Spanish too", async () => {
    await act(() => setLocale("es"));
    await consoleAt(
      "/n/web-2/apps",
      fleet(NODES, {
        "GET /api/nodes/web-2/api/apps": () =>
          problem(502, "node_refused", "Node web-2 refused this central's fleet token.", { output: '{"error":"unauthorized"}' }),
      }),
    );
    expect(await screen.findByText("web-2 rechazó a este servidor")).toBeInTheDocument();
    // No hint from the central: the console's own sentence, and the node's answer verbatim.
    expect(screen.getByText("web-2 ya no acepta el token de flota de este servidor.")).toBeInTheDocument();
    expect(screen.getByText("Node web-2 refused this central's fleet token.")).toBeInTheDocument();
    expect(screen.getByText('{"error":"unauthorized"}')).toBeInTheDocument();
  });
});

describe("the server selector", () => {
  it("is not there on a server with no nodes", async () => {
    await consoleAt("/apps", fleet([]));
    await screen.findByRole("link", { name: "shop.example.com" });
    expect(screen.queryByRole("button", { name: /^Server:/ })).toBeNull();
  });

  it("names the server on screen, lists every server with its state and version, and has no violations", async () => {
    const { user } = await consoleAt("/apps");
    const button = await trigger("Server: web-01");
    expect(button).toHaveTextContent("web-01");
    await user.click(button);
    const menu = await screen.findByRole("menu", { name: "Server: web-01" });
    const items = within(menu).getAllByRole("menuitemradio");
    expect(items.map((item) => item.textContent)).toEqual([
      "web-01 This server · Noust 2.0.0",
      "web-2 Reachable · Noust 2.0.0",
      "db-1 Unreachable · Noust 1.9.0",
    ]);
    expect(items[0]).toHaveAttribute("aria-checked", "true");
    expect(items[1]).toHaveAttribute("aria-checked", "false");
    await expectNoAxeViolations(menu);
    await user.keyboard("{Escape}");
    await waitFor(() => {
      expect(screen.queryByRole("menu")).toBeNull();
    });
    await expectNoAxeViolations(document.body, { page: true });
  });

  it("switches to a node on the same page, says so, and moves on with its data", async () => {
    const { user, history, backend } = await consoleAt("/apps");
    await screen.findByRole("link", { name: "shop.example.com" });
    await user.click(await trigger("Server: web-01"));
    await user.click(await screen.findByRole("menuitemradio", { name: /^web-2/ }));
    await waitFor(() => {
      expect(history.location.pathname).toBe("/n/web-2/apps");
    });
    expect(await screen.findByRole("link", { name: "api.web2.example" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "shop.example.com" })).toBeNull();
    expect(await trigger("Server: web-2")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByTestId("announcer-polite")).toHaveTextContent("Now on web-2");
    });
    expect(backend.callsTo("GET /api/nodes/web-2/api/apps").length).toBeGreaterThan(0);
  });

  it("goes to the overview when the page is about something the other server may not have", async () => {
    fleet(NODES, { "GET /api/nodes/web-2/api/apps/api.web2.example": () => json(200, WEB2_APPS[0]) });
    const { user, history } = renderConsole("/n/web-2/apps/api.web2.example/logs");
    await screen.findByRole("heading", { level: 1, name: "api.web2.example" });
    await user.click(await trigger("Server: web-2"));
    await user.click(await screen.findByRole("menuitemradio", { name: /^web-01/ }));
    await waitFor(() => {
      expect(history.location.pathname).toBe("/");
    });
    expect(await trigger("Server: web-01")).toBeInTheDocument();
  });

  it("works from the keyboard", async () => {
    const { user, history } = await consoleAt("/services");
    const button = await trigger("Server: web-01");
    button.focus();
    await user.keyboard("{Enter}");
    const menu = await screen.findByRole("menu", { name: "Server: web-01" });
    await waitFor(() => {
      expect(within(menu).getAllByRole("menuitemradio")[0]).toHaveFocus();
    });
    await user.keyboard("{ArrowDown}{Enter}");
    await waitFor(() => {
      expect(history.location.pathname).toBe("/n/web-2/services");
    });
    // Focus goes to the new page, not to a control that was replaced.
    await waitFor(() => {
      expect(screen.getByRole("heading", { level: 1, name: "Services" })).toHaveFocus();
    });
  });

  it("says a node that does not answer on the button itself", async () => {
    await consoleAt("/n/db-1/apps");
    const button = await trigger("Server: db-1, Unreachable");
    expect(button).toHaveTextContent("db-1Unreachable");
  });

  it("speaks Spanish", async () => {
    await act(() => setLocale("es"));
    const { user } = await consoleAt("/apps");
    await user.click(await trigger("Servidor: web-01"));
    const menu = await screen.findByRole("menu", { name: "Servidor: web-01" });
    expect(within(menu).getAllByRole("menuitemradio").map((item) => item.textContent)).toEqual([
      "web-01 Este servidor · Noust 2.0.0",
      "web-2 Accesible · Noust 2.0.0",
      "db-1 Inaccesible · Noust 1.9.0",
    ]);
    await expectNoAxeViolations(menu);
  });

  it("is in the command palette", async () => {
    const { user, history } = await consoleAt("/apps");
    await trigger("Server: web-01");
    await user.keyboard("{Control>}k{/Control}");
    const input = await screen.findByRole("combobox");
    await user.type(input, "switch to server web");
    await user.click(await screen.findByRole("option", { name: "Switch to server web-2" }));
    await waitFor(() => {
      expect(history.location.pathname).toBe("/n/web-2/apps");
    });
  });
});

describe("the shell on a node", () => {
  it("says when the node runs another Noust than this server", async () => {
    await consoleAt("/n/db-1/apps");
    const notice = await screen.findByRole("region", { name: "About db-1" });
    expect(notice).toHaveTextContent("db-1 runs Noust 1.9.0, older than this server's 2.0.0.");
  });

  it("says nothing on a node on the same version", async () => {
    await consoleAt("/n/web-2/apps");
    await screen.findByRole("link", { name: "api.web2.example" });
    expect(screen.queryByRole("region", { name: "About web-2" })).toBeNull();
  });

  it("says when there is no such node, with the way back", async () => {
    await consoleAt("/n/nope/apps");
    expect(await screen.findByText("This server has no node named nope.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to this server" })).toHaveAttribute("href", "/");
  });
});
