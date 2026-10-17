import { act, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { json } from "../../../test/fakes";
import { DOMAIN, READY_TO_SPREAD, renderSettings } from "./testKit";

const BLOCKED = {
  domain: DOMAIN,
  ready: false,
  by_image: false,
  findings: [
    {
      code: "persistent_paths",
      severity: "blocks",
      message: "It keeps files on this server (the folders it writes to between releases); a second server would start with them empty.",
      fix: "Keep them in object storage or a mount all the servers share.",
      items: ["storage/uploads", "public/avatars"],
    },
    {
      code: "scheduled_tasks",
      severity: "warns",
      message: "It has scheduled tasks, which would then run once on every server.",
      fix: "Keep them on one server, or make them safe to run twice.",
      items: ["nightly"],
    },
  ],
};

function exportPage(replication: object = READY_TO_SPREAD) {
  return renderSettings("/export", "Export", undefined, {
    [`GET /api/apps/${DOMAIN}/replication`]: () => json(200, replication),
  });
}

describe("whether the app could run on several servers", () => {
  it("says nothing stops it", async () => {
    await exportPage();

    expect(await screen.findByRole("heading", { name: "On several servers" })).toBeInTheDocument();
    expect(await screen.findByText("Nothing here stops it: a second server would serve the same.")).toBeInTheDocument();
    expect(screen.getByText("Nothing in its records speaks against it.")).toBeInTheDocument();
  });

  it("lists what blocks and what only warns, each with the names it is about and what to do", async () => {
    await exportPage(BLOCKED);

    expect(await screen.findByText("As it is, a second server would not serve the same: something here blocks it.")).toBeInTheDocument();
    const list = screen.getByRole("list", { name: "What a second server would get wrong or do twice" });
    const [blocks, warns] = within(list).getAllByRole("listitem");
    if (blocks === undefined || warns === undefined) throw new Error("expected two findings");
    expect(within(blocks).getByText("Blocks")).toBeInTheDocument();
    expect(within(blocks).getByText("storage/uploads")).toBeInTheDocument();
    expect(within(blocks).getByText("Keep them in object storage or a mount all the servers share.")).toBeInTheDocument();
    expect(within(warns).getByText("Warns")).toBeInTheDocument();
    expect(within(warns).getByText("nightly")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("says it runs a registry image, which a rollout can name by tag", async () => {
    await exportPage({ ...READY_TO_SPREAD, by_image: true });

    expect(await screen.findByText("It runs a registry image, so a rollout can name the tag and no server builds it.")).toBeInTheDocument();
  });

  it("speaks Spanish", async () => {
    await act(() => setLocale("es"));
    await renderSettings("/export", "Exportar", undefined, {
      [`GET /api/apps/${DOMAIN}/replication`]: () => json(200, BLOCKED),
    });

    expect(await screen.findByRole("heading", { name: "En varios servidores" })).toBeInTheDocument();
    expect(screen.getByText("Impide")).toBeInTheDocument();
    expect(screen.getByText("Avisa")).toBeInTheDocument();
  });
});
