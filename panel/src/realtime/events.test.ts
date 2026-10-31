import { QueryClient } from "@tanstack/react-query";
import { act, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { configureApi } from "../api/client";
import { appKeys } from "../api/queries/apps";
import { certKeys } from "../api/queries/certs";
import { databaseKeys } from "../api/queries/databases";
import { deploymentKeys } from "../api/queries/deployments";
import { jobKeys } from "../api/queries/jobs";
import { queueKeys } from "../api/queries/queue";
import { serviceKeys } from "../api/queries/services";
import { siteKeys } from "../api/queries/sites";
import { systemKeys } from "../api/queries/system";
import { setLocale } from "../app/locale";
import { toast } from "../components/ui/toast";
import { CHROME, dropNamespaces, loadCatalog } from "../i18n/catalogs";
import { ANONYMOUS, FakeEventSource, MACHINE, fakeBackend, json } from "../test/fakes";
import { RECONNECT, reconnectDelay } from "./backoff";
import { EventStream, applyServerEvent, failureWords } from "./events";

describe("applyServerEvent", () => {
  it("patches the app's own entry and invalidates the list on an app event", () => {
    const client = new QueryClient();
    client.setQueryData(appKeys.list, { apps: [], total: 0 });
    client.setQueryData(appKeys.detail("shop.example.com"), { domain: "shop.example.com", status: "running", port: 3000 });
    client.setQueryData(appKeys.detail("api.example.com"), { domain: "api.example.com", status: "running" });

    applyServerEvent(client, "app", { domain: "shop.example.com", status: "failed" });

    expect(client.getQueryState(appKeys.list)?.isInvalidated).toBe(true);
    expect(client.getQueryData(appKeys.detail("shop.example.com"))).toEqual({
      domain: "shop.example.com",
      status: "failed",
      port: 3000,
    });
    // Other apps are untouched: one app's change does not refetch the fleet.
    expect(client.getQueryState(appKeys.detail("api.example.com"))?.isInvalidated).toBe(false);
  });

  it("writes the machine snapshot where the strip reads it", () => {
    const client = new QueryClient();
    applyServerEvent(client, "machine", MACHINE);
    expect(client.getQueryData(systemKeys.machine)).toEqual(MACHINE);
  });

  it("refetches job lists on a change of status, not on every progress tick", () => {
    const client = new QueryClient();
    client.setQueryData(jobKeys.list({}), { jobs: [] });
    const job = { id: "j1", status: "running", progress: 10, metadata: { domain: "shop.example.com" } };

    applyServerEvent(client, "job", job);
    expect(client.getQueryState(jobKeys.list({}))?.isInvalidated).toBe(true);

    client.setQueryData(jobKeys.list({}), { jobs: [] });
    applyServerEvent(client, "job", { ...job, progress: 40 });
    expect(client.getQueryData(jobKeys.detail("j1"))).toMatchObject({ progress: 40 });
    expect(client.getQueryState(jobKeys.list({}))?.isInvalidated).toBe(false);
  });

  it("reads an application's deployments again as its deploy moves from step to step, so one a push started appears on its own", () => {
    const client = new QueryClient();
    const history = deploymentKeys.list({ domain: "shop.example.com" });
    const fresh = (): void => {
      client.setQueryData(history, { items: [], total: 0, next_before_id: null });
    };
    const deploy = { id: "j1", status: "running", progress: 10, current_step: "Fetching source into a new release", metadata: { domain: "shop.example.com" } };
    fresh();
    applyServerEvent(client, "job", deploy);
    expect(client.getQueryState(history)?.isInvalidated).toBe(true);

    // The same step again (a log line, a tick of progress) reads nothing.
    fresh();
    applyServerEvent(client, "job", { ...deploy, progress: 30 });
    expect(client.getQueryState(history)?.isInvalidated).toBe(false);

    // The next step, with the status as it was: the row the deployer has written by now is read.
    applyServerEvent(client, "job", { ...deploy, current_step: "Building the release", progress: 40 });
    expect(client.getQueryState(history)?.isInvalidated).toBe(true);

    // A job that is not about an application has no history to refresh.
    applyServerEvent(client, "job", { id: "j2", status: "running", current_step: "Counting", metadata: {} });
    fresh();
    applyServerEvent(client, "job", { id: "j2", status: "running", current_step: "Counting more", metadata: {} });
    expect(client.getQueryState(history)?.isInvalidated).toBe(false);
  });

  it("reads an application's deployments again when the application changes state", () => {
    const client = new QueryClient();
    const history = deploymentKeys.list({ domain: "shop.example.com" });
    client.setQueryData(history, { items: [], total: 0, next_before_id: null });
    applyServerEvent(client, "app", { domain: "shop.example.com", status: "deploying" });
    expect(client.getQueryState(history)?.isInvalidated).toBe(true);
  });

  it("reads the queue again when a waiting job moves up the line or is asked to stop, not on every tick", () => {
    const client = new QueryClient();
    const waiting = { id: "q1", status: "pending", queue_position: 2, waiting_for: "the update of api.example.com", cancel_requested: false, metadata: { domain: "shop.example.com" } };
    const fresh = (): void => {
      client.setQueryData(queueKeys.all, { running: [], queued: [] });
      client.setQueryData(jobKeys.active, { jobs: [] });
    };
    const stale = (): boolean[] => [client.getQueryState(queueKeys.all)?.isInvalidated ?? false, client.getQueryState(jobKeys.active)?.isInvalidated ?? false];

    // The first thing heard of it: every job list is read, the queue among them.
    fresh();
    applyServerEvent(client, "job", waiting);
    expect(stale()).toEqual([true, true]);

    fresh();
    applyServerEvent(client, "job", { ...waiting, queue_position: 1 });
    expect(stale()).toEqual([true, true]);

    fresh();
    applyServerEvent(client, "job", { ...waiting, queue_position: 1, waiting_for: null });
    expect(stale()).toEqual([true, true]);

    fresh();
    applyServerEvent(client, "job", { ...waiting, queue_position: 1, waiting_for: null, cancel_requested: true });
    expect(stale()).toEqual([true, true]);

    // A line of its log changes nothing about where it stands.
    fresh();
    applyServerEvent(client, "job", { ...waiting, queue_position: 1, waiting_for: null, cancel_requested: true, progress: 40 });
    expect(stale()).toEqual([false, false]);
  });

  it("refreshes the app a finished job acted on", () => {
    const client = new QueryClient();
    client.setQueryData(appKeys.detail("shop.example.com"), { domain: "shop.example.com" });
    applyServerEvent(client, "job", { id: "j1", status: "completed", metadata: { domain: "shop.example.com" } });
    expect(client.getQueryState(appKeys.detail("shop.example.com"))?.isInvalidated).toBe(true);
  });

  it("marks the lists the command palette indexes stale when a job ends, because it created or removed something", () => {
    const client = new QueryClient();
    const lists = [databaseKeys.list(null), siteKeys.all, certKeys.all, serviceKeys.list(true)];
    for (const key of lists) client.setQueryData(key, { items: [] });

    // Not while it runs: nothing it did is on disk yet, and a refetch would show the old picture.
    applyServerEvent(client, "job", { id: "j1", status: "running", metadata: { domain: "shop.example.com" } });
    for (const key of lists) expect(client.getQueryState(key)?.isInvalidated, JSON.stringify(key)).toBe(false);

    for (const status of ["completed", "failed", "cancelled"]) {
      for (const key of lists) client.setQueryData(key, { items: [] });
      applyServerEvent(client, "job", { id: `j-${status}`, status, metadata: { domain: "shop.example.com" } });
      for (const key of lists) expect(client.getQueryState(key)?.isInvalidated, `${status} ${JSON.stringify(key)}`).toBe(true);
    }
  });

  it("shows a notice in the system's words, as an error when something failed", () => {
    const error = vi.spyOn(toast, "error");
    const success = vi.spyOn(toast, "success");
    applyServerEvent(new QueryClient(), "notice", { text: "npm ERR! missing script: build", state: "failed" });
    applyServerEvent(new QueryClient(), "notice", { text: "Deploy shop.example.com", state: "active" });
    expect(error).toHaveBeenCalledWith("npm ERR! missing script: build");
    expect(success).toHaveBeenCalledWith("Deploy shop.example.com");
  });

  it("says a notice that is one of the server's sentences in the console's language, and a tool's words as they came", async () => {
    const success = vi.spyOn(toast, "success");
    const error = vi.spyOn(toast, "error");
    const sentence = { text: "Apply the fleet's settings on 2 servers", state: "active", coded: { code: "fleet_job.settings_apply.several", params: { count: 2 } } } as const;

    applyServerEvent(new QueryClient(), "notice", sentence);
    expect(success).toHaveBeenLastCalledWith("Apply the fleet's settings on 2 servers");

    await act(() => setLocale("es"));
    applyServerEvent(new QueryClient(), "notice", sentence);
    expect(success).toHaveBeenLastCalledWith("Aplicar los ajustes de la flota en 2 servidores");
    // A code the console does not know is the English the server sent beside it.
    applyServerEvent(new QueryClient(), "notice", { text: "Something new on 2 servers", state: "active", coded: { code: "fleet_job.something_new.several", params: { count: 2 } } });
    expect(success).toHaveBeenLastCalledWith("Something new on 2 servers");
    // What a tool said is shown as it came, whatever the language.
    applyServerEvent(new QueryClient(), "notice", { text: "certbot said no", state: "failed" });
    expect(error).toHaveBeenLastCalledWith("certbot said no");
  });

  it("says the first line of a failed deploy in the console's language and leaves what the gate printed as it came", async () => {
    const error = vi.spyOn(toast, "error");
    const gate = "  Details: Health check attempts 1-15 failed: HTTP 502";
    const notice = {
      text: `Release 20261031-2 did not pass its health check; release 20261031-1 is active again\n${gate}`,
      state: "failed",
      coded: { code: "deploy_failure.release_unhealthy_back", params: { release: "20261031-2", previous: "20261031-1" } },
    } as const;

    applyServerEvent(new QueryClient(), "notice", notice);
    expect(error).toHaveBeenLastCalledWith("Release 20261031-2 did not pass its startup check; release 20261031-1 is active again", {
      detail: `Release 20261031-2 did not pass its startup check; release 20261031-1 is active again\n${gate}`,
    });

    await act(() => setLocale("es"));
    applyServerEvent(new QueryClient(), "notice", notice);
    const first = "La versión 20261031-2 no superó la comprobación de arranque; la versión 20261031-1 está activa de nuevo";
    // The probes are another program's words: not translated, not paraphrased, still all there.
    expect(error).toHaveBeenLastCalledWith(first, { detail: `${first}\n${gate}` });

    // A code this console has no text for keeps the English the server sent, with the rest.
    applyServerEvent(new QueryClient(), "notice", { ...notice, coded: { code: "deploy_failure.something_new", params: {} } });
    expect(error).toHaveBeenLastCalledWith("Release 20261031-2 did not pass its health check; release 20261031-1 is active again", { detail: notice.text });
  });

  it("waits for the texts of the server's sentences when this console has not fetched them yet, so the first toast is in its language too", async () => {
    const error = vi.spyOn(toast, "error");
    await act(() => setLocale("es"));
    try {
      // A console that has just opened has only its chrome: the rest is fetched the first time it is asked for.
      dropNamespaces("en", CHROME);
      dropNamespaces("es", CHROME);
      error.mockClear();

      applyServerEvent(new QueryClient(), "notice", { text: "Build failed", state: "failed", coded: { code: "deploy_failure.build_failed", params: {} } });
      // Not said in English first, and then again in Spanish.
      expect(error).not.toHaveBeenCalled();
      await waitFor(() => {
        expect(error).toHaveBeenCalledTimes(1);
      });
      expect(error).toHaveBeenLastCalledWith("No se pudo compilar la aplicación");
    } finally {
      await loadCatalog("en");
      await loadCatalog("es");
    }
  });

  it("keeps a failed deploy's notice the size of a notice: its first line, and all of it beside, to scroll", () => {
    const error = vi.spyOn(toast, "error");
    const npm = ["npm ERR! code ELIFECYCLE", "npm ERR! errno 1", "npm ERR! app@1.4.2 build: `next build`", "npm ERR! Exit status 1"].join("\n");
    applyServerEvent(new QueryClient(), "notice", { text: npm, state: "failed" });
    expect(error).toHaveBeenLastCalledWith("npm ERR! code ELIFECYCLE", { detail: npm });
    // One short line is the whole of it: nothing to scroll.
    applyServerEvent(new QueryClient(), "notice", { text: "Application not found: shop.example.com", state: "failed" });
    expect(error).toHaveBeenLastCalledWith("Application not found: shop.example.com");
  });

  it("clips a title that is one very long line, and still keeps every word", () => {
    const line = `The build failed: ${"x".repeat(300)}`;
    const words = failureWords(line);
    expect(words.title.length).toBeLessThanOrEqual(120);
    expect(words.title.endsWith("…")).toBe(true);
    expect(words.detail).toBe(line);
  });

  it("ignores payloads that are not the shape it expects", () => {
    const client = new QueryClient();
    applyServerEvent(client, "app", { status: "failed" });
    applyServerEvent(client, "job", ["not", "an", "object"]);
    expect(client.getQueryCache().getAll()).toHaveLength(0);
  });
});

describe("reconnectDelay", () => {
  it("starts at a second, doubles, and stops at thirty", () => {
    expect([0, 1, 2, 3, 4, 5, 6, 10].map(reconnectDelay)).toEqual([1000, 2000, 4000, 8000, 16000, 30000, 30000, 30000]);
    expect(RECONNECT.maxMs).toBe(30_000);
  });
});

describe("EventStream", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  const start = (onDrop = vi.fn()) => {
    const events: [string, unknown][] = [];
    const statuses: string[] = [];
    const stream = new EventStream({
      connect: (url) => new FakeEventSource(url),
      onEvent: (name, data) => events.push([name, data]),
      onStatus: (status) => statuses.push(status),
      onDrop,
    });
    stream.start();
    return { stream, events, statuses, onDrop };
  };

  it("opens one source on /events and dispatches its named JSON events", () => {
    const { events, statuses } = start();
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(FakeEventSource.latest().url).toBe("/events");
    FakeEventSource.latest().open();
    FakeEventSource.latest().emit("machine", MACHINE);
    expect(events).toEqual([["machine", MACHINE]]);
    expect(statuses).toEqual(["connecting", "live"]);
  });

  it("drops a malformed frame and keeps the stream", () => {
    const logged = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const { events } = start();
    FakeEventSource.latest().emit("app", "{not json");
    FakeEventSource.latest().emit("app", { domain: "a.example.com" });
    expect(events).toEqual([["app", { domain: "a.example.com" }]]);
    expect(logged).toHaveBeenCalledOnce();
  });

  it("backs off 1s, 2s, 4s... up to 30s, and starts again from 1s after a success", () => {
    const { statuses, onDrop } = start();
    const waits: number[] = [];
    for (let attempt = 0; attempt < 7; attempt += 1) {
      const before = FakeEventSource.instances.length;
      FakeEventSource.latest().fail();
      expect(FakeEventSource.instances[before - 1]?.closed).toBe(true);
      let waited = 0;
      while (FakeEventSource.instances.length === before) {
        vi.advanceTimersByTime(500);
        waited += 500;
      }
      waits.push(waited);
    }
    expect(waits).toEqual([1000, 2000, 4000, 8000, 16000, 30000, 30000]);
    expect(statuses.at(-1)).toBe("reconnecting");
    expect(onDrop).toHaveBeenLastCalledWith(7);

    FakeEventSource.latest().open();
    expect(statuses.at(-1)).toBe("live");
    const before = FakeEventSource.instances.length;
    FakeEventSource.latest().fail();
    vi.advanceTimersByTime(999);
    expect(FakeEventSource.instances).toHaveLength(before);
    vi.advanceTimersByTime(1);
    expect(FakeEventSource.instances).toHaveLength(before + 1);
  });

  it("stops reconnecting once stopped", () => {
    const { stream } = start();
    FakeEventSource.latest().fail();
    stream.stop();
    vi.advanceTimersByTime(60_000);
    expect(FakeEventSource.instances).toHaveLength(1);
  });
});

describe("useServerEvents", () => {
  it("sends the operator to sign in when the stream drops because the session ended", async () => {
    const { renderHook } = await import("@testing-library/react");
    const { QueryClientProvider } = await import("@tanstack/react-query");
    const { createElement } = await import("react");
    const { useServerEvents } = await import("./events");

    fakeBackend({ "GET /api/auth/session": () => json(200, ANONYMOUS) });
    const onSessionExpired = vi.fn();
    configureApi({ onSessionExpired });
    const client = new QueryClient();
    renderHook(() => {
      useServerEvents((url) => new FakeEventSource(url));
    }, { wrapper: ({ children }) => createElement(QueryClientProvider, { client }, children) });

    FakeEventSource.latest().fail();
    await vi.waitFor(() => {
      expect(onSessionExpired).toHaveBeenCalledOnce();
    });
  });

  it("reads everything again once the stream is back: events said while it was down are lost", async () => {
    const { renderHook } = await import("@testing-library/react");
    const { QueryClientProvider } = await import("@tanstack/react-query");
    const { createElement } = await import("react");
    const { useServerEvents } = await import("./events");

    vi.useFakeTimers();
    try {
      fakeBackend({ "GET /api/auth/session": () => json(200, { ...ANONYMOUS, authenticated: true }) });
      const client = new QueryClient();
      client.setQueryData(jobKeys.detail("j1"), { id: "j1", status: "running" });
      renderHook(() => {
        useServerEvents((url) => new FakeEventSource(url));
      }, { wrapper: ({ children }) => createElement(QueryClientProvider, { client }, children) });

      FakeEventSource.latest().open();
      expect(client.getQueryState(jobKeys.detail("j1"))?.isInvalidated).toBe(false);

      FakeEventSource.latest().fail();
      await vi.advanceTimersByTimeAsync(RECONNECT.initialMs);
      FakeEventSource.latest().open();
      expect(client.getQueryState(jobKeys.detail("j1"))?.isInvalidated).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });
});
