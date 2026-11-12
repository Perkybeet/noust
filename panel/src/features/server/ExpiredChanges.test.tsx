import { act, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../test/fakes";
import { DISMISSED_KEY, recentlyExpired } from "./ExpiredChanges";
import type { PendingChange } from "./queries";
import { onDesktop, pendingChange, serverRoutes } from "./testing";

const NOW = Date.parse("2026-11-10T12:00:00Z");
const expired = (patch: Partial<PendingChange> = {}): PendingChange =>
  pendingChange(NOW, { status: "expired", kind: "firewall", title: "Allow 5432 from 10.0.4.12", resolved_at: NOW / 1000 - 600, resolved_by: "timer", ...patch });

beforeEach(() => {
  onDesktop();
  window.localStorage.clear();
});

afterEach(async () => {
  await act(() => setLocale("en"));
});

describe("which changes are told as undone", () => {
  it("are the ones the timer undid in the last day, newest first, that nobody put away", () => {
    const old = expired({ id: "old", resolved_at: NOW / 1000 - 2 * 86_400 });
    const mine = expired({ id: "mine", resolved_by: "ana" });
    const recent = expired({ id: "recent", resolved_at: NOW / 1000 - 60 });
    const earlier = expired({ id: "earlier", resolved_at: NOW / 1000 - 3_600 });
    const kept = pendingChange(NOW, { id: "kept", status: "confirmed" });
    const waiting = pendingChange(NOW, { id: "waiting" });

    expect(recentlyExpired([earlier, old, mine, recent, kept, waiting], NOW, []).map((change) => change.id)).toEqual(["recent", "earlier"]);
    // One put away is not told again.
    expect(recentlyExpired([earlier, recent], NOW, ["recent"]).map((change) => change.id)).toEqual(["earlier"]);
  });

  it("leaves out one that says no time: it cannot be told how long ago", () => {
    expect(recentlyExpired([expired({ resolved_at: null })], NOW, [])).toEqual([]);
  });
});

describe("a change nobody confirmed, on the Server page", () => {
  function serve(changes: PendingChange[]) {
    fakeBackend({ ...signedInRoutes(), ...serverRoutes({ changes }), "GET /api/server/security/changes": () => json(200, changes) });
    return renderConsole("/server");
  }

  it("says it was undone because nobody confirmed it, which change, and when (item 138)", async () => {
    serve([expired({ resolved_at: Date.now() / 1000 - 600 })]);

    const notice = (await screen.findByText("The firewall change was undone because nobody confirmed it")).closest("[data-tone]");
    if (!(notice instanceof HTMLElement)) throw new Error("not a notice");

    expect(notice).toHaveAttribute("data-tone", "warning");
    expect(within(notice).getByText("Allow 5432 from 10.0.4.12")).toBeInTheDocument();
    expect(notice).toHaveTextContent(/\d+m ago/);
  });

  it("says it in Spanish, and of SSH when it was SSH", async () => {
    await act(() => setLocale("es"));
    // Told for a day: the change is as old as the clock says, not as old as the fixed moment the others are made at.
    serve([expired({ kind: "sshd", title: "Desactivar las contraseñas", resolved_at: Date.now() / 1000 - 600 })]);

    expect(await screen.findByText("El cambio de SSH se deshizo porque nadie lo confirmó")).toBeInTheDocument();
  });

  it("is put away once, and stays away after a reload", async () => {
    const { user } = serve([expired({ resolved_at: Date.now() / 1000 - 600 })]);
    const title = await screen.findByText("The firewall change was undone because nobody confirmed it");
    const notice = title.closest("[data-tone]");
    if (!(notice instanceof HTMLElement)) throw new Error("not a notice");

    await user.click(within(notice).getByRole("button", { name: /Dismiss|Close/ }));

    await waitFor(() => {
      expect(screen.queryByText("The firewall change was undone because nobody confirmed it")).not.toBeInTheDocument();
    });
    expect(JSON.parse(window.localStorage.getItem(DISMISSED_KEY) ?? "[]")).toEqual(["c1a2b3"]);
  });

  it("is not there when nothing was undone", async () => {
    serve([pendingChange(Date.now(), { status: "confirmed" })]);

    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByText(/nobody confirmed it/)).not.toBeInTheDocument();
  });
});
