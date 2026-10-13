/**
 * The Logs tab of a Docker Compose stack (checklist M-39). A stack's unit only starts it and says
 * nothing of the application, so the tab reads what the containers printed, all services together or
 * one service at a time, and still has the systemd unit's journal when that is what is wanted.
 */

import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { FakeWebSocket, fakeBackend, json, problem } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { TAB_DOMAIN, appRoutes } from "../testRoutes";

const OUTPUT = [
  "web-1  | GET / 200 in 38ms",
  "db-1  | database system is ready to accept connections",
  "web-1  | Error: connect ECONNREFUSED 127.0.0.1:6379",
].join("\n");

const LOGS = `GET /api/apps/${TAB_DOMAIN}/logs`;

function stackLogs(call: { search: URLSearchParams }) {
  const service = call.search.get("service");
  const logs = service === null ? OUTPUT : OUTPUT.split("\n").filter((line) => line.startsWith(`${service}-`)).join("\n");
  return json(200, { domain: TAB_DOMAIN, logs, lines: 500, service, services: ["web", "db"] });
}

async function stackAt(app: Record<string, unknown> = {}, extra: Record<string, RouteHandler> = {}) {
  vi.stubGlobal("WebSocket", FakeWebSocket);
  // The virtualizer sizes its window from offsetHeight, which jsdom reports as 0.
  vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(400);
  vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockReturnValue(800);
  const backend = fakeBackend(
    appRoutes({ app_type: "docker-compose", ...app }, { "POST /api/auth/ws-ticket": () => json(200, { ticket: "t1" }), [LOGS]: stackLogs, ...extra }),
  );
  const harness = renderConsole(`/apps/${TAB_DOMAIN}/logs`);
  await screen.findByRole("heading", { level: 1, name: TAB_DOMAIN });
  return { ...harness, backend };
}

/** The first line of the output: the console and its lazy chunks are slow to arrive on a loaded machine. */
const printed = () => screen.findByText(/GET \/ 200 in 38ms/, {}, { timeout: 5000 });

async function chooseOutput(user: ReturnType<typeof renderConsole>["user"], name: string) {
  await user.click(await screen.findByRole("combobox", { name: "Output of" }));
  await user.click(await screen.findByRole("option", { name }));
}

describe("the logs of a Compose stack", () => {
  it("are what the containers printed, all services together, and open no journal stream", async () => {
    const { backend } = await stackAt();

    const output = await screen.findByRole("region", { name: `Logs of ${TAB_DOMAIN}` });

    expect(await printed()).toBeInTheDocument();
    expect(within(output).getByText(/database system is ready/)).toBeInTheDocument();
    expect(within(output).getByText(/ECONNREFUSED/).closest("[data-index]")).toHaveClass("bg-fail-soft");
    expect(FakeWebSocket.instances).toHaveLength(0);
    const asked = backend.callsTo(LOGS)[0];
    expect(asked?.search.get("lines")).toBe("500");
    expect(asked?.search.has("service")).toBe(false);
    expect(screen.getByRole("combobox", { name: "Output of" })).toHaveTextContent("All services");
  });

  it("are read for a container application the same way", async () => {
    await stackAt({ app_type: "container" });

    expect(await printed()).toBeInTheDocument();
    expect(FakeWebSocket.instances).toHaveLength(0);
  });

  it("offer each service, and read the one chosen alone", async () => {
    const { user, backend } = await stackAt();
    await printed();

    await user.click(await screen.findByRole("combobox", { name: "Output of" }));
    const options = (await screen.findAllByRole("option")).map((option) => option.textContent);
    expect(options).toEqual(["All services", "web", "db", "Systemd unit"]);
    await user.click(screen.getByRole("option", { name: "db" }));

    const output = screen.getByRole("region", { name: `Logs of ${TAB_DOMAIN}` });
    expect(await within(output).findByText(/database system is ready/)).toBeInTheDocument();
    await waitFor(() => expect(within(output).queryByText(/GET \/ 200 in 38ms/)).not.toBeInTheDocument());
    expect(backend.callsTo(LOGS).some((call) => call.search.get("service") === "db")).toBe(true);
    expect(FakeWebSocket.instances).toHaveLength(0);
  });

  it("read again when asked, so a fresh error is not a page away", async () => {
    const { user, backend } = await stackAt();
    await printed();
    const before = backend.callsTo(LOGS).length;

    await user.click(screen.getByRole("button", { name: "Refresh" }));

    await waitFor(() => expect(backend.callsTo(LOGS).length).toBeGreaterThan(before));
  });

  it("still have the unit's journal, as a stream, for what Noust ran", async () => {
    const { user } = await stackAt();
    await printed();

    await chooseOutput(user, "Systemd unit");

    await waitFor(() => {
      expect(FakeWebSocket.instances.some((socket) => socket.url.includes(`/ws/logs/${TAB_DOMAIN}`))).toBe(true);
    });
    expect(screen.getByText("Connecting")).toBeInTheDocument();
    expect(screen.queryByText(/GET \/ 200 in 38ms/)).not.toBeInTheDocument();
  });

  it("show the server's own words when the containers cannot be read", async () => {
    await stackAt({}, { [LOGS]: () => problem(502, "bad_gateway", "Compose did not list the containers: Cannot connect to the Docker daemon") });

    expect(await screen.findByText(/Cannot connect to the Docker daemon/)).toBeInTheDocument();
    expect(screen.getByText("The containers' output could not be read")).toBeInTheDocument();
  });

  it("say so when the containers have printed nothing", async () => {
    await stackAt({}, { [LOGS]: () => json(200, { domain: TAB_DOMAIN, logs: "No logs available", lines: 500, service: null, services: ["web"] }) });

    expect(await screen.findByText("The containers have printed nothing yet.")).toBeInTheDocument();
  });

  it("keep the level chips of the viewer, so a stack's errors are one click away", async () => {
    const { user } = await stackAt();
    const output = await screen.findByRole("region", { name: `Logs of ${TAB_DOMAIN}` });
    await printed();

    await user.click(screen.getByRole("button", { name: "Error 1" }));

    expect(within(output).getByText(/ECONNREFUSED/)).toBeInTheDocument();
    expect(within(output).queryByText(/GET \/ 200 in 38ms/)).not.toBeInTheDocument();
  });

  it("are not a choice for an application that is not a stack", async () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    fakeBackend(appRoutes({}, { "POST /api/auth/ws-ticket": () => json(200, { ticket: "t1" }) }));
    renderConsole(`/apps/${TAB_DOMAIN}/logs`);
    await screen.findByRole("heading", { level: 1, name: TAB_DOMAIN });

    await waitFor(() => expect(FakeWebSocket.instances.length).toBeGreaterThan(0));
    expect(screen.queryByRole("combobox", { name: "Output of" })).not.toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    await stackAt();
    await printed();

    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("speaks Spanish", async () => {
    await act(() => setLocale("es"));
    await stackAt();

    expect(await screen.findByRole("combobox", { name: "Salida de" })).toHaveTextContent("Todos los servicios");
    expect(screen.getByRole("button", { name: "Recargar" })).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});
