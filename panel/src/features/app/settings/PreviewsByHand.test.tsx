import { act, configure, screen, waitFor, within } from "@testing-library/react";
import { afterAll, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { APPS, SESSION, fakeBackend, json, problem, signedInRoutes } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { viewerSession } from "../../teams/testFixtures";
import { previewName } from "./previews";

vi.setConfig({ testTimeout: 20_000 });
beforeAll(() => {
  configure({ asyncUtilTimeout: 5_000 });
});
afterAll(() => {
  configure({ asyncUtilTimeout: 1_000 });
});

const DOMAIN = "shop.example.com";
const PREVIEWS = `/api/apps/${DOMAIN}/previews`;
const APP = {
  ...APPS[0],
  path: "/var/www/apps/shop-example-com",
  layout: "releases",
  keep_releases: 5,
  source: "https://github.com/shop/storefront.git",
  branch: "main",
};
const ELEVATED = { ...SESSION, elevated_until: "2999-01-01T00:00:00+00:00" };

const SETTINGS = {
  base_domain: "previews.example.com",
  max_previews: 3,
  ttl_hours: 168,
  allow_bots: false,
  exclude_env: [] as string[],
  inherit_env: "allowlist",
  allow_env: [] as string[],
  data_mode: "empty",
  created_at: "2026-09-20T10:00:00+00:00",
  updated_at: "2026-09-20T10:00:00+00:00",
};

const IN_FIVE_DAYS = new Date(Date.now() + 5 * 86_400_000 + 3_600_000).toISOString();

const PR = {
  domain: "pr-12-shop-example-com.previews.example.com",
  url: "https://pr-12-shop-example-com.previews.example.com",
  number: 12,
  kind: "pr",
  ref: null,
  databases: ["postgresql/shop_pr12"],
  branch: "feature/checkout",
  head_sha: "9f1c2e7d4b",
  provider: "github",
  repository: "shop/storefront",
  status: "ready",
  error: null,
  expires_at: IN_FIVE_DAYS,
  created_at: "2026-09-27T10:00:00+00:00",
  updated_at: "2026-09-27T10:05:00+00:00",
};

const BRANCH = {
  ...PR,
  domain: "x-1-shop-example-com.previews.example.com",
  url: "https://x-1-shop-example-com.previews.example.com",
  number: 1_000_001,
  kind: "branch",
  ref: "feature/login",
  databases: [] as string[],
  branch: "feature/login",
  status: "ready",
  provider: "manual",
  repository: null,
};

const TAG = { ...BRANCH, domain: "x-2-shop-example-com.previews.example.com", number: 1_000_002, kind: "tag", ref: "v1.4.0", branch: "v1.4.0" };

function listing(previews: object[], extra: object = {}) {
  return { domain: DOMAIN, enabled: true, settings: SETTINGS, previews, total: previews.length, has_database: true, ...extra };
}

const PULLS = {
  domain: DOMAIN,
  total: 3,
  pull_requests: [
    { number: 21, title: "Add a wishlist", branch: "feature/wishlist", base_branch: "main", head_sha: "aaaa111", author: "ana", draft: false, from_fork: false, trusted: true, url: "https://github.com/shop/storefront/pull/21", preview: null, status: null },
    { number: 22, title: "Fix the cart", branch: "fix/cart", base_branch: "main", head_sha: "bbbb222", author: "omar", draft: true, from_fork: true, trusted: false, url: "https://github.com/shop/storefront/pull/22", preview: null, status: null },
    { number: 12, title: "Checkout", branch: "feature/checkout", base_branch: "main", head_sha: "cccc333", author: "ana", draft: false, from_fork: false, trusted: true, url: "https://github.com/shop/storefront/pull/12", preview: PR.domain, status: "ready" },
  ],
};

async function previewsOf(state: () => object, extra: Record<string, RouteHandler> = {}, app: object = APP, session: typeof ELEVATED = ELEVATED) {
  const backend = fakeBackend({
    ...signedInRoutes(session),
    [`GET /api/apps/${DOMAIN}`]: () => json(200, app),
    "GET /api/certs": () => json(200, { certificates: [], total: 0 }),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
    [`GET ${PREVIEWS}`]: () => json(200, state()),
    [`GET ${PREVIEWS}/variables`]: () => json(200, { domain: DOMAIN, variables: [] }),
    ...extra,
  });
  const harness = renderConsole(`/apps/${DOMAIN}/settings/previews`);
  const section = await screen.findByRole("region", { name: "Pull request previews" });
  return { ...harness, backend, section };
}

describe("what a preview is called", () => {
  it("is #number for a pull request and the ref for a branch or a tag", () => {
    expect(previewName(PR)).toBe("#12");
    expect(previewName(BRANCH)).toBe("feature/login");
    expect(previewName(TAG)).toBe("v1.4.0");
  });
});

describe("what the settings in force expose", () => {
  it("says nothing of the safe way, and warns of every variable and of the shared databases when they are chosen", async () => {
    const safe = await previewsOf(() => listing([]));
    await within(safe.section).findByRole("radio", { name: "Only the ones I choose" });
    expect(within(safe.section).queryByText("Previews get this app's variables")).not.toBeInTheDocument();
    expect(within(safe.section).queryByText("Previews write into this app's databases")).not.toBeInTheDocument();
    safe.unmount();

    const exposed = await previewsOf(() => listing([], { settings: { ...SETTINGS, inherit_env: "all", data_mode: "shared" } }));
    expect(await within(exposed.section).findByText("Previews get this app's variables")).toBeInTheDocument();
    expect(within(exposed.section).getByText("Previews write into this app's databases")).toBeInTheDocument();
    exposed.unmount();

    // An app with no database has nothing to share.
    const none = await previewsOf(() => listing([], { settings: { ...SETTINGS, data_mode: "shared" }, has_database: false }));
    await within(none.section).findByRole("radio", { name: "Only the ones I choose" });
    expect(within(none.section).queryByText("Previews write into this app's databases")).not.toBeInTheDocument();
  });

  it("lets the operator pick the variables copied and the data, and saves the choice", async () => {
    let state: object = listing([]);
    const { user, backend, section } = await previewsOf(() => state, {
      [`PUT ${PREVIEWS}/settings`]: () => {
        const saved = { ...SETTINGS, allow_env: ["NEXT_PUBLIC_API_URL", "SENTRY_DSN"], data_mode: "shared" };
        state = listing([], { settings: saved });
        return json(200, saved);
      },
    });
    await user.type(await within(section).findByRole("textbox", { name: /Copied from the app/ }), "NEXT_PUBLIC_API_URL, SENTRY_DSN");
    await user.click(within(section).getByRole("radio", { name: "The app's databases" }));
    await user.click(within(section).getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(backend.callsTo(`PUT ${PREVIEWS}/settings`)[0]?.body).toEqual({
        base_domain: "previews.example.com",
        max_previews: 3,
        ttl_hours: 168,
        allow_bots: false,
        exclude_env: [],
        inherit_env: "allowlist",
        allow_env: ["NEXT_PUBLIC_API_URL", "SENTRY_DSN"],
        data_mode: "shared",
      });
    });
  });

  it("refuses a name that is not a variable in the list of the copied ones", async () => {
    const { user, backend, section } = await previewsOf(() => listing([]));
    await user.type(await within(section).findByRole("textbox", { name: /Copied from the app/ }), "9LIVES");
    await user.click(within(section).getByRole("button", { name: "Save" }));
    expect(await within(section).findByText(/Not a variable name: 9LIVES\./)).toBeInTheDocument();
    expect(backend.callsTo(`PUT ${PREVIEWS}/settings`)).toHaveLength(0);
  });
});

describe("making a preview by hand", () => {
  it("lists a manual preview by its branch or tag, and says which database is its own", async () => {
    const { section } = await previewsOf(() => listing([PR, BRANCH, TAG]));
    const list = await within(section).findByRole("list", { name: "Previews" });
    const [pr, branch, tag] = within(list).getAllByRole("listitem");
    if (!pr || !branch || !tag) throw new Error("three previews expected");
    expect(within(pr).getByRole("img", { name: "Pull request" })).toBeInTheDocument();
    expect(within(pr).getByText("postgresql/shop_pr12")).toBeInTheDocument();
    expect(within(branch).getByRole("img", { name: "Branch" })).toBeInTheDocument();
    expect(within(branch).getByText("feature/login")).toBeInTheDocument();
    expect(within(tag).getByRole("img", { name: "Tag" })).toBeInTheDocument();
    expect(within(tag).getByText("v1.4.0")).toBeInTheDocument();
  });

  it("creates one from a branch, naming nothing else", async () => {
    let state: object = listing([]);
    const { user, backend, section } = await previewsOf(() => state, {
      [`GET ${PREVIEWS}/pull-requests`]: () => json(200, PULLS),
      [`POST ${PREVIEWS}`]: () => {
        state = listing([BRANCH]);
        return json(202, { preview: { ...BRANCH, status: "pending" }, job_id: "job-1" });
      },
    });
    await user.click(await within(section).findByRole("button", { name: "New preview" }));
    const dialog = await screen.findByRole("dialog", { name: "New preview" });
    await user.click(within(dialog).getByRole("radio", { name: "Branch" }));
    await user.type(within(dialog).getByLabelText("Branch"), "feature/login");
    await user.click(within(dialog).getByRole("button", { name: "Create preview" }));
    await waitFor(() => {
      expect(backend.callsTo(`POST ${PREVIEWS}`)[0]?.body).toEqual({ branch: "feature/login", allow_fork: false });
    });
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "New preview" })).not.toBeInTheDocument();
    });
    expect(await within(section).findByText("feature/login")).toBeInTheDocument();
  });

  it("creates one from a tag", async () => {
    const { user, backend, section } = await previewsOf(() => listing([]), {
      [`GET ${PREVIEWS}/pull-requests`]: () => json(200, PULLS),
      [`POST ${PREVIEWS}`]: () => json(202, { preview: { ...TAG, status: "pending" }, job_id: "job-2" }),
    });
    await user.click(await within(section).findByRole("button", { name: "New preview" }));
    const dialog = await screen.findByRole("dialog", { name: "New preview" });
    await user.click(within(dialog).getByRole("radio", { name: "Tag" }));
    await user.type(within(dialog).getByLabelText("Tag"), "v1.4.0");
    await user.click(within(dialog).getByRole("button", { name: "Create preview" }));
    await waitFor(() => {
      expect(backend.callsTo(`POST ${PREVIEWS}`)[0]?.body).toEqual({ tag: "v1.4.0", allow_fork: false });
    });
  });

  it("lists the open pull requests of a GitHub repository and builds the one chosen", async () => {
    const { user, backend, section } = await previewsOf(() => listing([]), {
      [`GET ${PREVIEWS}/pull-requests`]: () => json(200, PULLS),
      [`POST ${PREVIEWS}`]: () => json(202, { preview: { ...PR, number: 21, status: "pending" }, job_id: "job-3" }),
    });
    await user.click(await within(section).findByRole("button", { name: "New preview" }));
    const dialog = await screen.findByRole("dialog", { name: "New preview" });
    // The repository is on GitHub: the pull requests are what the dialog starts on.
    const list = await within(dialog).findByRole("radiogroup", { name: "Open pull requests" });
    expect(within(list).getByRole("radio", { name: "#21 Add a wishlist" })).toBeInTheDocument();
    expect(within(list).getByText(/ana · feature\/wishlist/)).toBeInTheDocument();
    expect(within(list).getByText(/omar · fix\/cart · Draft · From a fork/)).toBeInTheDocument();
    expect(within(list).getByText(/Has a preview/)).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Create preview" }));
    expect(await within(dialog).findByText("Choose a pull request.")).toBeInTheDocument();
    expect(backend.callsTo(`POST ${PREVIEWS}`)).toHaveLength(0);

    await user.click(within(list).getByRole("radio", { name: "#21 Add a wishlist" }));
    expect(within(dialog).queryByRole("checkbox", { name: "Build code from a fork" })).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Create preview" }));
    await waitFor(() => {
      expect(backend.callsTo(`POST ${PREVIEWS}`)[0]?.body).toEqual({ pull_request: 21, allow_fork: false });
    });
  });

  it("asks before building the code of a fork", async () => {
    const { user, backend, section } = await previewsOf(() => listing([]), {
      [`GET ${PREVIEWS}/pull-requests`]: () => json(200, PULLS),
      [`POST ${PREVIEWS}`]: () => json(202, { preview: { ...PR, number: 22, status: "pending" }, job_id: "job-4" }),
    });
    await user.click(await within(section).findByRole("button", { name: "New preview" }));
    const dialog = await screen.findByRole("dialog", { name: "New preview" });
    await user.click(await within(dialog).findByRole("radio", { name: "#22 Fix the cart" }));
    await user.click(within(dialog).getByRole("checkbox", { name: "Build code from a fork" }));
    await user.click(within(dialog).getByRole("button", { name: "Create preview" }));
    await waitFor(() => {
      expect(backend.callsTo(`POST ${PREVIEWS}`)[0]?.body).toEqual({ pull_request: 22, allow_fork: true });
    });
  });

  it("says why the pull requests cannot be read and offers a branch instead", async () => {
    const { user, section } = await previewsOf(() => listing([]), {
      [`GET ${PREVIEWS}/pull-requests`]: () => problem(502, "integrationerror", "No GitHub App installation reaches shop/storefront"),
    });
    await user.click(await within(section).findByRole("button", { name: "New preview" }));
    const dialog = await screen.findByRole("dialog", { name: "New preview" });
    expect(await within(dialog).findByText("Could not read the open pull requests")).toBeInTheDocument();
    expect(within(dialog).getByText("No GitHub App installation reaches shop/storefront")).toBeInTheDocument();
    expect(within(dialog).getByText(/Make the preview from a branch instead/)).toBeInTheDocument();
  });

  it("starts on a branch where the repository is not on GitHub, and asks the host nothing", async () => {
    const { user, backend, section } = await previewsOf(() => listing([]), {}, { ...APP, source: "https://git.example.com/shop/storefront.git" });
    await user.click(await within(section).findByRole("button", { name: "New preview" }));
    const dialog = await screen.findByRole("dialog", { name: "New preview" });
    expect(within(dialog).getByLabelText("Branch")).toBeInTheDocument();
    expect(backend.callsTo(`GET ${PREVIEWS}/pull-requests`)).toHaveLength(0);
  });

  it("shows the refusal of the server in the dialog and keeps it open", async () => {
    const { user, section } = await previewsOf(() => listing([]), {
      [`POST ${PREVIEWS}`]: () => problem(400, "validation_error", "Previews are limited to 3 at once"),
    });
    await user.click(await within(section).findByRole("button", { name: "New preview" }));
    const dialog = await screen.findByRole("dialog", { name: "New preview" });
    await user.click(within(dialog).getByRole("radio", { name: "Branch" }));
    await user.type(within(dialog).getByLabelText("Branch"), "main");
    await user.click(within(dialog).getByRole("button", { name: "Create preview" }));
    expect(await within(dialog).findByText("Previews are limited to 3 at once")).toBeInTheDocument();
    expect(within(dialog).getByText("The preview was not created")).toBeInTheDocument();
  });

  it("offers no New preview while previews are off", async () => {
    const { section } = await previewsOf(() => ({ domain: DOMAIN, enabled: false, settings: null, previews: [], total: 0, has_database: false }));
    await within(section).findByText("Off");
    expect(within(section).queryByRole("button", { name: "New preview" })).not.toBeInTheDocument();
  });
});

describe("managing one preview at a time", () => {
  // jsdom has no layout: the log viewer sizes its window from offsetHeight, which it reports as 0.
  beforeEach(() => {
    vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockReturnValue(400);
    vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockReturnValue(800);
  });

  it("builds it again from its row", async () => {
    const { user, backend, section } = await previewsOf(() => listing([PR]), {
      [`POST ${PREVIEWS}/12/redeploy`]: () => json(202, { preview: { ...PR, status: "pending" }, job_id: "job-5" }),
    });
    const list = await within(section).findByRole("list", { name: "Previews" });
    await user.click(within(list).getByRole("button", { name: "Redeploy #12" }));
    await waitFor(() => {
      expect(backend.callsTo(`POST ${PREVIEWS}/12/redeploy`)).toHaveLength(1);
    });
  });

  it("stops one after one question, and starts it again", async () => {
    let state: object = listing([PR]);
    const { user, backend, section } = await previewsOf(() => state, {
      [`POST ${PREVIEWS}/12/stop`]: () => {
        state = listing([{ ...PR, status: "stopped" }]);
        return json(200, { ...PR, status: "stopped" });
      },
      [`POST ${PREVIEWS}/12/start`]: () => {
        state = listing([PR]);
        return json(200, PR);
      },
    });
    const list = await within(section).findByRole("list", { name: "Previews" });
    await user.click(within(list).getByRole("button", { name: "Actions for the preview of #12" }));
    await user.click(await screen.findByRole("menuitem", { name: "Stop" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Stop the preview of #12?" });
    expect(within(dialog).getByText(/stops answering\. Its files, certificate and database stay/)).toBeInTheDocument();
    expect(backend.callsTo(`POST ${PREVIEWS}/12/stop`)).toHaveLength(0);
    await user.click(within(dialog).getByRole("button", { name: "Stop preview" }));
    await waitFor(() => {
      expect(backend.callsTo(`POST ${PREVIEWS}/12/stop`)).toHaveLength(1);
    });
    expect(await within(list).findByText("Stopped")).toBeInTheDocument();

    await user.click(within(list).getByRole("button", { name: "Actions for the preview of #12" }));
    await user.click(await screen.findByRole("menuitem", { name: "Start" }));
    await waitFor(() => {
      expect(backend.callsTo(`POST ${PREVIEWS}/12/start`)).toHaveLength(1);
    });
  });

  it("opens the logs of its last build and of its service beside the list", async () => {
    const { user, section } = await previewsOf(() => listing([PR]), {
      [`GET ${PREVIEWS}/12/logs`]: () => json(200, { domain: PR.domain, lines: 200, logs: "Oct 12 10:00 node[41]: listening on 3101\n", build: "npm ci\nnpm run build\nbuilt in 12s\n" }),
    });
    const list = await within(section).findByRole("list", { name: "Previews" });
    await user.click(within(list).getByRole("button", { name: "Logs #12" }));
    const drawer = await screen.findByRole("dialog", { name: "Logs of the preview of #12" });
    expect(await within(drawer).findByText("built in 12s")).toBeInTheDocument();
    await user.click(within(drawer).getByRole("radio", { name: "Service" }));
    expect(await within(drawer).findByText(/listening on 3101/)).toBeInTheDocument();
    await expectNoAxeViolations(drawer);
  });

  it("opens the build's output from a failed preview", async () => {
    const failed = { ...PR, status: "failed", error: "npm run build exited with 1" };
    const { user, section } = await previewsOf(() => listing([failed]), {
      [`GET ${PREVIEWS}/12/logs`]: () => json(200, { domain: PR.domain, lines: 200, logs: "", build: "Type error: Property 'total' does not exist\n" }),
    });
    const list = await within(section).findByRole("list", { name: "Previews" });
    expect(within(list).getByText("npm run build exited with 1")).toBeInTheDocument();
    await user.click(within(list).getByRole("button", { name: /^Logs$/ }));
    expect(await screen.findByText(/Type error: Property 'total' does not exist/)).toBeInTheDocument();
  });

  it("shows a refusal to rebuild as the server worded it", async () => {
    const { user, section } = await previewsOf(() => listing([PR]), {
      [`POST ${PREVIEWS}/12/redeploy`]: () => problem(400, "validation_error", "The preview is being removed"),
    });
    const list = await within(section).findByRole("list", { name: "Previews" });
    await user.click(within(list).getByRole("button", { name: "Redeploy #12" }));
    expect(await screen.findByText("The preview of #12 was not rebuilt")).toBeInTheDocument();
    expect(screen.getByText("The preview is being removed")).toBeInTheDocument();
  });
});

describe("what each principal is offered", () => {
  it("lets someone who may only read open the logs and nothing that changes a preview", async () => {
    const { section } = await previewsOf(() => listing([PR]), {}, APP, viewerSession() as typeof ELEVATED);
    const list = await within(section).findByRole("list", { name: "Previews" });
    expect(within(list).getByRole("button", { name: "Logs #12" })).toBeInTheDocument();
    expect(within(list).queryByRole("button", { name: "Redeploy #12" })).not.toBeInTheDocument();
    expect(within(list).queryByRole("button", { name: "Actions for the preview of #12" })).not.toBeInTheDocument();
    expect(within(section).queryByRole("button", { name: "New preview" })).not.toBeInTheDocument();
  });

  it("lets an operator stop and start a preview but not rebuild or remove it", async () => {
    const operator = { ...ELEVATED, permissions: ["self", "apps.read", "apps.operate"] };
    const { user, section } = await previewsOf(() => listing([PR]), {}, APP, operator);
    const list = await within(section).findByRole("list", { name: "Previews" });
    expect(within(list).queryByRole("button", { name: "Redeploy #12" })).not.toBeInTheDocument();
    await user.click(within(list).getByRole("button", { name: "Actions for the preview of #12" }));
    expect(await screen.findByRole("menuitem", { name: "Stop" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Remove" })).not.toBeInTheDocument();
  });
});

describe("the variables defined for previews", () => {
  it("are listed under the previews, with the app's own dialog to add one", async () => {
    const { user, backend, section } = await previewsOf(() => listing([]), {
      [`GET ${PREVIEWS}/variables`]: () => json(200, { domain: DOMAIN, variables: [{ name: "STRIPE_KEY", value: "***", secret: true, marked: true, audience: "both", updated_at: "2026-10-10T10:00:00+00:00" }] }),
      [`PUT ${PREVIEWS}/variables/API_URL`]: () => json(200, { domain: DOMAIN, variables: [] }),
    });
    // A phone's width, as a test's window is: each variable is a card in a list.
    expect(await within(section).findByText("STRIPE_KEY")).toBeInTheDocument();
    expect(within(section).getByRole("list", { name: `Variables for the previews of ${DOMAIN}` })).toBeInTheDocument();
    expect(within(section).getByRole("heading", { level: 3, name: "Variables for previews" })).toBeInTheDocument();

    await user.click(within(section).getByRole("button", { name: "Add variable" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a shared variable" });
    await user.type(within(dialog).getByLabelText("Name"), "API_URL");
    await user.type(within(dialog).getByLabelText("Value"), "https://staging.api.example.com");
    await user.click(within(dialog).getByRole("button", { name: "Add variable" }));
    await waitFor(() => {
      expect(backend.callsTo(`PUT ${PREVIEWS}/variables/API_URL`)[0]?.body).toEqual({ value: "https://staging.api.example.com", secret: null, audience: "both" });
    });
  });

  it("are not offered where previews are off", async () => {
    const { backend, section } = await previewsOf(() => ({ domain: DOMAIN, enabled: false, settings: null, previews: [], total: 0, has_database: false }));
    await within(section).findByText("Off");
    expect(backend.callsTo(`GET ${PREVIEWS}/variables`)).toHaveLength(0);
  });
});

describe("the previews in Spanish", () => {
  it("speaks Spanish, with no accessibility violations in the dialog", async () => {
    const { user, section } = await previewsOf(() => listing([PR, BRANCH]), { [`GET ${PREVIEWS}/pull-requests`]: () => json(200, PULLS) });
    await within(section).findByRole("list", { name: "Previews" });
    await act(() => setLocale("es"));
    const list = await within(section).findByRole("list", { name: "Vistas previas" });
    expect(within(list).getByRole("button", { name: "Registros #12" })).toBeInTheDocument();
    expect(within(section).getByRole("radio", { name: "Solo las que elija" })).toBeInTheDocument();
    await user.click(within(section).getByRole("button", { name: "Nueva vista previa" }));
    const dialog = await screen.findByRole("dialog", { name: "Nueva vista previa" });
    expect(await within(dialog).findByRole("radio", { name: "#21 Add a wishlist" })).toBeInTheDocument();
    await expectNoAxeViolations(dialog);
  });
});
