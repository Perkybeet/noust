import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { json } from "../../../test/fakes";
import { screenWidth } from "../testRoutes";
import type { RouteHandler } from "../../../test/fakes";
import { DOMAIN, ELEVATED, IN_PLACE_APP, RELEASE_APP, part, renderSettings } from "./testKit";
import { connectionView, forgeName, outcomeView } from "./webhook";

const OFF = {
  domain: DOMAIN,
  enabled: false,
  state: "disabled",
  layout: "releases",
  inplace_warning: false,
  hooks: {
    exposed: false,
    base_url: null,
    hook_url: `http://localhost:3000/hooks/deploy/${DOMAIN}`,
    hook_url_public: false,
    content_type: "application/json",
    events: ["push"],
  },
  branch: { tracked: "main", pinned: true, any_push_deploys: false },
  forge: { forge: "github", host: "github.com", repository: "shop/storefront", settings_url: "https://github.com/shop/storefront/settings/hooks/new" },
  github_app: { configured: false, hooks_active: false, covers_repository: false, account: null, repository_selection: null, settings_url: null },
  deliveries: { total: 0, refused_since_last_verified: 0, last: null, last_verified_at: null, last_push_at: null },
};

const EXPOSED = { ...OFF.hooks, exposed: true, base_url: "https://hooks.example.com/hooks", hook_url: `https://hooks.example.com/hooks/deploy/${DOMAIN}`, hook_url_public: true };

const PUSH = {
  id: 9,
  received_at: "2026-09-29T10:00:00+00:00",
  provider: "github",
  event: "push",
  outcome: "deploy_started",
  branch: "main",
  detail: "Update queued",
  job_id: "u1",
  delivery_id: "abc",
  count: 1,
};

const CONNECTED = {
  ...OFF,
  enabled: true,
  state: "connected",
  hooks: EXPOSED,
  deliveries: { total: 3, refused_since_last_verified: 0, last: PUSH, last_verified_at: PUSH.received_at, last_push_at: PUSH.received_at },
};

const RECEIVED = {
  domain: DOMAIN,
  items: [
    PUSH,
    { ...PUSH, id: 8, outcome: "ignored_branch", branch: "feature/cart", detail: "Pushed to feature/cart; main deploys", job_id: null },
    { ...PUSH, id: 7, outcome: "bad_signature", provider: null, event: null, branch: null, detail: "The signature did not verify", job_id: null, count: 3 },
    { ...PUSH, id: 6, outcome: "ping", branch: null, detail: null, job_id: null },
  ],
  total: 4,
};

const SECRET = { domain: DOMAIN, secret: "whsec_3f9a0c1e7b2d4a6f", hook_url: `https://hooks.example.com/hooks/deploy/${DOMAIN}` };

function push(status: object | (() => object), routes: Record<string, RouteHandler> = {}, app: object = RELEASE_APP) {
  return renderSettings("/deploy-on-push", "Deploy on push", app, {
    "GET /api/auth/session": () => json(200, ELEVATED),
    [`GET /api/apps/${DOMAIN}/webhook`]: () => json(200, typeof status === "function" ? status() : status),
    [`GET /api/apps/${DOMAIN}/webhook/received`]: () => json(200, { domain: DOMAIN, items: [], total: 0 }),
    ...routes,
  });
}

function step(title: string): HTMLElement {
  return part(title);
}

describe("deploy on push, off", () => {
  it("says what it does, and sets it up in three steps, each saying whether it is done", async () => {
    await push(OFF);
    expect(screen.getByText(/When you push to the repository, GitHub, GitLab or Gitea tells this server/)).toBeInTheDocument();
    expect(await screen.findByText("Off")).toBeInTheDocument();
    expect(screen.getByText(/Pushes to the repository do not deploy this app/)).toBeInTheDocument();

    const hooks = step("A public address for webhooks");
    expect(within(hooks).getByText(/Not done: GitHub cannot reach this console/)).toBeInTheDocument();
    expect(within(hooks).getByText("noust web expose-hooks hooks.example.com")).toBeInTheDocument();

    const secret = step("This app's secret");
    expect(within(secret).getByText(/Not done: every delivery is refused/)).toBeInTheDocument();
    expect(within(secret).getByRole("button", { name: "Create secret" })).toBeInTheDocument();

    const forge = step("Add the webhook in GitHub");
    expect(within(forge).getByText(/In the repository's Settings, open Webhooks/)).toBeInTheDocument();
    expect(within(forge).getByText(`http://localhost:3000/hooks/deploy/${DOMAIN}`)).toBeInTheDocument();
    expect(within(forge).getByText(/This is the console's own address/)).toBeInTheDocument();
    expect(within(forge).getByText("application/json")).toBeInTheDocument();
    expect(within(forge).getByText("Just the push event")).toBeInTheDocument();
    expect(within(forge).getByRole("link", { name: /Open the webhook settings on GitHub/ })).toHaveAttribute("href", OFF.forge.settings_url);
    expect(screen.getByText(/This server's GitHub App deploys every repository it is installed on/)).toBeInTheDocument();
  });

  it("creates the secret and shows it where it is pasted, until hidden", async () => {
    let status: object = { ...OFF, hooks: EXPOSED };
    const { user, backend } = await push({ ...OFF, hooks: EXPOSED }, {
      [`GET /api/apps/${DOMAIN}/webhook`]: () => json(200, status),
      [`POST /api/apps/${DOMAIN}/webhook-secret`]: () => {
        status = { ...status, enabled: true, state: "waiting" };
        return json(200, SECRET);
      },
    });
    expect(within(await screen.findByText(/^Done: webhooks arrive at/)).getByText("https://hooks.example.com/hooks")).toBeInTheDocument();
    await user.click(within(step("This app's secret")).getByRole("button", { name: "Create secret" }));
    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${DOMAIN}/webhook-secret`)).toHaveLength(1);
    });
    const forge = step("Add the webhook in GitHub");
    expect(await within(forge).findByText(SECRET.secret)).toBeInTheDocument();
    expect(await screen.findByText("Waiting for the first push")).toBeInTheDocument();
    expect(within(step("This app's secret")).getByText(/^Done: every delivery must be signed/)).toBeInTheDocument();

    await user.click(within(forge).getByRole("button", { name: "Hide the secret" }));
    expect(within(forge).queryByText(SECRET.secret)).not.toBeInTheDocument();
    expect(within(forge).getByText("Hidden")).toBeInTheDocument();
  });
});

describe("deploy on push, on", () => {
  it("says it is connected, which branch deploys and the last push, and lists everything received", async () => {
    screenWidth(1440);
    await push(CONNECTED, { [`GET /api/apps/${DOMAIN}/webhook/received`]: () => json(200, RECEIVED) });
    expect(await screen.findByText("Connected")).toBeInTheDocument();
    expect(screen.getByText(/Pushes to/)).toHaveTextContent("Pushes to main deploy this app.");
    expect(within(step("Add the webhook in GitHub")).getByText(/^Done: GitHub's last delivery arrived/)).toBeInTheDocument();

    const received = await screen.findByRole("table", { name: `Deliveries received for ${DOMAIN}, newest first` });
    expect(within(received).getByText("Deploy started")).toBeInTheDocument();
    expect(within(received).getByText("Other branch, ignored")).toBeInTheDocument();
    expect(within(received).getByText("feature/cart")).toBeInTheDocument();
    expect(within(received).getByText("Wrong signature")).toBeInTheDocument();
    expect(within(received).getByText(/3 times\. The signature did not verify/)).toBeInTheDocument();
    expect(within(received).getByText("Ping")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Deploys it started" })).toHaveAttribute("href", `/apps/${DOMAIN}/deployments`);
  });

  it("shows the secret again only in sudo mode, on request", async () => {
    const { user, backend } = await push(CONNECTED, { [`POST /api/apps/${DOMAIN}/webhook/reveal`]: () => json(200, SECRET) });
    const forge = await screen.findByText("Hidden");
    await user.click(within(forge.parentElement ?? document.body).getByRole("button", { name: "Show" }));
    expect(await screen.findByText(SECRET.secret)).toBeInTheDocument();
    expect(backend.callsTo(`POST /api/apps/${DOMAIN}/webhook/reveal`)).toHaveLength(1);
  });

  it("asks before replacing the secret, and before turning it off", async () => {
    const { user, backend } = await push(CONNECTED, {
      [`POST /api/apps/${DOMAIN}/webhook-secret`]: () => json(200, SECRET),
      [`DELETE /api/apps/${DOMAIN}/webhook-secret`]: () => json(200, { domain: DOMAIN }),
    });
    await user.click(await screen.findByRole("button", { name: "New secret…" }));
    const rotate = await screen.findByRole("alertdialog", { name: "Create a new secret?" });
    expect(within(rotate).getByText(/refused until GitHub has the new one/)).toBeInTheDocument();
    expect(backend.callsTo(`POST /api/apps/${DOMAIN}/webhook-secret`)).toHaveLength(0);
    await user.click(within(rotate).getByRole("button", { name: "Create new secret" }));
    expect(await screen.findByText(SECRET.secret)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Turn off" }));
    const off = await screen.findByRole("alertdialog", { name: `Turn off deploy on push for ${DOMAIN}?` });
    await user.click(within(off).getByRole("button", { name: "Turn off" }));
    await waitFor(() => {
      expect(backend.callsTo(`DELETE /api/apps/${DOMAIN}/webhook-secret`)).toHaveLength(1);
    });
    await waitFor(() => {
      expect(screen.queryByText(SECRET.secret)).not.toBeInTheDocument();
    });
  });

  it("says when deliveries are refused, and why", async () => {
    await push({ ...CONNECTED, state: "problem", deliveries: { ...CONNECTED.deliveries, refused_since_last_verified: 3 } });
    expect(await screen.findByText("Deliveries refused")).toBeInTheDocument();
    expect(screen.getByText("3 deliveries were refused since the last good one: the secret at GitHub does not match this app's.")).toBeInTheDocument();
  });
});

describe("the warnings of deploy on push", () => {
  it("warns that any push deploys when no branch is pinned, and pins one from the warning", async () => {
    let status: object = { ...CONNECTED, branch: { tracked: null, pinned: false, any_push_deploys: true } };
    const { user, backend } = await push(() => status, {
      [`PATCH /api/apps/${DOMAIN}/branch`]: () => {
        status = CONNECTED;
        return json(200, { domain: DOMAIN, branch: "main", pinned: true, commit: "4f2a9c1", previous: null });
      },
    }, { ...RELEASE_APP, branch: null });
    const warning = (await screen.findByText("Without a pinned branch, any push deploys")).closest("[data-tone]") ?? document.body;
    expect(within(warning as HTMLElement).getByText(/A push to any branch, a work in progress included, deploys this app/)).toBeInTheDocument();
    await user.click(within(warning as HTMLElement).getByRole("button", { name: "Pin the branch" }));
    const dialog = await screen.findByRole("dialog", { name: "Pin the branch that deploys" });
    await user.click(within(dialog).getByRole("button", { name: "Pin branch" }));
    await waitFor(() => {
      expect(backend.callsTo(`PATCH /api/apps/${DOMAIN}/branch`)[0]?.body).toEqual({ branch: "main" });
    });
    await waitFor(() => {
      expect(screen.queryByText("Without a pinned branch, any push deploys")).not.toBeInTheDocument();
    });
  });

  it("warns when every push rebuilds a single folder, and leads to instant rollback", async () => {
    await push({ ...CONNECTED, layout: "inplace", inplace_warning: true }, {}, IN_PLACE_APP);
    expect(await screen.findByText("Every push rebuilds this app in its single folder")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Turn on instant rollback" })).toHaveAttribute("href", `/apps/${DOMAIN}/settings/deploys`);
  });

  it("says the GitHub App already deploys the app, so no webhook is needed", async () => {
    await push({
      ...OFF,
      github_app: { configured: true, hooks_active: true, covers_repository: true, account: "shop", repository_selection: "all", settings_url: "https://github.com/settings/installations/1" },
    });
    expect(await screen.findByText("The GitHub App already deploys this app on push")).toBeInTheDocument();
    expect(screen.getByText(/You do not need a webhook: with one too, each push would deploy twice\./)).toBeInTheDocument();
  });
});

describe("deploy on push, in Spanish and for assistive technology", () => {
  it("has no accessibility violations, off and connected", async () => {
    screenWidth(1440);
    const off = await push(OFF);
    await screen.findByText("Off");
    await expectNoAxeViolations(screen.getByRole("main"));
    off.unmount();
    await push(CONNECTED, { [`GET /api/apps/${DOMAIN}/webhook/received`]: () => json(200, RECEIVED) });
    await screen.findByRole("table");
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("speaks Spanish", async () => {
    await push(CONNECTED, { [`GET /api/apps/${DOMAIN}/webhook/received`]: () => json(200, RECEIVED) });
    await act(() => setLocale("es"));
    expect(await screen.findByRole("heading", { level: 2, name: "Desplegar al hacer push" })).toBeInTheDocument();
    expect(screen.getByText("Conectado")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Push recibidos" })).toBeInTheDocument();
    expect(screen.getByText("Firma incorrecta")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});

describe("the words of the setup", () => {
  it("draws where the connection stands, and each delivery, with a state, a shape and a word", () => {
    expect(connectionView("disabled")).toEqual({ state: "stopped", label: "Off" });
    expect(connectionView("waiting")).toEqual({ state: "queued", label: "Waiting for the first push" });
    expect(connectionView("connected")).toEqual({ state: "running", label: "Connected" });
    expect(connectionView("problem")).toEqual({ state: "failed", label: "Deliveries refused" });
    expect(connectionView("new")).toEqual({ state: "unknown", label: "new" });
    expect(outcomeView("deploy_started")).toEqual({ state: "running", label: "Deploy started" });
    expect(outcomeView("duplicate")).toEqual({ state: "stopped", label: "Repeat, ignored" });
    expect(outcomeView("locked")).toEqual({ state: "failed", label: "Refused, too many wrong signatures" });
    expect(outcomeView("something_new")).toEqual({ state: "unknown", label: "something_new" });
  });

  it("names the forge by its product name, and any other host in words", () => {
    expect(forgeName("gitlab")).toBe("GitLab");
    expect(forgeName(null)).toBe("your Git host");
    expect(forgeName(null, "es")).toBe("tu plataforma Git");
  });
});
