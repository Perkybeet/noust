import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { SESSION, fakeBackend, json, signedInRoutes } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { screenWidth } from "../../app/testRoutes";

const DIGEST = "b".repeat(64);
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
const NOTHING = { image: null, limits: LIMITS };
const SET = { image: PICTURE, limits: LIMITS };

/** A session that can read but not change: no `security.manage`. */
const READER = { ...SESSION, permissions: (SESSION.permissions ?? []).filter((permission) => permission !== "security.manage") };

function png(name = "logo.png", type = "image/png", size = 8): File {
  const bytes = new Uint8Array(size);
  bytes.set([137, 80, 78, 71, 13, 10, 26, 10]);
  return new File([bytes], name, { type });
}

async function pageAt(answer: unknown, extra: Record<string, RouteHandler> = {}, session = SESSION) {
  screenWidth(1440);
  const backend = fakeBackend({
    ...signedInRoutes(session),
    "GET /api/auth/sign-in/appearance": () => json(200, answer),
    ...extra,
  });
  const harness = renderConsole("/settings/sign-in-image");
  await screen.findByRole("heading", { level: 1, name: "Sign-in image" });
  // The page draws its screen once the answer is read.
  await screen.findByRole("figure");
  return { ...harness, backend };
}

/** What a file is dropped on: the preview of the screen. */
function dropZone(): HTMLElement {
  const zone = document.querySelector<HTMLElement>("[data-drop-zone]");
  if (zone === null) throw new Error("the page has no drop zone");
  return zone;
}

/** The slot of the preview's slab where the picture (or the drawing) is. */
function slotOfPreview(): HTMLElement {
  const slot = document.querySelector<HTMLElement>("[data-template='auth'] [data-slot='art']");
  if (slot === null) throw new Error("the preview has no picture slot");
  return slot;
}

describe("Settings > Sign-in image", () => {
  it("says everyone sees it, and shows the screen as it is now with the drawing and a way to choose", async () => {
    await pageAt(NOTHING);

    expect(screen.getByText("It is seen before signing in: anyone who opens the console sees it.")).toBeInTheDocument();
    expect(await screen.findByText("Noust's own drawing, as it is now")).toBeInTheDocument();
    expect(slotOfPreview().querySelector("svg[viewBox='0 0 400 230']")).not.toBeNull();
    expect(screen.getByRole("button", { name: "Choose an image" })).toBeEnabled();
    expect(screen.getByText(/PNG, JPEG or WebP, up to 1.0 MB and 4096 pixels on a side/)).toBeInTheDocument();
    // No picture, nothing to fit or describe, nothing to remove.
    expect(screen.queryByRole("radiogroup", { name: "How it fills the space" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Remove the image" })).toBeNull();
  });

  it("shows the picture that is set, in the preview, with its fit and description", async () => {
    await pageAt(SET);

    expect(await screen.findByText("The picture that is set now")).toBeInTheDocument();
    const image = slotOfPreview().querySelector("img");
    expect(image).toHaveAttribute("src", PICTURE.url);
    expect(screen.getByRole("radio", { name: "Fit it whole" })).toBeChecked();
    expect(screen.getByLabelText(/^Description/)).toHaveValue("Acme logo");
    expect(screen.getByRole("button", { name: "Change the image" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Remove the image" })).toBeInTheDocument();
  });

  it("is in the settings list, with the sign-in's own sections", async () => {
    await pageAt(NOTHING);

    const nav = await screen.findByRole("navigation", { name: "Settings sections" });
    expect(within(nav).getByRole("link", { name: "Sign-in image" })).toHaveAttribute("aria-current", "page");
  });

  it("puts a chosen file on the screen at once and sends nothing until Save", async () => {
    const { user, backend } = await pageAt(NOTHING);

    await user.upload(screen.getByLabelText("Choose an image"), png());

    expect(await screen.findByText("logo.png, not saved yet")).toBeInTheDocument();
    await waitFor(() => expect(slotOfPreview().querySelector("img")?.getAttribute("src")).toMatch(/^data:image\/png;base64,/));
    expect(screen.getByText("1 unsaved change")).toBeInTheDocument();
    expect(backend.callsTo("PUT /api/auth/sign-in/appearance")).toHaveLength(0);
  });

  it("changes the preview as the fit and the description change, and counts what is unsaved", async () => {
    const { user } = await pageAt(SET);

    await user.click(screen.getByRole("radio", { name: "Fill the space" }));
    expect(slotOfPreview().querySelector("img")).toHaveClass("object-cover");
    expect(screen.getByText("For a photograph: it covers the space and what does not fit is cropped.")).toBeInTheDocument();
    const description = screen.getByLabelText(/^Description/);
    await user.clear(description);
    await user.type(description, "Acme");

    expect(screen.getByText("2 unsaved changes")).toBeInTheDocument();
  });

  it("saves the file, the fit and the description in one call, and then shows the saved picture", async () => {
    const saved = { image: { ...PICTURE, fit: "cover", alt: "Acme" }, limits: LIMITS };
    const { user, backend } = await pageAt(NOTHING, { "PUT /api/auth/sign-in/appearance": () => json(200, saved) });

    await user.upload(screen.getByLabelText("Choose an image"), png());
    await screen.findByText("logo.png, not saved yet");
    await user.click(screen.getByRole("radio", { name: "Fill the space" }));
    await user.type(screen.getByLabelText(/^Description/), "Acme");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(backend.callsTo("PUT /api/auth/sign-in/appearance")).toHaveLength(1));
    const body = backend.callsTo("PUT /api/auth/sign-in/appearance")[0]?.body as Record<string, unknown>;
    expect(body["fit"]).toBe("cover");
    expect(body["alt"]).toBe("Acme");
    expect(body["image"]).toMatch(/^iVBORw0KGgo/);
    expect(await screen.findByText("The picture that is set now")).toBeInTheDocument();
    expect(slotOfPreview().querySelector("img")).toHaveAttribute("src", PICTURE.url);
  });

  it("changes only the fit and the description without sending the picture again", async () => {
    const { user, backend } = await pageAt(SET, {
      "PUT /api/auth/sign-in/appearance": () => json(200, { image: { ...PICTURE, fit: "cover" }, limits: LIMITS }),
    });

    await user.click(screen.getByRole("radio", { name: "Fill the space" }));
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(backend.callsTo("PUT /api/auth/sign-in/appearance")).toHaveLength(1));
    expect(backend.callsTo("PUT /api/auth/sign-in/appearance")[0]?.body).toEqual({ fit: "cover", alt: "Acme logo" });
  });

  it("discards what is not saved and goes back to the picture that is", async () => {
    const { user, backend } = await pageAt(SET);
    await user.click(screen.getByRole("radio", { name: "Fill the space" }));

    await user.click(screen.getByRole("button", { name: "Discard" }));

    expect(screen.getByRole("radio", { name: "Fit it whole" })).toBeChecked();
    expect(screen.getByText("No unsaved changes")).toBeInTheDocument();
    expect(backend.callsTo("PUT /api/auth/sign-in/appearance")).toHaveLength(0);
  });

  it("says what the server refuses with, in the console's language, and keeps what is there", async () => {
    const refusal = {
      error: "validation_error",
      detail: "An SVG is not accepted as the sign-in image",
      hint: "An SVG is code, and this image is shown to anyone who opens the console before they sign in. Export it as a PNG, JPEG or WebP.",
      fields: { image: "An SVG is not accepted as the sign-in image" },
      output: null,
      coded: { code: "sign_in_image.svg_refused", params: {} },
    };
    const { user } = await pageAt(SET, { "PUT /api/auth/sign-in/appearance": () => json(400, refusal) });
    await user.upload(screen.getByLabelText("Change the image"), png());
    await screen.findByText("logo.png, not saved yet");

    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Could not save the sign-in image")).toBeInTheDocument();
    expect(screen.getByText("An SVG is not accepted as the sign-in image")).toBeInTheDocument();
    await act(async () => {
      await setLocale("es");
    });
    expect(screen.getByText("Un SVG no se admite como imagen del inicio de sesión")).toBeInTheDocument();
    // Nothing was lost: the file is still on the screen to be changed or discarded.
    expect(screen.getByRole("button", { name: "Guardar" })).toBeEnabled();
  });

  it("refuses an SVG or a file that is too large before sending a byte, and says why", async () => {
    const { user, backend } = await pageAt(NOTHING);
    const input = screen.getByLabelText("Choose an image");

    // `accept` is a hint to the file chooser: a dropped or scripted file is judged here too.
    fireEvent.change(input, { target: { files: [png("logo.svg", "image/svg+xml")] } });
    expect(await screen.findByText(/An SVG is not accepted: it is code/)).toBeInTheDocument();

    fireEvent.change(input, { target: { files: [png("huge.png", "image/png", 2 * 1024 * 1024)] } });
    expect(await screen.findByText("That file weighs 2.0 MB and the most is 1.0 MB.")).toBeInTheDocument();
    expect(screen.queryByText(/not saved yet/)).toBeNull();
    expect(backend.callsTo("PUT /api/auth/sign-in/appearance")).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Choose an image" }));
  });

  it("takes a file dropped on the screen of the preview", async () => {
    await pageAt(NOTHING);
    const figure = dropZone();

    fireEvent.dragOver(figure, { dataTransfer: { types: ["Files"], files: [] } });
    expect(await screen.findByText("Drop it to see it on the screen")).toBeInTheDocument();
    fireEvent.drop(figure, { dataTransfer: { types: ["Files"], files: [png("dropped.png")] } });

    expect(await screen.findByText("dropped.png, not saved yet")).toBeInTheDocument();
  });

  it("removes the picture after one question, and the drawing comes back", async () => {
    const { user, backend } = await pageAt(SET, { "DELETE /api/auth/sign-in/appearance": () => json(200, NOTHING) });

    await user.click(screen.getByRole("button", { name: "Remove the image" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Remove the sign-in image?" });
    expect(backend.callsTo("DELETE /api/auth/sign-in/appearance")).toHaveLength(0);
    await user.click(within(dialog).getByRole("button", { name: "Remove the image" }));

    await waitFor(() => expect(backend.callsTo("DELETE /api/auth/sign-in/appearance")).toHaveLength(1));
    expect(await screen.findByText("Noust's own drawing, as it is now")).toBeInTheDocument();
    expect(slotOfPreview().querySelector("svg[viewBox='0 0 400 230']")).not.toBeNull();
  });

  it("is read-only without the permission: the action is turned off with a lock and says why when pressed", async () => {
    const { user } = await pageAt(SET, {}, READER);

    expect(screen.getByText("You can see this page, not change it. Whoever looks after the console's security does.")).toBeInTheDocument();
    const locked = screen.getByRole("button", { name: "Change the image" });
    expect(locked).toHaveAttribute("aria-disabled", "true");
    // The page is read: the picture, its fit and its description are said, and nothing can be edited.
    expect(slotOfPreview().querySelector("img")).toHaveAttribute("src", PICTURE.url);
    expect(screen.getByText("Fit it whole")).toBeInTheDocument();
    expect(screen.getByText("Acme logo")).toBeInTheDocument();
    expect(screen.queryByRole("radiogroup")).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("button", { name: "Remove the image" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    expect(screen.queryByLabelText("Change the image")).not.toBeNull();

    await user.click(locked);

    expect(await screen.findByText("Why you cannot change it")).toBeInTheDocument();
    expect(screen.getByText("security.manage")).toBeInTheDocument();
  });

  it("does not take a dropped file without the permission", async () => {
    await pageAt(NOTHING, {}, READER);

    fireEvent.drop(dropZone(), { dataTransfer: { types: ["Files"], files: [png()] } });

    expect(screen.queryByText(/not saved yet/)).toBeNull();
  });

  it("says when the picture cannot be read, and tries again", async () => {
    let attempts = 0;
    const { user } = await (async () => {
      screenWidth(1440);
      fakeBackend({
        ...signedInRoutes(),
        "GET /api/auth/sign-in/appearance": () => (++attempts === 1 ? json(500, { error: "internal_error", detail: "It broke" }) : json(200, NOTHING)),
      });
      const harness = renderConsole("/settings/sign-in-image");
      await screen.findByRole("heading", { level: 1, name: "Sign-in image" });
      return harness;
    })();

    expect(await screen.findByText("Could not read the sign-in image")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /Try again/ }));

    expect(await screen.findByText("Noust's own drawing, as it is now")).toBeInTheDocument();
  });

  it("has no accessibility violations, with and without a picture, read-only or not, in either language", async () => {
    await pageAt(SET);
    await screen.findByText("The picture that is set now");
    await expectNoAxeViolations(screen.getByRole("main"));
    await act(async () => {
      await setLocale("es");
    });
    expect(screen.getByRole("heading", { level: 1, name: "Imagen del inicio de sesión" })).toBeInTheDocument();
    expect(screen.getByText("Se ve antes de entrar: la ve cualquiera que abra la consola.")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("has no accessibility violations read-only, nor with nothing set", async () => {
    await pageAt(NOTHING, {}, READER);
    await screen.findByText("Noust's own drawing, as it is now");
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});
