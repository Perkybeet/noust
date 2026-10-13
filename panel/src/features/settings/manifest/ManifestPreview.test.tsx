import { act, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { screenWidth } from "../../app/testRoutes";

const MANIFEST = "version: 1\napplications:\n  - domain: shop.example.com\n    source: https://github.com/acme/shop\n    branch: main\n";

const PLAN = {
  empty: false,
  actions: [
    { kind: "app.create", target: "shop.example.com", summary: "create shop.example.com from https://github.com/acme/shop (main)", elevated: false, unverified: false },
    { kind: "app.env", target: "shop.example.com", summary: "set 2 variables (names only)", elevated: true, unverified: true },
  ],
  counts: { "app.create": 1, "app.env": 1 },
  warnings: ["shop.example.com: the live health check differs and is not reconciled"],
  unmanaged: ["blog.example.com"],
};

async function pageAt(extra: Record<string, RouteHandler> = {}) {
  screenWidth(1440);
  const backend = fakeBackend({
    ...signedInRoutes(),
    "POST /api/manifest/plan": () => json(200, PLAN),
    ...extra,
  });
  const harness = renderConsole("/settings/manifest");
  await screen.findByRole("heading", { level: 1, name: "Settings" });
  return { ...harness, backend };
}

describe("Settings > Manifest", () => {
  it("previews what applying a manifest would change, in order, and what it leaves alone, and passes axe", { timeout: 30_000 }, async () => {
    const { backend, user } = await pageAt();
    const button = await screen.findByRole("button", { name: "Preview changes" });
    // Nothing to preview until something is pasted.
    expect(button).toBeDisabled();
    await user.click(screen.getByLabelText(/^Manifest/));
    await user.paste(MANIFEST);
    await user.click(button);

    const table = await screen.findByRole("region", { name: "Changes a manifest would make" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    const [first, second] = rows;
    if (!first || !second) throw new Error("missing rows");
    expect(within(first).getByText("Create application")).toBeInTheDocument();
    expect(within(second).getByText("Set variables")).toBeInTheDocument();
    expect(within(second).getByText("Needs sudo mode")).toBeInTheDocument();
    expect(within(second).getByText("Not checked here")).toBeInTheDocument();
    expect(screen.getByText("2 changes, in this order")).toBeInTheDocument();
    expect(screen.getByText("shop.example.com: the live health check differs and is not reconciled")).toBeInTheDocument();
    expect(screen.getByText("Applying never deletes them: blog.example.com")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/manifest/plan")[0]?.body).toEqual({ manifest: MANIFEST });
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("says when there is nothing to change", { timeout: 30_000 }, async () => {
    const { user } = await pageAt({ "POST /api/manifest/plan": () => json(200, { ...PLAN, actions: [], empty: true, counts: {}, warnings: [], unmanaged: [] }) });
    await user.click(await screen.findByLabelText(/^Manifest/));
    await user.paste(MANIFEST);
    await user.click(screen.getByRole("button", { name: "Preview changes" }));
    expect(await screen.findByText("Nothing to change")).toBeInTheDocument();
    expect(screen.getByText("This server already matches the manifest.")).toBeInTheDocument();
  });

  it("shows the server's refusal of the document as it wrote it", { timeout: 30_000 }, async () => {
    const { user } = await pageAt({
      "POST /api/manifest/plan": () => problem(422, "manifesterror", "applications[0].memory_mb: unknown key", { hint: "Did you mean applications[0].limits.memory_mb?" }),
    });
    await user.click(await screen.findByLabelText(/^Manifest/));
    await user.paste(MANIFEST);
    await user.click(screen.getByRole("button", { name: "Preview changes" }));
    expect(await screen.findByText("applications[0].memory_mb: unknown key")).toBeInTheDocument();
    expect(screen.getByText("Did you mean applications[0].limits.memory_mb?")).toBeInTheDocument();
  });

  it("is in the settings navigation, as the server's own section", async () => {
    await pageAt();
    const nav = await screen.findByRole("navigation", { name: "Settings sections" });
    expect(within(nav).getByRole("link", { name: "Manifest" })).toHaveAttribute("aria-current", "page");
  });

  it("speaks Spanish", { timeout: 30_000 }, async () => {
    await pageAt();
    await act(() => setLocale("es"));
    expect(await screen.findByRole("button", { name: "Ver los cambios" })).toBeInTheDocument();
    await act(() => setLocale("en"));
  });
});
