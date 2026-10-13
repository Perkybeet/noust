import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { downloadText } from "../../../lib/clipboard";
import { expectNoAxeViolations } from "../../../test/axe";
import { json } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { DOMAIN, ELEVATED, RELEASE_APP, part, renderSettings } from "./testKit";

// The real implementation still runs (a blob and an anchor), wrapped so the exact file name and
// text handed to it can be asserted.
vi.mock("../../../lib/clipboard", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../lib/clipboard")>();
  return { ...actual, downloadText: vi.fn(actual.downloadText) };
});

const DECLARATION = "app:\n  health:\n    path: /up\n";

const SAFE_PLAN = {
  domain: DOMAIN,
  changes: [{ section: "app", description: "Health check path becomes /up", root_equivalent: false }],
  unchanged: ["env"],
  notes: ["Variables are never removed."],
  confirm: [] as string[],
  prune: false,
};

const RUNNING_PLAN = {
  ...SAFE_PLAN,
  changes: [
    ...SAFE_PLAN.changes,
    { section: "build", description: "Build command becomes npm run build:prod", root_equivalent: true },
  ],
  confirm: ["the build command"],
};

function exportPage(routes: Record<string, RouteHandler> = {}) {
  return renderSettings("/export", "Export", RELEASE_APP, {
    "GET /api/auth/session": () => json(200, ELEVATED),
    ...routes,
  });
}

const card = () => part("Declaration file");

describe("the declaration file", () => {
  it("is downloaded as noust.yaml", { timeout: 40_000 }, async () => {
    Object.assign(URL, { createObjectURL: vi.fn(() => "blob:declaration"), revokeObjectURL: vi.fn() });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    const { user, container } = await exportPage({
      [`GET /api/apps/${DOMAIN}/declaration`]: () => json(200, { domain: DOMAIN, declaration: DECLARATION }),
    });
    await screen.findByRole("heading", { name: "Declaration file" });
    await user.click(within(card()).getByRole("button", { name: "Download the declaration" }));

    await waitFor(() => {
      expect(downloadText).toHaveBeenCalledWith("noust.yaml", DECLARATION, "text/yaml");
    });
    expect(within(card()).getByText(/Secret variables are never in it/)).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("shows what a declaration would change, and changes nothing while checking", { timeout: 40_000 }, async () => {
    const { user, backend } = await exportPage({
      [`POST /api/apps/${DOMAIN}/apply`]: () => json(200, { plan: SAFE_PLAN, applied: false, steps: [] }),
    });
    await screen.findByRole("heading", { name: "Declaration file" });
    await user.click(within(card()).getByRole("button", { name: "Apply a declaration…" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("button", { name: "Check what it changes" })).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Apply it" })).toBeDisabled();

    await user.click(within(dialog).getByLabelText("Declaration (YAML)"));
    await user.paste(DECLARATION);
    await user.click(within(dialog).getByRole("button", { name: "Check what it changes" }));

    expect(await within(dialog).findByText("Health check path becomes /up")).toBeInTheDocument();
    expect(within(dialog).getByText(/Already as declared: env/)).toBeInTheDocument();
    expect(within(dialog).getByText("Variables are never removed.")).toBeInTheDocument();
    expect(within(dialog).queryByText("Runs commands")).not.toBeInTheDocument();
    expect(backend.callsTo(`POST /api/apps/${DOMAIN}/apply`)).toHaveLength(1);
    expect(backend.callsTo(`POST /api/apps/${DOMAIN}/apply`)[0]?.body).toEqual({ declaration: DECLARATION, check: true, confirm: false, prune: false });
  });

  it("asks for the box to be ticked before it applies what runs commands", { timeout: 40_000 }, async () => {
    let applied = false;
    const { user, backend } = await exportPage({
      [`POST /api/apps/${DOMAIN}/apply`]: (call) => {
        const body = call.body as { check: boolean };
        if (body.check) return json(200, { plan: RUNNING_PLAN, applied: false, steps: [] });
        applied = true;
        return json(200, {
          plan: RUNNING_PLAN,
          applied: true,
          steps: [
            { part: "app", applied: true, detail: "" },
            { part: "build", applied: false, detail: "The build block needs a repository." },
          ],
        });
      },
    });
    await screen.findByRole("heading", { name: "Declaration file" });
    await user.click(within(card()).getByRole("button", { name: "Apply a declaration…" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByLabelText("Declaration (YAML)"));
    await user.paste(DECLARATION);
    await user.click(within(dialog).getByRole("button", { name: "Check what it changes" }));

    expect(await within(dialog).findByText("Build command becomes npm run build:prod")).toBeInTheDocument();
    expect(within(dialog).getByText("Runs commands")).toBeInTheDocument();
    const apply = within(dialog).getByRole("button", { name: "Apply it" });
    expect(apply).toBeDisabled();

    await user.click(within(dialog).getByRole("checkbox", { name: /I have read what it runs/ }));
    expect(apply).toBeEnabled();
    await user.click(apply);

    await waitFor(() => expect(applied).toBe(true));
    const sent = backend.callsTo(`POST /api/apps/${DOMAIN}/apply`);
    expect(sent[1]?.body).toEqual({ declaration: DECLARATION, check: false, confirm: true, prune: false });
    // What was left undone is said, not hidden.
    expect(await within(dialog).findByText("Not applied: build")).toBeInTheDocument();
    expect(within(dialog).getByText("The build block needs a repository.")).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Apply it" })).not.toBeInTheDocument();
  });

  it("forgets a plan when the text changes, so it never applies a file that was not checked", { timeout: 40_000 }, async () => {
    const { user } = await exportPage({
      [`POST /api/apps/${DOMAIN}/apply`]: () => json(200, { plan: SAFE_PLAN, applied: false, steps: [] }),
    });
    await screen.findByRole("heading", { name: "Declaration file" });
    await user.click(within(card()).getByRole("button", { name: "Apply a declaration…" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByLabelText("Declaration (YAML)"));
    await user.paste(DECLARATION);
    await user.click(within(dialog).getByRole("button", { name: "Check what it changes" }));
    await within(dialog).findByText("Health check path becomes /up");
    expect(within(dialog).getByRole("button", { name: "Apply it" })).toBeEnabled();

    await user.type(within(dialog).getByLabelText("Declaration (YAML)"), "x");

    expect(within(dialog).queryByText("Health check path becomes /up")).not.toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Apply it" })).toBeDisabled();
  });

  it("shows the server's refusal beside the text, verbatim", { timeout: 40_000 }, async () => {
    const { user } = await exportPage({
      [`POST /api/apps/${DOMAIN}/apply`]: () =>
        json(400, {
          error: "validation_error",
          detail: "declaration: unknown key 'colour'",
          hint: "Allowed keys: app, env, domains, tasks.",
          fields: { declaration: "unknown key 'colour'" },
        }),
    });
    await screen.findByRole("heading", { name: "Declaration file" });
    await user.click(within(card()).getByRole("button", { name: "Apply a declaration…" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByLabelText("Declaration (YAML)"));
    await user.paste("colour: red\n");
    await user.click(within(dialog).getByRole("button", { name: "Check what it changes" }));

    expect(await within(dialog).findByText(/unknown key 'colour'/)).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Declaration (YAML)")).toHaveValue("colour: red\n");
  });
});
