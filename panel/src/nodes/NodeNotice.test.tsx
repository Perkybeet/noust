import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderConsole } from "../test/console";
import { NODES, fakeBackend, json, onNode, signedInRoutes } from "../test/fakes";

/**
 * One global notice at a time, by priority (docs/DESIGN.md, NV-16): the chrome never stacks banners
 * above a page. What the node lets this central do outranks that it runs another Noust.
 */

function fleet(access: "read" | "deploy" | "admin", nodes: readonly object[] = NODES) {
  fakeBackend({
    ...signedInRoutes(),
    ...onNode("web-2", signedInRoutes()),
    ...onNode("db-1", signedInRoutes()),
    "GET /api/nodes": () => json(200, { items: nodes }),
    "GET /api/fleet/servers": () =>
      json(200, { items: nodes.map((node) => ({ node: (node as { name: string }).name, local: false, href: "/", access: { level: access, host_access: false } })) }),
  });
}

describe("the notice above a node's pages", () => {
  it("says what the node lets this central do, and only that, when the node also runs another Noust", async () => {
    // db-1 runs 1.9.0 against this server's 2.0.0, and lets the central only read.
    fleet("read");
    renderConsole("/n/db-1/apps");
    const notice = await screen.findByRole("region", { name: "About db-1" });
    expect(notice.querySelectorAll('[data-variant="banner"]')).toHaveLength(1);
    expect(notice).toHaveTextContent("db-1 lets this central only read");
    expect(notice).not.toHaveTextContent("runs Noust 1.9.0");
  });

  it("says that the node runs another Noust when it lets the central do everything", async () => {
    fleet("admin");
    renderConsole("/n/db-1/apps");
    const notice = await screen.findByRole("region", { name: "About db-1" });
    expect(notice.querySelectorAll('[data-variant="banner"]')).toHaveLength(1);
    expect(notice).toHaveTextContent("db-1 runs Noust 1.9.0, older than this server's 2.0.0.");
  });

  it("says nothing on a node on the same version that lets the central do everything", async () => {
    fleet("admin");
    renderConsole("/n/web-2/apps");
    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByRole("region", { name: "About web-2" })).toBeNull();
  });
});
