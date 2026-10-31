/**
 * Typing an address into "What to deploy" letter by letter. The first letters of `https://` are also
 * words, and the box used to show the catalog for them and take it away at `https://g`: the page
 * jumped twice, and every key took longer to land while the catalog was drawn.
 */

import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderConsole } from "../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../test/fakes";
import { RECIPES } from "./testFixtures";

type View = "popular" | "results" | "source";

/** What the page under the box shows: the popular recipes, what the catalog found for words, or the source's own. */
function view(): View {
  if (screen.queryByRole("list", { name: "Popular recipes" }) !== null) return "popular";
  if (screen.queryByText("noust recipe list") !== null) return "results";
  return "source";
}

function wizard() {
  fakeBackend({
    ...signedInRoutes(),
    "GET /api/apps": () => json(200, { total: 0, apps: [] }),
    "GET /api/config/webserver": () => json(200, { webserver: "nginx" }),
    "GET /api/apps/types": () => json(200, { types: [{ type: "nodejs", name: "Node.js", default_port: 3000 }] }),
    "GET /api/recipes": () => json(200, RECIPES),
    "GET /api/domains/temporary": () => json(200, { domain: "api.203-0-113-10.sslip.io", address: "203.0.113.10" }),
    "POST /api/apps/inspect": () => new Promise<Response>(() => undefined),
  });
  return renderConsole("/apps/new");
}

/** The views the page goes through as `text` is typed one letter at a time. */
async function typeOut(user: ReturnType<typeof renderConsole>["user"], text: string): Promise<View[]> {
  await user.click(screen.getByLabelText("What to deploy"));
  const seen: View[] = [];
  for (const letter of text) {
    await user.keyboard(letter);
    seen.push(view());
  }
  return seen;
}

describe("an address typed letter by letter", () => {
  it.each(["https://github.com/acme/api.git", "git@github.com:acme/api.git", "ssh://deploy@git.example.com/acme/api.git"])(
    "never shows the catalog on the way to %s, and changes the page once",
    { timeout: 30_000 },
    async (address) => {
      const { user } = wizard();
      await screen.findByRole("heading", { level: 1, name: "New application" });
      await screen.findByRole("list", { name: "Popular recipes" });

      const seen = await typeOut(user, address);

      expect(seen).not.toContain("results");
      // The popular recipes stay under the first letters, then the source's own page takes their place, and stays.
      const first = seen.indexOf("source");
      expect(first).toBeGreaterThan(0);
      expect(seen.slice(0, first).every((shown) => shown === "popular")).toBe(true);
      expect(seen.slice(first).every((shown) => shown === "source")).toBe(true);
    },
  );

  it("still looks in the catalog for words, from the first letter that cannot begin an address", { timeout: 30_000 }, async () => {
    const { user } = wizard();
    await screen.findByRole("heading", { level: 1, name: "New application" });
    await screen.findByRole("list", { name: "Popular recipes" });

    const seen = await typeOut(user, "wordp");

    // `w` and `wo` are no beginning of an address: nothing is held back for them.
    expect(seen.every((shown) => shown === "results")).toBe(true);
  });
});
