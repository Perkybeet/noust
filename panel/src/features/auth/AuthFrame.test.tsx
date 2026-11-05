import { act, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { ANONYMOUS, SESSION, fakeBackend, json, signedInRoutes } from "../../test/fakes";

/** What a server says of itself before anyone signs in: a name and a version it would rather not have shown. */
const STRANGER = { ...ANONYMOUS, hostname: "db-primary.corp.internal", version: "4.2.0", login_label: null };

/** The part of the slab with this `data-slot`, or a failure that says which one is missing. */
function slot(slab: HTMLElement, name: string): HTMLElement {
  const found = slab.querySelector<HTMLElement>(`[data-slot='${name}']`);
  if (found === null) throw new Error(`the slab has no "${name}" slot`);
  return found;
}

function signedOutBackend(session: Record<string, unknown> = STRANGER) {
  return fakeBackend({ ...signedInRoutes(), "GET /api/auth/session": () => json(200, session) });
}

describe("the frame of the screens before the console", () => {
  it("is the slab: the logo, the drawing and a sentence on the left, the title and the form on the right", async () => {
    signedOutBackend();
    renderConsole("/login");
    await screen.findByLabelText("Username or email");
    const slab = screen.getByRole("main");
    const identity = slot(slab, "identity");
    expect(within(identity).getByRole("img", { name: "Noust" })).toBeInTheDocument();
    expect(identity.querySelector("svg[aria-hidden='true']")).not.toBeNull();
    expect(within(identity).getByText("Your apps and servers, kept safe.")).toBeInTheDocument();
    const form = slot(slab, "form");
    expect(within(form).getByRole("heading", { level: 1, name: "Sign in" })).toBeInTheDocument();
    expect(within(form).getByLabelText("Username or email")).toBeInTheDocument();
    expect(slab).toHaveAttribute("data-template", "auth");
  });

  it("shows nothing of the server before sign-in: not its name, not its version", async () => {
    signedOutBackend();
    renderConsole("/login");
    await screen.findByLabelText("Username or email");
    expect(document.body).not.toHaveTextContent("db-primary.corp.internal");
    expect(document.body).not.toHaveTextContent("4.2.0");
    expect(document.body).not.toHaveTextContent("Noust console");
    expect(document.querySelector("[data-slot='label']")).toBeNull();
  });

  it("shows only the line the operator chose, and still not the version", async () => {
    signedOutBackend({ ...STRANGER, hostname: "web-01 (production)", login_label: "web-01 (production)" });
    renderConsole("/login");
    await screen.findByLabelText("Username or email");
    const label = document.querySelector("[data-slot='label']");
    expect(label).toHaveTextContent("web-01 (production)");
    expect(screen.getByText("web-01 (production)")).toHaveAttribute("translate", "no");
    expect(document.body).not.toHaveTextContent("4.2.0");
  });

  it("keeps the licence notice under the slab, whoever looks", async () => {
    signedOutBackend();
    renderConsole("/login");
    await screen.findByLabelText("Username or email");
    const legal = screen.getByRole("contentinfo");
    expect(legal).toHaveTextContent(/© 2024-2026 Yago López Prado\. Free software under the AGPL-3\.0-or-later/);
    expect(within(legal).getByRole("link", { name: /^AGPL-3\.0-or-later/ })).toHaveAttribute(
      "href",
      "https://github.com/Perkybeet/noust/blob/main/LICENSE",
    );
    expect(screen.getByRole("main")).not.toContainElement(legal);
  });

  it("names the machine once a person is in and a check is still owed, and still not its version", async () => {
    fakeBackend({
      ...signedInRoutes(),
      "GET /api/auth/session": () => json(200, { ...SESSION, hostname: "web-01", version: "4.2.0", mfa_required: true, grant: null, role: "operator" }),
    });
    renderConsole("/welcome");
    await screen.findByRole("heading", { level: 1, name: "Set up a second factor" });
    expect(document.querySelector("[data-slot='label']")).toHaveTextContent("web-01");
    expect(document.body).not.toHaveTextContent("4.2.0");
  });

  it("says its sentence in Spanish and has no accessibility violations in either language", async () => {
    signedOutBackend();
    renderConsole("/login");
    await screen.findByLabelText("Username or email");
    await expectNoAxeViolations(document.body, { page: true });
    await act(async () => {
      await setLocale("es");
    });
    expect(screen.getByText("Tus aplicaciones y tus servidores, a resguardo.")).toBeInTheDocument();
    expect(screen.getByRole("contentinfo")).toHaveTextContent(/Software libre bajo la AGPL-3\.0-or-later/);
    await expectNoAxeViolations(document.body, { page: true });
  });
});
