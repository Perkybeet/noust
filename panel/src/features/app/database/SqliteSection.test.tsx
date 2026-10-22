import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { ENGINES } from "../../databases/testFixtures";
import { TAB_DOMAIN, appRoutes, screenWidth } from "../testRoutes";

const FILE = { path: "data/app.db", size: 5_242_880, wal_bytes: 0, modified: 1_790_000_000, journal_mode: "rollback", page_size: 4096, pages: 1280 };
const LIVE = { path: "storage/live.sqlite", size: 40_960, wal_bytes: 8_192, modified: 1_790_000_100, journal_mode: "wal", page_size: 4096, pages: 10 };
const CHECK = `POST /api/apps/${TAB_DOMAIN}/sqlite/check`;

function listing(files: unknown[], truncated = false) {
  return () => json(200, { domain: TAB_DOMAIN, files, searched: ["."], truncated });
}

async function tabAt(files: unknown[], extra: Record<string, RouteHandler> = {}, truncated = false) {
  screenWidth(1440);
  const backend = fakeBackend(
    appRoutes(
      {},
      {
        [`GET /api/apps/${TAB_DOMAIN}/databases`]: () => json(200, { domain: TAB_DOMAIN, databases: [] }),
        "GET /api/databases/engines": () => json(200, { engines: ENGINES }),
        [`GET /api/apps/${TAB_DOMAIN}/sqlite`]: listing(files, truncated),
        ...extra,
      },
    ),
  );
  const harness = renderConsole(`/apps/${TAB_DOMAIN}/database`);
  await screen.findByRole("heading", { level: 1, name: TAB_DOMAIN });
  return { ...harness, backend };
}

describe("the SQLite files in an application's Database tab", { timeout: 40_000 }, () => {
  it("is not there for an application that keeps none", async () => {
    await tabAt([]);

    await screen.findByRole("heading", { level: 2, name: "No database yet" });
    expect(screen.queryByRole("heading", { name: "SQLite files" })).not.toBeInTheDocument();
  });

  it("is not there, and says nothing, on a server that does not have the endpoint yet", async () => {
    await tabAt([], { [`GET /api/apps/${TAB_DOMAIN}/sqlite`]: () => json(404, { error: "not_found", detail: "Not Found", fields: {}, output: null }) });

    await screen.findByRole("heading", { level: 2, name: "No database yet" });
    expect(screen.queryByText("Could not look for SQLite files")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "SQLite files" })).not.toBeInTheDocument();
  });

  it("lists each file with what its header says", async () => {
    await tabAt([FILE, LIVE]);

    const plain = (await screen.findByText("data/app.db")).closest("li");
    const live = (await screen.findByText("storage/live.sqlite")).closest("li");
    if (!plain || !live) throw new Error("no card");

    expect(within(plain).getByText(/^5(\.0+)? MB$/)).toBeInTheDocument();
    expect(within(plain).getByText("Rollback journal")).toBeInTheDocument();
    expect(within(plain).queryByText("Committed, waiting in the log")).not.toBeInTheDocument();
    expect(within(live).getByText("WAL (write-ahead log)")).toBeInTheDocument();
    expect(within(live).getByText("Committed, waiting in the log")).toBeInTheDocument();
    expect(screen.getByText("noust db sqlite list " + TAB_DOMAIN)).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("says the search stopped when it did", async () => {
    await tabAt([FILE], {}, true);

    expect(await screen.findByText(/The search stopped at its limit/)).toBeInTheDocument();
  });

  it("checks a file and says it is sound, asking for the quick check or the thorough one", async () => {
    const { user, backend } = await tabAt([FILE], {
      [CHECK]: (call) => json(200, { path: FILE.path, ok: true, complete: true, full: (call.body as { full: boolean }).full, problems: [], seconds: 0.25 }),
    });

    const card = (await screen.findByText("data/app.db")).closest("li");
    if (!card) throw new Error("no card");
    await user.click(within(card).getByRole("button", { name: "Check" }));

    expect(await within(card).findByText(/Nothing wrong found in 0.25 s/)).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Check every index" }));
    await waitFor(() => expect(backend.callsTo(CHECK)).toHaveLength(2));
    expect(backend.callsTo(CHECK)[0]?.body).toEqual({ path: "data/app.db", full: false });
    expect(backend.callsTo(CHECK)[1]?.body).toEqual({ path: "data/app.db", full: true });
  });

  it("shows what SQLite found, in its own words", async () => {
    const { user } = await tabAt([FILE], {
      [CHECK]: () =>
        json(200, { path: FILE.path, ok: false, complete: true, full: false, problems: ["*** in database main ***", "Page 4: btreeInitPage() returns error code 11"], seconds: 0.1 }),
    });

    const card = (await screen.findByText("data/app.db")).closest("li");
    if (!card) throw new Error("no card");
    await user.click(within(card).getByRole("button", { name: "Check" }));

    const alert = await within(card).findByRole("alert");
    expect(within(alert).getByText("SQLite found problems in data/app.db")).toBeInTheDocument();
    expect(alert.textContent).toContain("Page 4: btreeInitPage() returns error code 11");
    await expectNoAxeViolations(card);
  });

  it("says a check that ran out of time is not an all clear", async () => {
    const { user } = await tabAt([FILE], {
      [CHECK]: () => json(200, { path: FILE.path, ok: true, complete: false, full: true, problems: [], seconds: 30 }),
    });

    const card = (await screen.findByText("data/app.db")).closest("li");
    if (!card) throw new Error("no card");
    await user.click(within(card).getByRole("button", { name: "Check every index" }));

    expect(await within(card).findByText(/The check stopped before the end/)).toBeInTheDocument();
    expect(within(card).queryByText(/Nothing wrong found in/)).not.toBeInTheDocument();
  });
});
