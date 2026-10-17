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

const RUNNING = "app:\n  health:\n    path: /\n  limits:\n    memory_max_mb: 512\nenv:\n  LOG_LEVEL: info\n";
const DRAFT = "app:\n  health:\n    path: /up\n  limits:\n    memory_max_mb: 512\nenv:\n  LOG_LEVEL: debug\n";

const TWO_CHANGES = {
  ...SAFE_PLAN,
  changes: [
    { section: "health", description: "Health check path becomes /up", root_equivalent: false },
    { section: "env", description: "LOG_LEVEL becomes debug", root_equivalent: false },
  ],
  unchanged: ["limits"],
};
const ONE_CHANGE = { ...TWO_CHANGES, changes: [TWO_CHANGES.changes[1]], unchanged: ["limits", "health"] };

describe("reviewing a set of changes", () => {
  const running = { [`GET /api/apps/${DOMAIN}/declaration`]: () => json(200, { domain: DOMAIN, declaration: RUNNING }) };

  it("starts a draft from what runs now", { timeout: 40_000 }, async () => {
    const { user } = await exportPage(running);
    await screen.findByRole("heading", { name: "Declaration file" });
    await user.click(within(card()).getByRole("button", { name: "Apply a declaration…" }));
    const dialog = await screen.findByRole("dialog");

    await user.click(within(dialog).getByRole("button", { name: "Start from what runs now" }));

    await waitFor(() => expect(within(dialog).getByLabelText("Declaration (YAML)")).toHaveValue(RUNNING));
  });

  it("says what the set covers and what it leaves to other pages", { timeout: 40_000 }, async () => {
    const { user } = await exportPage(running);
    await screen.findByRole("heading", { name: "Declaration file" });
    await user.click(within(card()).getByRole("button", { name: "Apply a declaration…" }));
    const dialog = await screen.findByRole("dialog");

    expect(within(dialog).getByText("What this set covers")).toBeInTheDocument();
    expect(within(dialog).getByText(/put back if it fails/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Each part is applied on its own/)).toBeInTheDocument();
  });

  it("shows the lines that change against what runs, block by block", { timeout: 40_000 }, async () => {
    const { user, container } = await exportPage({
      ...running,
      [`POST /api/apps/${DOMAIN}/apply`]: () => json(200, { plan: TWO_CHANGES, applied: false, steps: [] }),
    });
    await screen.findByRole("heading", { name: "Declaration file" });
    await user.click(within(card()).getByRole("button", { name: "Apply a declaration…" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByLabelText("Declaration (YAML)"));
    await user.paste(DRAFT);
    await user.click(within(dialog).getByRole("button", { name: "Check what it changes" }));

    expect(await within(dialog).findByText("2 blocks change")).toBeInTheDocument();
    const blocks = within(dialog).getByRole("list", { name: "Blocks of the declaration that change" });
    const app = within(blocks).getByRole("group", { name: /Lines of How it runs/ });
    // Only the line that differs is marked, and by its text as well as its ground.
    expect(within(app).getByText("path: /up", { exact: false })).toBeInTheDocument();
    expect(within(app).getAllByText("Removed:")).toHaveLength(1);
    expect(within(app).getAllByText("Added:")).toHaveLength(1);
    expect(within(blocks).getByRole("group", { name: /Lines of Variables/ })).toBeInTheDocument();
    // What each change does is the plan, said once.
    expect(within(dialog).getAllByText("Health check path becomes /up")).toHaveLength(1);
    await expectNoAxeViolations(container);
  });

  it("shows no review for a draft that says what runs", { timeout: 40_000 }, async () => {
    const { user } = await exportPage({
      ...running,
      [`POST /api/apps/${DOMAIN}/apply`]: () => json(200, { plan: { ...SAFE_PLAN, changes: [] }, applied: false, steps: [] }),
    });
    await screen.findByRole("heading", { name: "Declaration file" });
    await user.click(within(card()).getByRole("button", { name: "Apply a declaration…" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByLabelText("Declaration (YAML)"));
    await user.paste(RUNNING);
    await user.click(within(dialog).getByRole("button", { name: "Check what it changes" }));

    expect(await within(dialog).findByText(/Nothing: the application already is as declared/)).toBeInTheDocument();
    expect(within(dialog).queryByRole("list", { name: "Blocks of the declaration that change" })).not.toBeInTheDocument();
  });

  it("puts one block back to what runs and checks the rest again", { timeout: 40_000 }, async () => {
    const { user, backend } = await exportPage({
      ...running,
      [`POST /api/apps/${DOMAIN}/apply`]: (call) => {
        const body = call.body as { declaration: string };
        return json(200, { plan: body.declaration.includes("/up") ? TWO_CHANGES : ONE_CHANGE, applied: false, steps: [] });
      },
    });
    await screen.findByRole("heading", { name: "Declaration file" });
    await user.click(within(card()).getByRole("button", { name: "Apply a declaration…" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByLabelText("Declaration (YAML)"));
    await user.paste(DRAFT);
    await user.click(within(dialog).getByRole("button", { name: "Check what it changes" }));
    await within(dialog).findByText("2 blocks change");

    await user.click(within(dialog).getByRole("button", { name: /^Put How it runs .* back$/ }));

    // The text is the draft with that block as it was, the other kept, and the plan is of that text.
    await waitFor(() => expect(within(dialog).getByLabelText("Declaration (YAML)")).toHaveValue(DRAFT.replace("path: /up", "path: /")));
    expect(await within(dialog).findByText("1 block changes")).toBeInTheDocument();
    expect(within(dialog).queryByText("Health check path becomes /up")).not.toBeInTheDocument();
    const checks = backend.callsTo(`POST /api/apps/${DOMAIN}/apply`);
    expect(checks).toHaveLength(2);
    expect(checks[1]?.body).toEqual({ declaration: DRAFT.replace("path: /up", "path: /"), check: true, confirm: false, prune: false });
    // Nothing was applied by going back.
    expect(checks.every((call) => (call.body as { check: boolean }).check)).toBe(true);
  });

  it("asks again for the agreement once a block is put back", { timeout: 40_000 }, async () => {
    const runs = { ...RUNNING_PLAN, changes: [...RUNNING_PLAN.changes, { section: "env", description: "LOG_LEVEL becomes debug", root_equivalent: false }] };
    const { user } = await exportPage({
      ...running,
      [`POST /api/apps/${DOMAIN}/apply`]: () => json(200, { plan: runs, applied: false, steps: [] }),
    });
    await screen.findByRole("heading", { name: "Declaration file" });
    await user.click(within(card()).getByRole("button", { name: "Apply a declaration…" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByLabelText("Declaration (YAML)"));
    await user.paste(`${DRAFT}build:\n  command: npm run build:prod\n`);
    await user.click(within(dialog).getByRole("button", { name: "Check what it changes" }));
    await user.click(await within(dialog).findByRole("checkbox", { name: /I have read what it runs/ }));
    expect(within(dialog).getByRole("button", { name: "Apply it" })).toBeEnabled();

    await user.click(within(dialog).getByRole("button", { name: /^Put Variables back$/ }));

    // A new plan is a new reading: the box is cleared until it is read again.
    await waitFor(() => expect(within(dialog).getByRole("checkbox", { name: /I have read what it runs/ })).not.toBeChecked());
    expect(within(dialog).getByRole("button", { name: "Apply it" })).toBeDisabled();
  });
});
