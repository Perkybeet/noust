import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { json, problem } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { DOMAIN, ELEVATED, RELEASE_APP, ZERO_DOWNTIME_OFF, accepted, jobOf, part, renderSettings, saveBar } from "./testKit";
import { outcomeView } from "./webhook";

const COMPOSE_APP = { ...RELEASE_APP, app_type: "docker-compose", layout: "inplace", follow_tags: null };

const REPO_HOOKS = {
  domain: DOMAIN,
  source: "repo",
  document: null,
  repository_error: null,
  pre_deploy: [{ run: ["/app/node_modules/.bin/prisma", "migrate", "deploy"], service: "backend", workdir: "/app", timeout: 600, migrates: true }],
  post_deploy: [{ run: ["./scripts/purge-cache.sh"], service: null, workdir: null, timeout: 60, migrates: false }],
};

const DOCUMENT = "hooks:\n  pre_deploy:\n    - run: npx prisma migrate deploy\n      migrates: true\n";

const IDENTITY_SHARED = { domain: DOMAIN, account: "www-data", group: "www-data", own: false, eligible: true, proposed: "noust-app-shop-example-com", reason: null };

const ELEVATED_SESSION: Record<string, RouteHandler> = { "GET /api/auth/session": () => json(200, ELEVATED) };

describe("deploy hooks in an app's settings", { timeout: 20_000 }, () => {
  it("lists the hooks in use and says they come from the repository", async () => {
    await renderSettings("/hooks", "Deploy hooks", COMPOSE_APP, { [`GET /api/apps/${DOMAIN}/hooks`]: () => json(200, REPO_HOOKS) });
    await screen.findByRole("heading", { name: "In use" });
    const inUse = part("In use");
    expect(within(inUse).getByText("From the repository")).toBeInTheDocument();
    const before = within(inUse).getByRole("list", { name: "Before serving" });
    expect(within(before).getByText("/app/node_modules/.bin/prisma migrate deploy")).toBeInTheDocument();
    expect(within(before).getByText("In the backend service")).toBeInTheDocument();
    expect(within(before).getByText("Changes the database schema")).toBeInTheDocument();
    expect(within(inUse).getByText("./scripts/purge-cache.sh")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("shows why the repository's noust.yaml is not valid, verbatim", async () => {
    await renderSettings("/hooks", "Deploy hooks", COMPOSE_APP, {
      [`GET /api/apps/${DOMAIN}/hooks`]: () => json(200, { ...REPO_HOOKS, repository_error: "hooks.pre_deploy[0].user: unknown key" }),
    });
    expect(await screen.findByText("The repository's noust.yaml is not valid")).toBeInTheDocument();
    expect(screen.getByText("hooks.pre_deploy[0].user: unknown key")).toBeInTheDocument();
  });

  it("saves the operator's YAML with PUT, and then says the hooks are the operator's", async () => {
    const { user, backend } = await renderSettings("/hooks", "Deploy hooks", COMPOSE_APP, {
      ...ELEVATED_SESSION,
      [`GET /api/apps/${DOMAIN}/hooks`]: () => json(200, REPO_HOOKS),
      [`PUT /api/apps/${DOMAIN}/hooks`]: (call) =>
        json(200, { ...REPO_HOOKS, source: "operator", document: (call.body as { document: string }).document, post_deploy: [] }),
    });
    const field = await screen.findByRole("textbox", { name: "Hooks (YAML)" });
    await user.click(field);
    await user.paste(DOCUMENT);
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/hooks`)[0]?.body).toEqual({ document: DOCUMENT });
    });
    expect(await within(part("In use")).findByText("From the operator")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove the operator's hooks" })).toBeInTheDocument();
  });

  it("puts the backend's refusal of the document beside the field", async () => {
    const { user } = await renderSettings("/hooks", "Deploy hooks", COMPOSE_APP, {
      ...ELEVATED_SESSION,
      [`GET /api/apps/${DOMAIN}/hooks`]: () => json(200, REPO_HOOKS),
      [`PUT /api/apps/${DOMAIN}/hooks`]: () => problem(400, "validationerror", "hooks.pre_deploy[0].privileged: unknown key", { fields: { document: "hooks.pre_deploy[0].privileged: unknown key" } }),
    });
    await user.click(await screen.findByRole("textbox", { name: "Hooks (YAML)" }));
    await user.paste("hooks:\n  pre_deploy:\n    - run: x\n      privileged: true\n");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("hooks.pre_deploy[0].privileged: unknown key")).toBeInTheDocument();
  });

  it("speaks Spanish", async () => {
    await act(() => setLocale("es"));
    await renderSettings("/hooks", "Ganchos de despliegue", COMPOSE_APP, { [`GET /api/apps/${DOMAIN}/hooks`]: () => json(200, REPO_HOOKS) });
    expect(await screen.findByText("Del repositorio")).toBeInTheDocument();
  });
});

describe("tags and the account in General", { timeout: 20_000 }, () => {
  it("follows tags with PATCH, and says it", async () => {
    let app: object = COMPOSE_APP;
    const { user, backend } = await renderSettings("", "General", COMPOSE_APP, {
      ...ELEVATED_SESSION,
      [`GET /api/apps/${DOMAIN}`]: () => json(200, app),
      [`GET /api/apps/${DOMAIN}/identity`]: () => json(200, IDENTITY_SHARED),
      [`PATCH /api/apps/${DOMAIN}/follow-tags`]: () => {
        app = { ...COMPOSE_APP, follow_tags: "v*" };
        return json(200, { domain: DOMAIN, follow_tags: "v*", following: true, previous: null });
      },
    });
    expect(screen.getByText("None: it deploys its branch")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Follow tags…" }));
    const dialog = await screen.findByRole("dialog", { name: "Deploy by tag" });
    await user.click(within(dialog).getByRole("button", { name: "Follow these tags" }));
    await waitFor(() => {
      expect(backend.callsTo(`PATCH /api/apps/${DOMAIN}/follow-tags`)[0]?.body).toEqual({ pattern: "v*" });
    });
    expect(await screen.findByRole("button", { name: "Follow the branch again" })).toBeInTheDocument();
  });

  it("says the app shares an account, and moves it to its own as a job", async () => {
    const job = jobOf("id1", "identity_migrate");
    let identity: object = IDENTITY_SHARED;
    const { user, backend } = await renderSettings("", "General", COMPOSE_APP, {
      ...ELEVATED_SESSION,
      [`GET /api/apps/${DOMAIN}/identity`]: () => json(200, identity),
      [`POST /api/apps/${DOMAIN}/identity/migrate`]: () => {
        identity = { ...IDENTITY_SHARED, account: "noust-app-shop-example-com", group: "noust-app-shop-example-com", own: true, proposed: null };
        return json(202, accepted(job));
      },
      [`GET /api/jobs/${job.id}`]: () => json(200, { ...job, status: "completed" }),
    });
    const card = part("The account it runs as");
    expect(await within(card).findByText(/shared with other apps/)).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Give it its own account…" }));
    const dialog = await screen.findByRole("alertdialog", { name: `Give ${DOMAIN} an account of its own?` });
    await user.click(within(dialog).getByRole("button", { name: "Give it its own account" }));
    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${DOMAIN}/identity/migrate`)).toHaveLength(1);
    });
    expect(await within(card).findByText(/an account of its own\./, {}, { timeout: 5_000 })).toBeInTheDocument();
  });
});

describe("zero-downtime deploys for a Compose stack", { timeout: 20_000 }, () => {
  it("lists what stands in the way, each a fix, instead of asking for instant rollback", async () => {
    await renderSettings("/deploys", "Deploys", COMPOSE_APP, {
      [`GET /api/apps/${DOMAIN}/zero-downtime`]: () =>
        json(200, {
          ...ZERO_DOWNTIME_OFF,
          eligible: false,
          reason: "shop.example.com cannot be updated without a cut yet: the service frontend publishes no port on the host",
          hint: "- the service frontend publishes no port on the host: publish one, such as 127.0.0.1:3000:3000\n- the site proggest does not include /etc/nginx/noust-upstreams/shop-example-com/backend.servers: add include /etc/nginx/noust-upstreams/shop-example-com/backend.servers; inside upstream nestjs_upstream",
        }),
    });
    await screen.findByRole("list", { name: "What to change first" });
    const card = part("Zero-downtime deploys");
    expect(within(card).queryByText(/They need instant rollback/)).not.toBeInTheDocument();
    expect(card.querySelector("[data-feature-state]")).toHaveAttribute("data-state", "off");
    const problems = within(card).getByRole("list", { name: "What to change first" });
    expect(within(problems).getAllByRole("listitem")).toHaveLength(2);
    expect(within(problems).getByText(/add include \/etc\/nginx\/noust-upstreams\/shop-example-com\/backend\.servers; inside upstream nestjs_upstream/)).toBeInTheDocument();
  });

  it("offers turning the relay on when the stack can have it, in its own words", async () => {
    await renderSettings("/deploys", "Deploys", COMPOSE_APP, {});
    await screen.findByRole("button", { name: "Turn on…" });
    const card = part("Zero-downtime deploys");
    expect(within(card).getByText(/Each web service's new container starts beside the running one/)).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: "Turn on…" })).toBeInTheDocument();
  });
});

describe("the copy of a stack's databases before an update", { timeout: 20_000 }, () => {
  it("is on, asks before switching it off, and switches it on again at once", async () => {
    let app: object = { ...COMPOSE_APP, backup_before_update: true };
    const { user, backend } = await renderSettings("/deploys", "Deploys", app, {
      ...ELEVATED_SESSION,
      [`GET /api/apps/${DOMAIN}`]: () => json(200, app),
      [`PATCH /api/apps/${DOMAIN}/backup-before-update`]: (call) => {
        const enabled = (call.body as { enabled: boolean }).enabled;
        app = { ...COMPOSE_APP, backup_before_update: enabled };
        return json(200, { domain: DOMAIN, backup_before_update: enabled, previous: !enabled });
      },
    });
    const card = part("Copy of the databases before an update");
    const toggle = within(card).getByRole("switch", { name: "Copy the databases before each update" });
    expect(toggle).toBeChecked();
    await user.click(toggle);
    const dialog = await screen.findByRole("alertdialog", { name: `Stop copying the databases of ${DOMAIN} before an update?` });
    expect(backend.callsTo(`PATCH /api/apps/${DOMAIN}/backup-before-update`)).toHaveLength(0);
    await expectNoAxeViolations(dialog);
    await user.click(within(dialog).getByRole("button", { name: "Stop copying them" }));
    await waitFor(() => {
      expect(backend.callsTo(`PATCH /api/apps/${DOMAIN}/backup-before-update`).map((call) => call.body)).toEqual([{ enabled: false }]);
    });
    expect(await within(card).findByText(/Off: an update takes no copy of the databases/)).toBeInTheDocument();
    await user.click(within(card).getByRole("switch", { name: "Copy the databases before each update" }));
    await waitFor(() => {
      expect(backend.callsTo(`PATCH /api/apps/${DOMAIN}/backup-before-update`).map((call) => call.body)).toEqual([{ enabled: false }, { enabled: true }]);
    });
    await waitFor(() => {
      expect(within(card).queryByText(/Off: an update takes no copy of the databases/)).not.toBeInTheDocument();
    });
  });

  it("is not offered for an app that is not a stack, nor by a server that does not report it", async () => {
    await renderSettings("/deploys", "Deploys", { ...RELEASE_APP, backup_before_update: true });
    expect(screen.queryByRole("heading", { name: "Copy of the databases before an update" })).not.toBeInTheDocument();
  });
});

describe("a worker a 1.x deploy gave a port", { timeout: 20_000 }, () => {
  const WORKER = { ...COMPOSE_APP, port: 3000 };
  const CHECK = { domain: DOMAIN, headless: true, recorded_port: 3000, site_retirable: true };

  it("says why it is reported down, and records it as a worker, removing its site only when ticked", async () => {
    let check: object = CHECK;
    const { user, backend } = await renderSettings("", "General", WORKER, {
      ...ELEVATED_SESSION,
      [`GET /api/apps/${DOMAIN}/identity`]: () => json(200, IDENTITY_SHARED),
      [`GET /api/apps/${DOMAIN}/headless`]: () => json(200, check),
      [`POST /api/apps/${DOMAIN}/headless`]: () => {
        check = { ...CHECK, recorded_port: null };
        return json(200, { domain: DOMAIN, previous_port: 3000, site: "removed" });
      },
    });
    const card = await waitFor(() => part("A worker reported as down"));
    expect(within(card).getByText(/Port 3000 is still recorded for it/)).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Record it as a worker…" }));
    const dialog = await screen.findByRole("alertdialog", { name: `Record ${DOMAIN} as a worker?` });
    const remove = within(dialog).getByRole("checkbox", { name: /Also remove the nginx site Noust wrote for it/ });
    // Removing more starts unchecked.
    expect(remove).not.toBeChecked();
    await expectNoAxeViolations(dialog);
    await user.click(remove);
    await user.click(within(dialog).getByRole("button", { name: "Record it as a worker" }));
    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${DOMAIN}/headless`).map((call) => call.body)).toEqual([{ remove_site: true }]);
    });
    await waitFor(() => {
      expect(screen.queryByRole("heading", { name: "A worker reported as down" })).not.toBeInTheDocument();
    });
  });

  it("offers no site removal when the command would not, and keeps the site", async () => {
    const { user, backend } = await renderSettings("", "General", WORKER, {
      ...ELEVATED_SESSION,
      [`GET /api/apps/${DOMAIN}/identity`]: () => json(200, IDENTITY_SHARED),
      [`GET /api/apps/${DOMAIN}/headless`]: () => json(200, { ...CHECK, site_retirable: false }),
      [`POST /api/apps/${DOMAIN}/headless`]: () => json(200, { domain: DOMAIN, previous_port: 3000, site: "kept" }),
    });
    const card = await waitFor(() => part("A worker reported as down"));
    await user.click(within(card).getByRole("button", { name: "Record it as a worker…" }));
    const dialog = await screen.findByRole("alertdialog", { name: `Record ${DOMAIN} as a worker?` });
    expect(within(dialog).queryByRole("checkbox")).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Record it as a worker" }));
    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${DOMAIN}/headless`).map((call) => call.body)).toEqual([{ remove_site: false }]);
    });
  });

  it("says nothing about a stack that publishes a port", async () => {
    const { backend } = await renderSettings("", "General", WORKER, {
      [`GET /api/apps/${DOMAIN}/identity`]: () => json(200, IDENTITY_SHARED),
      [`GET /api/apps/${DOMAIN}/headless`]: () => json(200, { ...CHECK, headless: false, site_retirable: false }),
    });
    await waitFor(() => {
      expect(backend.callsTo(`GET /api/apps/${DOMAIN}/headless`)).toHaveLength(1);
    });
    await waitFor(() => {
      expect(screen.queryByText("Checking whether the stack publishes a port")).not.toBeInTheDocument();
    });
    expect(screen.queryByRole("heading", { name: "A worker reported as down" })).not.toBeInTheDocument();
  });
});

describe("the webhook of an app that follows tags", () => {
  it("names an ignored tag", () => {
    expect(outcomeView("ignored_tag", "en")).toEqual({ state: "stopped", label: "Older or unmatched tag, ignored" });
  });
});

describe("a node older than 3.2 behind a 3.2 central", { timeout: 20_000 }, () => {
  it("says the server has no deploy hooks instead of an error", async () => {
    await renderSettings("/hooks", "Deploy hooks", COMPOSE_APP, {
      [`GET /api/apps/${DOMAIN}/hooks`]: () => problem(404, "not_found", "Not Found"),
    });
    expect(await screen.findByText(/This server runs an older version of Noust, which has no deploy hooks/)).toBeInTheDocument();
  });
});
