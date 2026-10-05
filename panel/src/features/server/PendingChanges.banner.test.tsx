import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { formatClock } from "../../lib/format";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { PENDING_POLL_MS, changesQuery, serverKeys } from "./queries";
import type { PendingChange } from "./queries";
import { onDesktop, pendingChange, pendingChangeFromOlderNode, serverRoutes } from "./testing";

beforeEach(onDesktop);

const LOGIN_AT = Date.now() / 1000 - 5;
const SEEN: Partial<PendingChange> = {
  proof_seen: true,
  proof_login: { user: "alex", source: "203.0.113.5", at: LOGIN_AT },
};
/** What the server answers when Keep is pressed before a new login is on record. */
const keepRefused = (): Response =>
  problem(400, "accessguarderror", "No new SSH login since the change", {
    hint: "Keep this session open, open a NEW SSH session to this server and confirm again once it has logged in.",
  });

/** The Server area with `current()` as its pending change, read again each time the poll runs. */
function banner(current: () => PendingChange, extra: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend({
    ...signedInRoutes(),
    ...serverRoutes(),
    "GET /api/server/security/changes": () => json(200, [current()]),
    ...extra,
  });
  const harness = renderConsole("/server");
  return { ...harness, backend };
}

/** The banner itself, once it has loaded. */
async function pendingBanner(): Promise<HTMLElement> {
  const title = await screen.findByText(/change is waiting for you: it undoes itself in \d+:\d\d/);
  const found = title.closest("[data-tone]");
  if (!(found instanceof HTMLElement)) throw new Error("the title is not inside a notice");
  return found;
}

describe("the pending change banner, before a new login", () => {
  it("says what to do first, shows the step waiting and keeps Keep off with its reason beside it", async () => {
    banner(() => pendingChange());
    const notice = await pendingBanner();

    const text = notice.textContent;
    const instruction = text.indexOf("Open a new SSH session to this server from another terminal");
    const step = text.indexOf("Waiting for a new SSH login");
    expect(instruction).toBeGreaterThanOrEqual(0);
    expect(step).toBeGreaterThan(instruction);
    expect(within(notice).getByText(/A session that was already open proves nothing/)).toBeInTheDocument();

    const keep = within(notice).getByRole("button", { name: "Keep the change" });
    expect(keep).toBeDisabled();
    // Not only disabled: the reason is what a screen reader reads with the button.
    expect(keep).toHaveAccessibleDescription(/Waiting for a new SSH login.*Keep the change turns on as soon as one is seen/);
    // Undoing never needs a login.
    expect(within(notice).getByRole("button", { name: "Undo now" })).toBeEnabled();
    // The terminal's way is still there.
    expect(within(notice).getByText("noust server security confirm c1a2b3")).toBeInTheDocument();
    await expectNoAxeViolations(notice);
  });

  it("says that any new login counts for a firewall change", async () => {
    banner(() => pendingChange(Date.now(), { kind: "firewall", title: "Turn the firewall on", proof: "any" }));
    const notice = await pendingBanner();

    expect(within(notice).getByText(/A firewall change is waiting for you/)).toBeInTheDocument();
    expect(within(notice).getByText(/Any new login counts/)).toBeInTheDocument();
    expect(within(notice).getByRole("button", { name: "Keep the change" })).toBeDisabled();
  });

  it("turns Keep on, and says who logged in, when the next reading finds the login", async () => {
    let change = pendingChange();
    const { user, queryClient, backend } = banner(
      () => change,
      { "POST /api/server/security/changes/c1a2b3/confirm": () => json(200, { ...change, status: "confirmed" }) },
    );
    const notice = await pendingBanner();
    expect(within(notice).getByRole("button", { name: "Keep the change" })).toBeDisabled();

    change = pendingChange(Date.now(), SEEN);
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: serverKeys.changes });
    });

    const when = formatClock(new Date(LOGIN_AT * 1000), "en");
    await waitFor(() => {
      expect(within(notice).getByRole("button", { name: "Keep the change" })).toBeEnabled();
    });
    expect(notice).toHaveTextContent(`New login: alex from 203.0.113.5 at ${when}`);
    // Said politely, once, to whoever is in the other terminal.
    expect(within(notice).getByText("The way in still works, so the change is safe to keep.").closest("[role=status]")).not.toBeNull();
    // What to do is done: the instruction goes, and Keep no longer has a reason to be described.
    expect(within(notice).queryByText(/Open a new SSH session to this server/)).not.toBeInTheDocument();
    expect(within(notice).getByRole("button", { name: "Keep the change" })).not.toHaveAttribute("aria-describedby");
    await expectNoAxeViolations(notice);

    await user.click(within(notice).getByRole("button", { name: "Keep the change" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/server/security/changes/c1a2b3/confirm")).toHaveLength(1);
    });
  });

  it("shows why the history cannot be read, verbatim, keeps Keep off and keeps the terminal's command", async () => {
    const error = "No journal files were found.; and neither /var/log/auth.log nor /var/log/secure can be read";
    banner(() => pendingChange(Date.now(), { proof_readable: false, proof_error: error }));
    const notice = await pendingBanner();

    expect(within(notice).getByText("Noust cannot see new SSH logins from the console")).toBeInTheDocument();
    expect(within(notice).getByText(error)).toBeInTheDocument();
    expect(within(notice).getByRole("button", { name: "Keep the change" })).toBeDisabled();
    expect(within(notice).getByRole("button", { name: "Keep the change" })).toHaveAccessibleDescription(/cannot see new SSH logins/);
    expect(within(notice).getByText("noust server security confirm c1a2b3")).toBeInTheDocument();
    await expectNoAxeViolations(notice);
  });
});

describe("a pending change from a server that does not report the proof", () => {
  it("shows the instruction, no proof step and no error block, and leaves Keep on", async () => {
    banner(() => pendingChangeFromOlderNode());
    const notice = await pendingBanner();

    expect(within(notice).getByText(/Open a new SSH session to this server from another terminal/)).toBeInTheDocument();
    expect(within(notice).queryByText("Waiting for a new SSH login")).not.toBeInTheDocument();
    expect(within(notice).queryByText("Noust cannot see new SSH logins from the console")).not.toBeInTheDocument();
    expect(within(notice).queryByText("Why the logins cannot be read")).not.toBeInTheDocument();
    const keep = within(notice).getByRole("button", { name: "Keep the change" });
    expect(keep).toBeEnabled();
    expect(keep).not.toHaveAttribute("aria-describedby");
    expect(within(notice).getByText("noust server security confirm c1a2b3")).toBeInTheDocument();
    await expectNoAxeViolations(notice);
  });

  it("says that any login counts for a firewall change, and lets the server's confirm answer", async () => {
    const { user, backend } = banner(
      () => pendingChangeFromOlderNode(Date.now(), { kind: "firewall", title: "Turn the firewall on", proof: "any" }),
      { "POST /api/server/security/changes/c1a2b3/confirm": () => keepRefused() },
    );
    const notice = await pendingBanner();

    expect(within(notice).getByText(/Any new login counts/)).toBeInTheDocument();
    await user.click(within(notice).getByRole("button", { name: "Keep the change" }));
    // The refusal is the server's own, not a guess the console made before asking.
    expect(await screen.findByText("No new SSH login since the change")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/server/security/changes/c1a2b3/confirm")).toHaveLength(1);
  });

  it("does not call a readable history unreadable when only the error text is missing", async () => {
    banner(() => pendingChange(Date.now(), { proof_readable: false, proof_error: undefined as unknown as string }));
    const notice = await pendingBanner();

    expect(within(notice).getByText("Noust cannot see new SSH logins from the console")).toBeInTheDocument();
    expect(within(notice).queryByText("Why the logins cannot be read")).not.toBeInTheDocument();
    expect(within(notice).getByRole("button", { name: "Keep the change" })).toBeDisabled();
  });
});

describe("a refused Keep and Undo", () => {
  it("does not leave Keep's refusal up when the undo dialog opens", async () => {
    const { user, backend } = banner(() => pendingChange(Date.now(), SEEN), {
      "POST /api/server/security/changes/c1a2b3/confirm": () => keepRefused(),
    });
    const notice = await pendingBanner();

    await user.click(within(notice).getByRole("button", { name: "Keep the change" }));
    expect(await screen.findByText("No new SSH login since the change")).toBeInTheDocument();
    expect(screen.getByText("Could not keep the change")).toBeInTheDocument();

    await user.click(within(notice).getByRole("button", { name: "Undo now" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Undo the change now?" });

    expect(dialog).toBeInTheDocument();
    expect(screen.queryByText("No new SSH login since the change")).not.toBeInTheDocument();
    expect(screen.queryByText("Could not keep the change")).not.toBeInTheDocument();
    // Opening the dialog undid nothing.
    expect(backend.callsTo("POST /api/server/security/changes/c1a2b3/revert")).toHaveLength(0);
  });

  it("drops a refusal that no longer holds once the login arrives", async () => {
    let change = pendingChange(Date.now(), SEEN);
    const { user, queryClient } = banner(() => change, {
      "POST /api/server/security/changes/c1a2b3/confirm": () => keepRefused(),
    });
    const notice = await pendingBanner();
    await user.click(within(notice).getByRole("button", { name: "Keep the change" }));
    expect(await screen.findByText("No new SSH login since the change")).toBeInTheDocument();

    // The record loses the login (its window moves on) and then finds one again.
    change = pendingChange();
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: serverKeys.changes });
    });
    await waitFor(() => {
      expect(within(notice).getByRole("button", { name: "Keep the change" })).toBeDisabled();
    });
    change = pendingChange(Date.now(), SEEN);
    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: serverKeys.changes });
    });

    await waitFor(() => {
      expect(screen.queryByText("No new SSH login since the change")).not.toBeInTheDocument();
    });
  });

  it("undoes the change without asking for a login", async () => {
    let change = pendingChange();
    const { user, backend } = banner(() => change, {
      "POST /api/server/security/changes/c1a2b3/revert": () => {
        change = { ...change, status: "reverted" };
        return json(200, change);
      },
    });
    const notice = await pendingBanner();

    await user.click(within(notice).getByRole("button", { name: "Undo now" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Undo the change now?" });
    await user.click(within(dialog).getByRole("button", { name: "Undo the change" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/server/security/changes/c1a2b3/revert")).toHaveLength(1);
    });
  });
});

describe("how often a pending change is read", () => {
  it("reads every few seconds while a change waits, and stops once none does", () => {
    const interval = changesQuery().refetchInterval;
    if (typeof interval !== "function") throw new Error("the changes poll must depend on what was read");
    const reading = (changes: PendingChange[]) => ({ state: { data: changes } }) as unknown as Parameters<typeof interval>[0];

    expect(PENDING_POLL_MS).toBeLessThanOrEqual(3_000);
    expect(interval(reading([pendingChange()]))).toBe(PENDING_POLL_MS);
    expect(interval(reading([pendingChange(Date.now(), { status: "confirmed" })]))).toBe(false);
    expect(interval(reading([]))).toBe(false);
  });
});

describe("the pending change banner in Spanish", () => {
  it("says the same, with the instruction first and Keep off until the login is seen", async () => {
    await act(async () => {
      await setLocale("es");
    });
    banner(() => pendingChange());

    const title = await screen.findByText(/Un cambio de SSH espera tu confirmación: se deshace solo en \d+:\d\d/);
    const notice = title.closest("[data-tone]");
    if (!(notice instanceof HTMLElement)) throw new Error("the title is not inside a notice");

    expect(within(notice).getByText(/Abre una sesión SSH nueva a este servidor desde otra terminal/)).toBeInTheDocument();
    expect(within(notice).getByText("Esperando un acceso SSH nuevo")).toBeInTheDocument();
    const keep = within(notice).getByRole("button", { name: "Conservar el cambio" });
    expect(keep).toBeDisabled();
    expect(keep).toHaveAccessibleDescription(/Esperando un acceso SSH nuevo/);
    await expectNoAxeViolations(notice);
  });

  it("names who logged in and when once the login is on record", async () => {
    await act(async () => {
      await setLocale("es");
    });
    banner(() => pendingChange(Date.now(), SEEN));

    const when = formatClock(new Date(LOGIN_AT * 1000), "es");
    const title = await screen.findByText(/Un cambio de SSH espera tu confirmación/);
    const notice = title.closest("[data-tone]");
    if (!(notice instanceof HTMLElement)) throw new Error("the title is not inside a notice");

    expect(notice).toHaveTextContent(`Acceso nuevo: alex desde 203.0.113.5 a las ${when}`);
    expect(within(notice).getByRole("button", { name: "Conservar el cambio" })).toBeEnabled();
    await expectNoAxeViolations(notice);
  });
});
