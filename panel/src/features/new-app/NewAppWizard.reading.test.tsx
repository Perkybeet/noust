/**
 * The read the step starts by itself (a repository chosen from the list, an address pasted or left) is a
 * convenience: what it found is on screen before Continue is pressed. It must never take a key or the focus
 * from the person who goes on typing. It used to disable the box, the repository search and Change repository
 * from 80ms after the choice until GitHub answered, so a link typed straight after choosing a repository lost
 * letters at random (on a loaded machine, ahead of the point where the page gave way to the typed link), and a
 * Change repository pressed in that window opened a search that could not be typed in.
 *
 * Every test here waits for the read to be under way (the request is recorded) and only then acts, so none of
 * them depends on how fast the machine is: before the fix they fail every time, not by turns.
 */

import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { GitHubRepository, GitHubStatus } from "../../api/queries/github";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { RECIPES } from "./testFixtures";

const STATUS: GitHubStatus = {
  configured: true,
  warnings: [],
  app_id: 424242,
  slug: "noust-web-01",
  name: "Noust web-01",
  owner: "acme",
  html_url: "https://github.com/apps/noust-web-01",
  settings_url: "https://github.com/organizations/acme/settings/apps/noust-web-01",
  install_url: "https://github.com/apps/noust-web-01/installations/new",
  installations: [{ installation_id: 7001, account: "acme", account_type: "Organization", repository_selection: "selected", settings_url: null }],
  hooks_url: "https://hooks.example.com/hooks/github",
  hooks_active: true,
  host: "github.com",
};

const REPOSITORIES: GitHubRepository[] = [
  { full_name: "acme/api", private: true, default_branch: "main", clone_url: "https://github.com/acme/api.git", source: "github:acme/api", installation_id: 7001 },
  { full_name: "acme/blog", private: false, default_branch: "main", clone_url: "https://github.com/acme/blog.git", source: "github:acme/blog", installation_id: 7001 },
];

const ADDRESS = "https://gitlab.com/acme/api.git";

/** How many reads of a source were asked of the server, and how many of them the console gave up on. */
interface Reads {
  started: () => number;
  aborted: () => number;
}

/**
 * The reads of a source never end here, as a slow clone does not: the request stays out until the console aborts it,
 * and that is counted.
 */
function slowReads(): Reads {
  let started = 0;
  let aborted = 0;
  const inner = globalThis.fetch;
  vi.stubGlobal("fetch", (input: RequestInfo | URL, init: RequestInit = {}) => {
    const href = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    if (!href.endsWith("/api/apps/inspect")) return inner(input, init);
    started += 1;
    return new Promise<Response>((_, reject) => {
      init.signal?.addEventListener("abort", () => {
        aborted += 1;
        reject(new DOMException("The operation was aborted.", "AbortError"));
      });
    });
  });
  return { started: () => started, aborted: () => aborted };
}

/** The wizard on a server whose GitHub App is connected. */
function wizard(extra: Record<string, RouteHandler> = {}) {
  fakeBackend({
    ...signedInRoutes(),
    "GET /api/apps": () => json(200, { total: 0, apps: [] }),
    "GET /api/config/webserver": () => json(200, { webserver: "nginx" }),
    "GET /api/apps/types": () => json(200, { types: [{ type: "nodejs", name: "Node.js", default_port: 3000 }] }),
    "GET /api/recipes": () => json(200, RECIPES),
    "GET /api/domains/temporary": () => json(200, { domain: "api.203-0-113-10.sslip.io", address: "203.0.113.10" }),
    "GET /api/integrations/github": () => json(200, STATUS),
    "GET /api/integrations/github/repositories": () => json(200, { items: REPOSITORIES, total: REPOSITORIES.length }),
    "GET /api/integrations/github/repositories/acme/api/branches": () => json(200, { items: [{ name: "main", protected: false, commit: "4f2a9c1" }], total: 1 }),
    ...extra,
  });
  const reads = slowReads();
  return { reads, harness: renderConsole("/apps/new") };
}

/** Waits until the read of a source is under way: the request is out and the step says so, with its Cancel. */
async function readUnderWay(reads: Reads, count = 1): Promise<void> {
  await waitFor(() => {
    expect(reads.started()).toBe(count);
  });
  await screen.findByRole("button", { name: "Cancel" });
}

describe("the read the step starts by itself", () => {
  it("takes every key of a link typed after choosing a repository, and lets go of the read for it", { timeout: 30_000 }, async () => {
    const { reads, harness } = wizard();
    const { user } = harness;
    await user.click(await screen.findByRole("option", { name: /acme\/api/ }));
    await readUnderWay(reads);

    const field = screen.getByLabelText("What to deploy");
    expect(field).toBeEnabled();
    await user.type(field, ADDRESS);

    expect(field).toHaveValue(ADDRESS);
    // The link is another source: the read of the repository is no longer what the step is doing, and is given up.
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
    });
    expect(reads.aborted()).toBe(1);
    expect(screen.queryByRole("combobox", { name: "Repository" })).not.toBeInTheDocument();
  });

  it("opens the search of Change repository with the focus in it while the repository is being read", { timeout: 30_000 }, async () => {
    const { reads, harness } = wizard();
    const { user } = harness;
    await user.click(await screen.findByRole("option", { name: /acme\/api/ }));
    await readUnderWay(reads);

    await user.click(screen.getByRole("button", { name: "Change repository" }));
    const search = screen.getByRole("combobox", { name: "Repository" });
    expect(search).toBeEnabled();
    expect(search).toHaveFocus();

    await user.type(search, "blog");
    expect(search).toHaveValue("blog");
    expect(search).toHaveFocus();
    expect(within(screen.getByRole("listbox", { name: "Repositories" })).getAllByRole("option")).toHaveLength(1);
  });

  it("keeps the focus on the box while a pasted link is read and typing goes on after it", { timeout: 30_000 }, async () => {
    const { reads, harness } = wizard();
    const { user } = harness;
    await screen.findByRole("option", { name: /acme\/api/ });
    const field = screen.getByLabelText("What to deploy");
    await user.click(field);
    await user.paste("https://gitlab.com/acme/api");
    await readUnderWay(reads);
    expect(field).toBeEnabled();
    expect(field).toHaveFocus();

    await user.keyboard(".git");

    expect(field).toHaveValue(ADDRESS);
    expect(field).toHaveFocus();
    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
    });
    expect(reads.aborted()).toBe(1);
  });

  it("still holds the box while the read Continue asked for runs", { timeout: 30_000 }, async () => {
    const { reads, harness } = wizard();
    const { user } = harness;
    await user.click(await screen.findByRole("option", { name: /acme\/api/ }));
    await screen.findByRole("button", { name: "Change repository" });
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await readUnderWay(reads);

    // The page is about to move on to what the read found: nothing is typed over it meanwhile.
    expect(screen.getByLabelText("What to deploy")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Continue" })).toHaveAttribute("aria-busy", "true");
  });
});
