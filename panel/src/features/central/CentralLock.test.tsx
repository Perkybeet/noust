import { act, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem } from "../../test/fakes";
import type { FakeBackend } from "../../test/fakes";
import { centralSession, fleetRoutes } from "../fleet/testFixtures";

/** A sealed central whose passphrase is "correct horse"; unlocking it takes sudo mode. */
function sealedCentral(): FakeBackend {
  let locked = true;
  let elevated = false;
  const backend = fakeBackend({
    ...fleetRoutes(),
    "GET /api/auth/session": () => json(200, centralSession({ sealed: true, locked })),
    "POST /api/auth/elevate": () => {
      elevated = true;
      return json(200, { elevated_until: new Date(Date.now() + 600_000).toISOString() });
    },
    "POST /api/central/unlock": (call) => {
      if (!elevated) return problem(403, "elevation_required", "Confirm it's you to continue.");
      const { passphrase } = call.body as { passphrase: string };
      if (passphrase !== "correct horse") return problem(403, "wrong_passphrase", "The passphrase does not open the sealed secrets");
      locked = false;
      return json(200, { role: "server", sealed: true, locked: false });
    },
  });
  return backend;
}

async function confirmItsYou(user: ReturnType<typeof renderConsole>["user"]): Promise<void> {
  const confirm = await screen.findByRole("dialog", { name: "Confirm it's you" });
  await user.type(within(confirm).getByLabelText("Authentication code"), "123456");
  await user.click(within(confirm).getByRole("button", { name: "Confirm" }));
}

afterEach(() => {
  window.sessionStorage.clear();
});

describe("a sealed central", () => {
  it("shows the lock screen after sign-in, refuses a wrong passphrase, and opens with the right one", { timeout: 30_000 }, async () => {
    const backend = sealedCentral();
    const { user, container } = renderConsole("/");
    expect(await screen.findByRole("heading", { level: 1, name: "This central is locked" })).toBeInTheDocument();
    expect(screen.getByText(/nothing reaches the servers/)).toBeInTheDocument();
    // The machine is named first, so a passphrase is never typed into the wrong one.
    expect(screen.getByText("web-01")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 1, name: "Overview" })).not.toBeInTheDocument();
    await expectNoAxeViolations(container, { page: true });

    const field = screen.getByLabelText("Passphrase");
    expect(field).toHaveAttribute("type", "password");
    await waitFor(() => {
      expect(field).toHaveFocus();
    });
    await user.type(field, "wrong one");
    await user.click(screen.getByRole("button", { name: "Unlock" }));
    // Unlocking is sudo mode, like every action that reaches the servers' secrets.
    await confirmItsYou(user);
    expect(await screen.findByText("That passphrase does not open this central's secrets. Check it and try again.")).toBeInTheDocument();
    expect(field).toHaveAttribute("aria-invalid", "true");

    await user.clear(field);
    await user.type(field, "correct horse");
    await user.click(screen.getByRole("button", { name: "Unlock" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Overview" })).toBeInTheDocument();
    expect(backend.callsTo("POST /api/central/unlock").map((call) => call.body)).toEqual([
      { passphrase: "wrong one" },
      { passphrase: "wrong one" },
      { passphrase: "correct horse" },
    ]);
  });

  it("shows any other refusal in the system's words, with its fix", { timeout: 20_000 }, async () => {
    const backend = sealedCentral();
    backend.on("POST /api/central/unlock", () =>
      problem(409, "sealerror", "The seal header /data/state/secrets/.seal is damaged", { hint: "Restore it from a backup of the volume." }),
    );
    const { user } = renderConsole("/");
    await user.type(await screen.findByLabelText("Passphrase"), "anything");
    await user.click(screen.getByRole("button", { name: "Unlock" }));
    const title = await screen.findByText("Could not unlock the central");
    const alert = title.closest<HTMLElement>("[role=alert]");
    if (alert === null) throw new Error("the refusal is not announced");
    expect(alert).toHaveTextContent("Restore it from a backup of the volume.");
    expect(within(alert).getByText("The seal header /data/state/secrets/.seal is damaged")).toBeInTheDocument();
  });

  it("goes on without unlocking, and the servers say they are locked until it is unlocked from there", { timeout: 30_000 }, async () => {
    sealedCentral();
    const { user } = renderConsole("/fleet");
    await user.click(await screen.findByRole("button", { name: "Continue without unlocking" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Fleet" })).toBeInTheDocument();
    expect(screen.getByText("Its servers are out of reach until you unlock its sealed secrets.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Unlock" }));
    const dialog = await screen.findByRole("dialog", { name: "Unlock this central" });
    await user.type(within(dialog).getByLabelText("Passphrase"), "correct horse");
    await user.click(within(dialog).getByRole("button", { name: "Unlock" }));
    await confirmItsYou(user);
    await waitFor(() => {
      expect(screen.queryByText("Its servers are out of reach until you unlock its sealed secrets.")).not.toBeInTheDocument();
    });
    const table = screen.getByRole("region", { name: "Servers of this fleet" });
    expect(await within(table).findByText("7.5%")).toBeInTheDocument();
  });

  it("speaks Spanish on the lock screen, with no accessibility violations", { timeout: 20_000 }, async () => {
    sealedCentral();
    const { container } = renderConsole("/");
    await screen.findByRole("heading", { level: 1, name: "This central is locked" });
    await act(async () => {
      await setLocale("es");
    });
    expect(await screen.findByRole("heading", { level: 1, name: "Esta central está bloqueada" })).toBeInTheDocument();
    expect(screen.getByLabelText("Frase de paso")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Continuar sin desbloquear" })).toBeInTheDocument();
    await expectNoAxeViolations(container, { page: true });
  });
});
