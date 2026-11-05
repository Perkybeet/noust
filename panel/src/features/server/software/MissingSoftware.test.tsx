import { screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { SESSION, fakeBackend, json, problem, signedInRoutes } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { onDesktop, serverRoutes } from "../testing";
import { ITEMS, JOB_ACCEPTED, SOFTWARE, softwareJob, softwareRoutes } from "./testFixtures";

beforeEach(onDesktop);

/** What the Ports page says when nginx lacks the stream module, which Noust can install. */
const LACKING = {
  available: false,
  module: "missing",
  loader: "manual",
  problem: "This nginx was built without the stream module",
  hint: "Install the module with: apt install libnginx-mod-stream",
  install: "nginx_stream_module",
  coded: { code: "streams.support.no_stream_module", params: { command: "apt install libnginx-mod-stream" } },
};

const READY = { available: true, module: "dynamic", loader: "installable", problem: null, hint: null };

/** The Ports page, the first of the pages that say what is missing through "X is missing". */
function ports(extra: Record<string, RouteHandler> = {}, options: { session?: typeof SESSION; software?: Parameters<typeof softwareRoutes>[0]; support?: unknown } = {}) {
  const backend = fakeBackend({
    ...signedInRoutes(options.session ?? SESSION),
    ...serverRoutes(),
    ...softwareRoutes(options.software),
    "GET /api/streams": () => json(200, { exposures: [], support: options.support ?? LACKING }),
    ...extra,
  });
  return { ...renderConsole("/network"), backend };
}

describe("X is missing, on the Ports page", () => {
  it("says which piece is missing, what the page loses without it, and offers to install it", async () => {
    ports();
    const notice = await screen.findByText("nginx stream module is missing");
    const block = notice.closest("[data-tone]");
    if (!(block instanceof HTMLElement)) throw new Error("no notice");
    expect(within(block).getByText(/Without it nginx cannot publish a TCP or UDP port that is not web\./)).toBeInTheDocument();
    expect(within(block).getByRole("link", { name: "See it in Software" })).toHaveAttribute("href", expect.stringContaining("/server/software"));
    expect(within(block).getByRole("button", { name: "Install" })).toBeInTheDocument();
    // The one install button of the page is the notice's: "Open a port" waits for what is missing.
    expect(screen.getByRole("button", { name: "Publish a port" })).toBeDisabled();
  });

  it("installs it as a task, says so while it runs with the way to the task, and reads the page again when it ends", async () => {
    let state: "running" | "completed" = "running";
    let published = false;
    const { user, backend } = ports({
      "POST /api/server/software/nginx_stream_module/install": () => json(202, JOB_ACCEPTED),
      "GET /api/jobs/5e1f0c2a": () => json(200, softwareJob(state)),
      "GET /api/streams": () => json(200, { exposures: [], support: published ? READY : LACKING }),
    });
    await user.click(await screen.findByRole("button", { name: "Install" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/server/software/nginx_stream_module/install")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/server/software/nginx_stream_module/install")[0]?.body).toEqual({ epel: false });
    expect(await screen.findByText("Installing nginx stream module")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "See the task" })).toHaveAttribute("href", expect.stringContaining("/activity/tasks/5e1f0c2a"));

    // It ends: the registry says it is there and the page's own check says nginx can publish.
    state = "completed";
    published = true;
    backend.on("GET /api/server/software", () => json(200, { ...SOFTWARE, items: ITEMS.map((entry) => (entry.id === "nginx_stream_module" ? { ...entry, installed: true, version: "1.24.0" } : entry)) }));
    await waitFor(() => {
      expect(screen.queryByText("nginx stream module is missing")).toBeNull();
      expect(screen.queryByText("Installing nginx stream module")).toBeNull();
    }, { timeout: 8000 });
  });

  it("says the install failed with the package manager's own words, and offers to try again", async () => {
    const { user } = ports({
      "POST /api/server/software/nginx_stream_module/install": () => json(202, JOB_ACCEPTED),
      "GET /api/jobs/5e1f0c2a": () => json(200, softwareJob("failed")),
    });
    await user.click(await screen.findByRole("button", { name: "Install" }));

    expect(await screen.findByText("nginx stream module was not installed")).toBeInTheDocument();
    expect(screen.getByText("E: Unable to locate package libnginx-mod-stream")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("says what the server refused, in its words, when it does not start the install", async () => {
    const { user } = ports({
      "POST /api/server/software/nginx_stream_module/install": () => problem(409, "host_busy", "Another package manager is running", { hint: "Wait for it to finish." }),
    });
    await user.click(await screen.findByRole("button", { name: "Install" }));
    expect(await screen.findByText("Could not install nginx stream module")).toBeInTheDocument();
    expect(screen.getByText("Another package manager is running")).toBeInTheDocument();
  });

  it("says who can install it, and offers no button, to an account that cannot", async () => {
    const reader = { ...SESSION, permissions: (SESSION.permissions ?? []).filter((permission) => permission !== "server.host_access") };
    ports({}, { session: reader });
    expect(await screen.findByText("nginx stream module is missing")).toBeInTheDocument();
    expect(screen.getByText(/Your account cannot install it: it takes the permissions/)).toBeInTheDocument();
    expect(screen.getByText("server.host_access")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Install" })).toBeNull();
  });

  it("says Noust does not install it on a system it does not, instead of a button that would be refused", async () => {
    ports({}, { software: { list: { ...SOFTWARE, items: ITEMS.map((entry) => (entry.id === "nginx_stream_module" ? { ...entry, installable: false } : entry)) } } });
    expect(await screen.findByText("nginx stream module is missing")).toBeInTheDocument();
    expect(screen.getByText("Noust does not install nginx stream module on this system: install it with the system's package manager.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Install" })).toBeNull();
  });

  it("leaves the page's own words when the registry says the piece is there", async () => {
    ports({}, { software: { list: { ...SOFTWARE, items: ITEMS.map((entry) => (entry.id === "nginx_stream_module" ? { ...entry, installed: true } : entry)) } } });
    // The server's explanation, which is what the page said before there was a registry.
    expect(await screen.findByText("This nginx was built without the stream module")).toBeInTheDocument();
    expect(screen.queryByText("nginx stream module is missing")).toBeNull();
  });

  it("leaves the page's own words when the server cannot say what is installed", async () => {
    ports({ "GET /api/server/software": () => problem(404, "not_found", "No such route") });
    expect(await screen.findByText("This nginx was built without the stream module")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Install" })).toBeNull();
  });

  it("has no accessibility violations", async () => {
    ports();
    await screen.findByText("nginx stream module is missing");
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});
