import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
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

/** The digest the picture is addressed by, and what `GET /api/auth/sign-in/appearance` says of it. */
const DIGEST = "a".repeat(64);
const LIMITS = { bytes: 1024 * 1024, side: 4096, alt_chars: 120, types: ["image/png", "image/jpeg", "image/webp"] };
const PICTURE = {
  url: `/api/auth/sign-in/image?v=${DIGEST}`,
  sha256: DIGEST,
  content_type: "image/png",
  width: 300,
  height: 100,
  size: 900,
  fit: "contain",
  alt: "Acme logo",
  updated_at: "2026-11-05T10:00:00+00:00",
};

function withAppearance(handler: () => Response | Promise<Response>, session: Record<string, unknown> = STRANGER) {
  return fakeBackend({
    ...signedInRoutes(),
    "GET /api/auth/session": () => json(200, session),
    "GET /api/auth/sign-in/appearance": handler,
  });
}

describe("the picture an operator chose for the slot of the drawing (item 129)", () => {
  it("is in the slot where the noust was, and nothing else of the slab is theirs", async () => {
    withAppearance(() => json(200, { image: PICTURE, limits: LIMITS }), { ...STRANGER, login_label: "web-01 (production)" });
    renderConsole("/login");
    const image = await screen.findByRole("img", { name: "Acme logo" });

    const slab = screen.getByRole("main");
    const art = slot(slab, "art");
    expect(art).toContainElement(image);
    expect(image).toHaveAttribute("src", PICTURE.url);
    expect(slab.querySelector("svg[viewBox='0 0 400 230']")).toBeNull();
    // Noust's logo, its sentence, the operator's line and the licence notice are where they were, outside the slot.
    const identity = slot(slab, "identity");
    expect(art).not.toContainElement(within(identity).getByRole("img", { name: "Noust" }));
    expect(within(identity).getByText("Your apps and servers, kept safe.")).toBeInTheDocument();
    expect(art).not.toContainElement(within(identity).getByText("Your apps and servers, kept safe."));
    expect(slot(slab, "label")).toHaveTextContent("web-01 (production)");
    expect(screen.getByRole("contentinfo")).toHaveTextContent(/Free software under the AGPL-3\.0-or-later/);
    expect(slab).not.toContainElement(screen.getByRole("contentinfo"));
  });

  it("fills the space or is shown whole, as the operator chose", async () => {
    withAppearance(() => json(200, { image: { ...PICTURE, fit: "cover" }, limits: LIMITS }));
    renderConsole("/login");

    const image = await screen.findByRole("img", { name: "Acme logo" });

    expect(image).toHaveClass("object-cover");
  });

  it("is decoration to a screen reader when it has no description", async () => {
    withAppearance(() => json(200, { image: { ...PICTURE, alt: "" }, limits: LIMITS }));
    renderConsole("/login");
    await screen.findByLabelText("Username or email");

    await waitFor(() => expect(slot(screen.getByRole("main"), "art").querySelector("img[src^='/api/auth/sign-in/image']")).not.toBeNull());
    expect(slot(screen.getByRole("main"), "art").querySelector("img")).toHaveAttribute("alt", "");
  });

  it("is the noust that was always drawn when no picture is set", async () => {
    withAppearance(() => json(200, { image: null, limits: LIMITS }));
    renderConsole("/login");
    await screen.findByLabelText("Username or email");

    await waitFor(() => expect(slot(screen.getByRole("main"), "art").querySelector("div.invisible")).toBeNull());
    expect(slot(screen.getByRole("main"), "art").querySelector("svg[viewBox='0 0 400 230']")).not.toBeNull();
    expect(slot(screen.getByRole("main"), "art").querySelector("img")).toBeNull();
  });

  it("is the noust too when the answer cannot be read, and the form is still there", async () => {
    withAppearance(() => json(500, { error: "internal_error", detail: "It broke" }));
    renderConsole("/login");
    await screen.findByLabelText("Username or email");

    await waitFor(() => expect(slot(screen.getByRole("main"), "art").querySelector("div.invisible")).toBeNull());
    expect(slot(screen.getByRole("main"), "art").querySelector("svg[viewBox='0 0 400 230']")).not.toBeNull();
  });

  it("keeps the room of the picture while the answer is on its way, and never draws the noust first", async () => {
    let answer: (response: Response) => void = () => undefined;
    withAppearance(() => new Promise<Response>((resolve) => (answer = resolve)));
    renderConsole("/login");
    await screen.findByLabelText("Username or email");

    // The drawing holds the place unseen, so the slab is the size it will be and nothing moves later.
    const waiting = slot(screen.getByRole("main"), "art").querySelector("div.invisible");
    expect(waiting).not.toBeNull();
    expect(waiting?.querySelector("svg[viewBox='0 0 400 230']")).not.toBeNull();

    await act(() => {
      answer(json(200, { image: PICTURE, limits: LIMITS }));
      return Promise.resolve();
    });
    expect(await screen.findByRole("img", { name: "Acme logo" })).toBeInTheDocument();
    expect(slot(screen.getByRole("main"), "art").querySelector("div.invisible")).toBeNull();
    expect(screen.getByRole("main").querySelector("svg[viewBox='0 0 400 230']")).toBeNull();
  });

  it("is the noust again when the picture does not load", async () => {
    withAppearance(() => json(200, { image: PICTURE, limits: LIMITS }));
    renderConsole("/login");
    const image = await screen.findByRole("img", { name: "Acme logo" });

    fireEvent.error(image);

    expect(slot(screen.getByRole("main"), "art").querySelector("img")).toBeNull();
    expect(slot(screen.getByRole("main"), "art").querySelector("svg[viewBox='0 0 400 230']")).not.toBeNull();
  });

  it("is on every screen of the slab, not only the first", async () => {
    fakeBackend({
      ...signedInRoutes(),
      "GET /api/auth/session": () => json(200, { ...SESSION, hostname: "web-01", mfa_required: true, grant: null, role: "operator" }),
      "GET /api/auth/sign-in/appearance": () => json(200, { image: PICTURE, limits: LIMITS }),
    });
    renderConsole("/welcome");
    await screen.findByRole("heading", { level: 1, name: "Set up a second factor" });

    expect(await screen.findByRole("img", { name: "Acme logo" })).toBeInTheDocument();
  });

  it("has no accessibility violations with a picture, and is the same in Spanish", async () => {
    withAppearance(() => json(200, { image: PICTURE, limits: LIMITS }));
    renderConsole("/login");
    await screen.findByRole("img", { name: "Acme logo" });
    await expectNoAxeViolations(document.body, { page: true });

    await act(async () => {
      await setLocale("es");
    });
    expect(screen.getByText("Tus aplicaciones y tus servidores, a resguardo.")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Acme logo" })).toBeInTheDocument();
    await expectNoAxeViolations(document.body, { page: true });
  });
});
