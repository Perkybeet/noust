import { act, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { renderConsole } from "../../test/console";
import { FakeEventSource, fakeBackend, json, signedInRoutes } from "../../test/fakes";
import { resetHandled } from "../../realtime/jobEnds";
import { EMPTY, taskStore } from "./store";
import { job } from "./taskFixtures";

const ME = "abcdef123456";

/** A task of the person's that has run for twenty seconds: long enough for leaving it to be a thing. */
const LONG = job({ id: "long1", actor: ME, started_at: new Date(Date.now() - 20_000).toISOString(), progress: 30 });

class FakeNotification {
  static permission: NotificationPermission = "default";
  static made: { title: string; body: string | undefined }[] = [];
  static requestPermission = vi.fn((): Promise<NotificationPermission> => {
    FakeNotification.permission = "granted";
    return Promise.resolve("granted");
  });
  onclick: (() => void) | null = null;
  constructor(title: string, options?: NotificationOptions) {
    FakeNotification.made.push({ title, body: options?.body });
  }
  close(): void {
    // A notification that is shown has nothing to close in a test.
  }
}

function serve(): void {
  fakeBackend({
    ...signedInRoutes(),
    "GET /api/auth/sessions": () => json(200, { active_sessions: 1, current_session: `${ME}789`, sessions: [] }),
    "GET /api/jobs/active": () => json(200, { jobs: [LONG], total: 1, active: 1 }),
    "GET /api/jobs": () => json(200, { jobs: [LONG], total: 1, active: 1 }),
    "GET /api/operations/active": () => json(200, { operations: [] }),
  });
}

beforeEach(() => {
  window.localStorage.clear();
  taskStore.reset();
  resetHandled();
  FakeNotification.permission = "default";
  FakeNotification.made = [];
  FakeNotification.requestPermission.mockClear();
  vi.stubGlobal("Notification", FakeNotification);
});

afterEach(() => {
  taskStore.update(() => EMPTY);
  vi.restoreAllMocks();
});

describe("the browser's notification", () => {
  it("is offered the first time you leave a long task of yours, never before, and turns on only when you say so", async () => {
    serve();
    const { user, router } = renderConsole("/apps");
    await screen.findByRole("button", { name: "Tasks: 1 running, 0 queued" });
    // Nothing is asked of the browser, or said, until you leave.
    expect(FakeNotification.requestPermission).not.toHaveBeenCalled();
    expect(screen.queryByText("Want to know when it ends?")).toBeNull();

    await act(() => router.navigate({ to: "/databases" }));
    const toasts = await screen.findByRole("region", { name: "Notifications" });
    expect(await within(toasts).findByText("Want to know when it ends?")).toBeInTheDocument();
    expect(FakeNotification.requestPermission).not.toHaveBeenCalled();
    expect(window.localStorage.getItem("noust.tasks.notify")).toBeNull();

    await user.click(within(toasts).getByRole("button", { name: "Notify me even if I am on another tab" }));
    await waitFor(() => {
      expect(FakeNotification.requestPermission).toHaveBeenCalledTimes(1);
    });
    expect(window.localStorage.getItem("noust.tasks.notify")).toBe("on");
    expect(window.localStorage.getItem("noust.tasks.notify.offered")).toBe("yes");
    expect(await within(toasts).findByText("Noust will notify you when your tasks end.")).toBeInTheDocument();
  });

  it("is not offered again once it was, whatever the answer", async () => {
    window.localStorage.setItem("noust.tasks.notify.offered", "yes");
    serve();
    const { router } = renderConsole("/apps");
    await screen.findByRole("button", { name: "Tasks: 1 running, 0 queued" });
    await act(() => router.navigate({ to: "/databases" }));
    await act(() => router.navigate({ to: "/domains" }));
    expect(screen.queryByText("Want to know when it ends?")).toBeNull();
  });

  it("is not offered for a task that is not yours, or one that has only just begun", async () => {
    fakeBackend({
      ...signedInRoutes(),
      "GET /api/auth/sessions": () => json(200, { active_sessions: 1, current_session: `${ME}789`, sessions: [] }),
      "GET /api/jobs/active": () => json(200, { jobs: [{ ...LONG, actor: "ben" }, job({ id: "short1", actor: ME, started_at: new Date().toISOString() })], total: 2, active: 2 }),
      "GET /api/jobs": () => json(200, { jobs: [], total: 0, active: 0 }),
      "GET /api/operations/active": () => json(200, { operations: [] }),
    });
    const { router } = renderConsole("/apps");
    await screen.findByRole("button", { name: "Tasks: 2 running, 0 queued" });
    await act(() => router.navigate({ to: "/databases" }));
    expect(screen.queryByText("Want to know when it ends?")).toBeNull();
  });

  it("shows when a task of yours ends while the tab is hidden, once it is on, and then the toast is not", async () => {
    window.localStorage.setItem("noust.tasks.notify", "on");
    FakeNotification.permission = "granted";
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    serve();
    renderConsole("/databases");
    await screen.findByRole("button", { name: "Tasks: 1 running, 0 queued" });
    act(() => {
      FakeEventSource.latest().open();
      FakeEventSource.latest().emit("job", { ...LONG, status: "completed", state: "succeeded", completed_at: new Date().toISOString() });
    });
    await waitFor(() => {
      expect(FakeNotification.made).toEqual([{ title: "Deploy shop.example.com: done", body: "You started it and left that page." }]);
    });
    expect(within(screen.getByRole("region", { name: "Notifications" })).queryByText("Deploy shop.example.com: done")).toBeNull();
  });
});
