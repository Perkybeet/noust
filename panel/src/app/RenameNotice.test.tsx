import { act, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../test/axe";
import { renderConsole } from "../test/console";
import { SESSION, fakeBackend, signedInRoutes } from "../test/fakes";
import { setLocale } from "./locale";

const STORAGE_KEY = "noust.renameNotice";
const URL = "https://github.com/Perkybeet/noust/blob/main/docs/UPGRADING-3.0.md";
const EN_TEXT = "WASM is now Noust — nothing else changed: same console, same applications.";
const ES_TEXT = "WASM ahora se llama Noust: nada más ha cambiado, la misma consola y las mismas aplicaciones.";

async function shellAt(renamed: boolean) {
  fakeBackend(signedInRoutes({ ...SESSION, renamed_from_wasm: renamed }));
  const harness = renderConsole("/");
  await screen.findByRole("heading", { level: 1 });
  return harness;
}

/** The notice's own container: the page also has a global `role="status"` announcer, so
 * finding this one by its text keeps the two apart. */
function notice(text: string): HTMLElement {
  const paragraph = screen.getByText(text);
  const container = paragraph.closest('[role="status"]');
  if (container === null) throw new Error("The rename notice's text is not inside a role=status container.");
  return container as HTMLElement;
}

describe("the rename notice", () => {
  it("shows when this server ran WASM before Noust, and is not dismissed", async () => {
    await shellAt(true);
    await screen.findByText(EN_TEXT);
    expect(notice(EN_TEXT)).toHaveAttribute("role", "status");
  });

  it("stays hidden when this server never ran WASM", async () => {
    await shellAt(false);
    expect(screen.queryByText(EN_TEXT)).not.toBeInTheDocument();
  });

  it("links to what changed, opened in a new tab", async () => {
    await shellAt(true);
    const link = await screen.findByRole("link", { name: /What changed and why/ });
    expect(link).toHaveAttribute("href", URL);
    expect(link).toHaveAttribute("target", "_blank");
  });

  it("dismisses, remembers it, and stays hidden after a reload", async () => {
    const { user } = await shellAt(true);
    await screen.findByText(EN_TEXT);
    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText(EN_TEXT)).not.toBeInTheDocument();
    expect(window.localStorage.getItem(STORAGE_KEY)).toBe("dismissed");

    // A fresh mount, as a reload would be, reads the stored dismissal back.
    await shellAt(true);
    expect(screen.queryByText(EN_TEXT)).not.toBeInTheDocument();
  });

  it("has no accessibility violations", async () => {
    await shellAt(true);
    await screen.findByText(EN_TEXT);
    await expectNoAxeViolations(notice(EN_TEXT));
  });

  it("shows in Spanish", async () => {
    await act(async () => {
      await setLocale("es");
    });
    await shellAt(true);
    await screen.findByText(ES_TEXT);
    expect(notice(ES_TEXT)).toHaveAttribute("role", "status");
    expect(screen.getByRole("link", { name: /Qué ha cambiado y por qué/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Descartar" })).toBeInTheDocument();
  });
});
