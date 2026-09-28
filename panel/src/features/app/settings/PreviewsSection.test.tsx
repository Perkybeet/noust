import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { previewsInterval } from "../../../api/queries/previews";
import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { APPS, SESSION, fakeBackend, json, problem, signedInRoutes } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { envNamesOf, lifetime, parsePreviewDraft, previewDraftOf, previewFieldOf, previewStatus, samePreviewDraft, ttlDraft } from "./previews";

const DOMAIN = "shop.example.com";
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
  created_at: "2026-09-20T10:00:00+00:00",
  updated_at: "2026-09-20T10:00:00+00:00",
};

const IN_FIVE_DAYS = new Date(Date.now() + 5 * 86_400_000 + 3_600_000).toISOString();

const READY = {
  domain: "pr-12-shop-example-com.previews.example.com",
  url: "https://pr-12-shop-example-com.previews.example.com",
  number: 12,
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

const FAILED = {
  ...READY,
  domain: "pr-15-shop-example-com.previews.example.com",
  url: "https://pr-15-shop-example-com.previews.example.com",
  number: 15,
  branch: "fix/cart",
  status: "failed",
  error: "npm run build exited with 1\nType error: Property 'total' does not exist on type 'Cart'.",
};

const OFF = { domain: DOMAIN, enabled: false, settings: null, previews: [], total: 0 };
const ON = { domain: DOMAIN, enabled: true, settings: SETTINGS, previews: [READY, FAILED], total: 2 };

const JOB = {
  id: "pv1",
  type: "delete",
  name: "Remove pr-12-shop-example-com.previews.example.com",
  description: "Removing a preview",
  status: "pending",
  progress: 0,
  total_steps: 100,
  current_step: "",
  created_at: "2026-09-28T10:00:00",
  logs: [],
  metadata: { domain: READY.domain, parent: DOMAIN },
};

async function previewsOf(state: () => object, extra: Record<string, RouteHandler> = {}, app: object = APP) {
  const backend = fakeBackend({
    ...signedInRoutes(ELEVATED),
    [`GET /api/apps/${DOMAIN}`]: () => json(200, app),
    "GET /api/certs": () => json(200, { certificates: [], total: 0 }),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
    [`GET /api/apps/${DOMAIN}/releases`]: () => json(200, { domain: DOMAIN, items: [], total: 0 }),
    [`GET /api/apps/${DOMAIN}/webhook/deliveries`]: () => json(200, { items: [], total: 0 }),
    [`GET /api/apps/${DOMAIN}/zero-downtime`]: () =>
      json(200, { domain: DOMAIN, enabled: false, drain_seconds: 10, instances: [], eligible: true, reason: null, hint: null }),
    [`GET /api/apps/${DOMAIN}/previews`]: () => json(200, state()),
    [`GET /api/jobs/${JOB.id}`]: () => json(200, JOB),
    ...extra,
  });
  const harness = renderConsole(`/apps/${DOMAIN}/settings`);
  const section = await screen.findByRole("region", { name: "Pull request previews" });
  return { ...harness, backend, section };
}

describe("pull request previews", () => {
  it("says what previews need and what they get, then turns them on with the form's values", async () => {
    let state: object = OFF;
    const { user, backend, section } = await previewsOf(() => state, {
      [`PUT /api/apps/${DOMAIN}/previews/settings`]: () => {
        state = { ...OFF, enabled: true, settings: SETTINGS };
        return json(200, SETTINGS);
      },
    });
    expect(await within(section).findByText("Off")).toBeInTheDocument();
    const needs = within(section).getByRole("list", { name: "What previews need" });
    expect(within(needs).getByText("A wildcard DNS record")).toBeInTheDocument();
    expect(within(needs).getByText("Pull request events")).toBeInTheDocument();
    expect(within(section).getByText("Previews get this app's production secrets")).toBeInTheDocument();
    expect(within(section).getByText(/built as root, like every deploy/)).toBeInTheDocument();
    expect(within(section).getByText(/except those never copied to previews, and connects to its databases/)).toBeInTheDocument();
    expect(within(section).getByText(/only pull requests by the repository's owners, members and\s+collaborators/)).toBeInTheDocument();
    expect(within(section).getByRole("checkbox", { name: "Allow pull requests from bots" })).not.toBeChecked();
    expect(within(section).getByRole("textbox", { name: "Never copied to previews" })).toHaveValue("");

    expect(within(section).getByRole("textbox", { name: "At most" })).toHaveValue("3");
    expect(within(section).getByRole("textbox", { name: "Removed after" })).toHaveValue("7");
    expect(within(section).getByRole("radio", { name: "Days" })).toBeChecked();

    await user.type(within(section).getByRole("textbox", { name: "Base domain" }), "previews.example.com");
    expect(within(section).getByText("*.previews.example.com", { selector: "code" })).toBeInTheDocument();
    await user.click(within(section).getByRole("button", { name: "Turn on previews" }));
    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/previews/settings`)[0]?.body).toEqual({
        base_domain: "previews.example.com",
        max_previews: 3,
        ttl_hours: 168,
        allow_bots: false,
        exclude_env: [],
      });
    });
    expect(await within(section).findByText("On")).toBeInTheDocument();
    expect(within(section).getByText("Every variable is copied. Bots get none.")).toBeInTheDocument();
    expect(within(section).getByText("Now: at most 3 previews under previews.example.com, each removed after 7 days without a push.")).toBeInTheDocument();
    expect(within(section).getByRole("button", { name: "Turn off previews" })).toBeInTheDocument();
  });

  it("takes the lifetime in hours when asked to", async () => {
    const { user, backend, section } = await previewsOf(() => OFF, {
      [`PUT /api/apps/${DOMAIN}/previews/settings`]: () => json(200, { ...SETTINGS, ttl_hours: 36 }),
    });
    await user.type(await within(section).findByRole("textbox", { name: "Base domain" }), "*.previews.example.com");
    await user.click(within(section).getByRole("radio", { name: "Hours" }));
    const ttl = within(section).getByRole("textbox", { name: "Removed after" });
    await user.clear(ttl);
    await user.type(ttl, "36");
    await user.click(within(section).getByRole("button", { name: "Turn on previews" }));
    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/previews/settings`)[0]?.body).toEqual({
        base_domain: "*.previews.example.com",
        max_previews: 3,
        ttl_hours: 36,
        allow_bots: false,
        exclude_env: [],
      });
    });
  });

  it("refuses what is plainly wrong before asking the backend", async () => {
    const { user, backend, section } = await previewsOf(() => OFF);
    const max = await within(section).findByRole("textbox", { name: "At most" });
    await user.clear(max);
    await user.type(max, "25");
    await user.click(within(section).getByRole("button", { name: "Turn on previews" }));
    expect(await within(section).findByText(/Give the domain the wildcard record is for/)).toBeInTheDocument();
    expect(within(section).getByRole("textbox", { name: "Base domain" })).toHaveAttribute("aria-invalid", "true");
    expect(within(section).getByText("From 1 to 20 previews at once.")).toBeInTheDocument();
    expect(max).toHaveAttribute("aria-invalid", "true");
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/previews/settings`)).toHaveLength(0);
  });

  it("saves the variables never copied and whether bots get previews", async () => {
    let state: object = { ...ON, previews: [] };
    const { user, backend, section } = await previewsOf(() => state, {
      [`PUT /api/apps/${DOMAIN}/previews/settings`]: () => {
        const saved = { ...SETTINGS, allow_bots: true, exclude_env: ["STRIPE_SECRET_KEY", "SMTP_PASSWORD"] };
        state = { ...ON, previews: [], settings: saved };
        return json(200, saved);
      },
    });
    const save = await within(section).findByRole("button", { name: "Save" });
    expect(save).toBeDisabled();
    await user.type(within(section).getByRole("textbox", { name: "Never copied to previews" }), "STRIPE_SECRET_KEY,  SMTP_PASSWORD STRIPE_SECRET_KEY");
    await user.click(within(section).getByRole("checkbox", { name: "Allow pull requests from bots" }));
    await user.click(save);
    await waitFor(() => {
      expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/previews/settings`)[0]?.body).toEqual({
        base_domain: "previews.example.com",
        max_previews: 3,
        ttl_hours: 168,
        allow_bots: true,
        exclude_env: ["STRIPE_SECRET_KEY", "SMTP_PASSWORD"],
      });
    });
    expect(await within(section).findByText("STRIPE_SECRET_KEY, SMTP_PASSWORD", { selector: "span" })).toBeInTheDocument();
    expect(within(section).getByText(/Bots get previews\./)).toBeInTheDocument();
    expect(within(section).getByRole("textbox", { name: "Never copied to previews" })).toHaveValue("STRIPE_SECRET_KEY, SMTP_PASSWORD");
    expect(within(section).getByRole("checkbox", { name: "Allow pull requests from bots" })).toBeChecked();
  });

  it("refuses a variable name the backend would refuse, and places the backend's own refusal", async () => {
    const { user, backend, section } = await previewsOf(() => ({ ...ON, previews: [] }), {
      [`PUT /api/apps/${DOMAIN}/previews/settings`]: () =>
        problem(400, "validation_error", "Invalid variable name: 'SECRET-KEY'", { fields: { exclude_env: "Invalid variable name: 'SECRET-KEY'" } }),
    });
    const names = await within(section).findByRole("textbox", { name: "Never copied to previews" });
    await user.type(names, "1TOKEN");
    await user.click(within(section).getByRole("button", { name: "Save" }));
    expect(await within(section).findByText(/Not a variable name: 1TOKEN\./)).toBeInTheDocument();
    expect(names).toHaveAttribute("aria-invalid", "true");
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/previews/settings`)).toHaveLength(0);

    await user.clear(names);
    await user.type(names, "SECRET_KEY");
    await user.click(within(section).getByRole("button", { name: "Save" }));
    expect(await within(section).findByText("Invalid variable name: 'SECRET-KEY'")).toBeInTheDocument();
    expect(names).toHaveAttribute("aria-invalid", "true");
  });

  it("puts the backend's refusal beside the field its words name", async () => {
    const { user, section } = await previewsOf(() => OFF, {
      [`PUT /api/apps/${DOMAIN}/previews/settings`]: () =>
        problem(400, "validation_error", "Not a base domain for previews: 'localhost'", {
          hint: "Give a domain name such as previews.example.com, not an address or a single label.",
        }),
    });
    const base = await within(section).findByRole("textbox", { name: "Base domain" });
    await user.type(base, "localhost");
    await user.click(within(section).getByRole("button", { name: "Turn on previews" }));
    expect(
      await within(section).findByText(
        "Not a base domain for previews: 'localhost'. Give a domain name such as previews.example.com, not an address or a single label.",
      ),
    ).toBeInTheDocument();
    expect(base).toHaveAttribute("aria-invalid", "true");
  });

  it("lists each preview: its state, branch, where it answers, its page here and when it goes", async () => {
    const { section } = await previewsOf(() => ON);
    const list = await within(section).findByRole("list", { name: "Previews" });
    expect(within(section).getByText("2 of at most 3")).toBeInTheDocument();
    const [ready, failed] = within(list).getAllByRole("listitem");
    if (!ready || !failed) throw new Error("two previews expected");

    expect(within(ready).getByText("Ready")).toBeInTheDocument();
    expect(within(ready).getByText("#12")).toBeInTheDocument();
    expect(within(ready).getByText("feature/checkout")).toBeInTheDocument();
    expect(within(ready).getByText("9f1c2e7")).toBeInTheDocument();
    expect(within(ready).getByRole("link", { name: READY.domain })).toHaveAttribute("href", `/apps/${READY.domain}`);
    const open = within(ready).getByRole("link", { name: "Open preview of pull request #12 (opens in a new tab)" });
    expect(open).toHaveAttribute("href", READY.url);
    expect(open).toHaveAttribute("target", "_blank");
    expect(open.getAttribute("rel")).toContain("noopener");
    expect(within(ready).getByText(/^Expires/)).toBeInTheDocument();
    expect(within(ready).getByText("in 5d")).toBeInTheDocument();

    expect(within(failed).getByText("Failed")).toBeInTheDocument();
    expect(within(failed).getByText("The last build of #15 failed")).toBeInTheDocument();
    expect(within(failed).getByText(/Type error: Property 'total' does not exist on type 'Cart'\./)).toBeInTheDocument();
  });

  it("removes one preview after asking, as a job", async () => {
    const { user, backend, section } = await previewsOf(() => ON, {
      [`DELETE /api/apps/${DOMAIN}/previews/12`]: () =>
        json(202, { job_id: JOB.id, status: "pending", message: `Removal of ${READY.domain} queued`, job: JOB }),
    });
    const list = await within(section).findByRole("list", { name: "Previews" });
    await user.click(within(list).getByRole("button", { name: "Remove the preview of pull request #12" }));
    const dialog = await screen.findByRole("dialog", { name: "Remove the preview of pull request #12?" });
    expect(within(dialog).getByText(new RegExp(`${READY.domain.replace(/\./g, "\\.")} stops answering`))).toBeInTheDocument();
    expect(backend.callsTo(`DELETE /api/apps/${DOMAIN}/previews/12`)).toHaveLength(0);
    await user.click(within(dialog).getByRole("button", { name: "Remove preview" }));
    await waitFor(() => {
      expect(backend.callsTo(`DELETE /api/apps/${DOMAIN}/previews/12`)).toHaveLength(1);
    });
    const [ready] = within(list).getAllByRole("listitem");
    if (!ready) throw new Error("a preview expected");
    expect(await within(ready).findByText("Removing")).toBeInTheDocument();
    expect(within(ready).getByRole("button", { name: "Remove the preview of pull request #12" })).toBeDisabled();
  });

  it("turns previews off after naming what is removed", async () => {
    let state: object = ON;
    const { user, backend, section } = await previewsOf(() => state, {
      [`DELETE /api/apps/${DOMAIN}/previews/settings`]: () => {
        state = { ...ON, enabled: false, settings: null, previews: ON.previews.map((preview) => ({ ...preview, status: "removing" })) };
        return json(200, { domain: DOMAIN, enabled: false, removing: [READY.domain, FAILED.domain], job_id: JOB.id });
      },
    });
    await user.click(await within(section).findByRole("button", { name: "Turn off previews" }));
    const dialog = await screen.findByRole("dialog", { name: `Turn off previews for ${DOMAIN}?` });
    expect(within(dialog).getByText(/the 2 previews there are now are removed/)).toBeInTheDocument();
    const removed = within(dialog).getByRole("list", { name: "Previews that are removed" });
    expect(within(removed).getByText(READY.domain)).toBeInTheDocument();
    expect(within(removed).getByText(FAILED.domain)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Turn off previews" }));
    await waitFor(() => {
      expect(backend.callsTo(`DELETE /api/apps/${DOMAIN}/previews/settings`)).toHaveLength(1);
    });
    expect(await within(section).findByText("Off")).toBeInTheDocument();
    expect(await within(section).findAllByText("Removing")).toHaveLength(2);
    expect(within(section).getByRole("button", { name: "Turn on previews" })).toBeInTheDocument();
  });

  it("on a preview, points at the application it previews instead", async () => {
    const { backend, section } = await previewsOf(() => OFF, {}, { ...APP, preview_parent: "store.example.com" });
    expect(within(section).getByText(/This app is a preview of/)).toBeInTheDocument();
    expect(within(section).getByRole("link", { name: "store.example.com" })).toHaveAttribute("href", "/apps/store.example.com/settings");
    expect(within(section).queryByRole("textbox")).not.toBeInTheDocument();
    expect(backend.callsTo(`GET /api/apps/${DOMAIN}/previews`)).toHaveLength(0);
  });

  it("has no accessibility violations, off and on", async () => {
    const off = await previewsOf(() => OFF);
    await within(off.section).findByText("Off");
    await expectNoAxeViolations(off.section);
    off.unmount();

    const on = await previewsOf(() => ON);
    await within(on.section).findByRole("list", { name: "Previews" });
    await expectNoAxeViolations(on.section);
    await on.user.click(within(on.section).getByRole("button", { name: "Turn off previews" }));
    await expectNoAxeViolations(await screen.findByRole("dialog"));
  });

  it("renders the previews list in Spanish, with no accessibility violations", async () => {
    const { section } = await previewsOf(() => ON);
    await act(() => setLocale("es"));
    await within(section).findByRole("list", { name: "Vistas previas" });
    expect(within(section).getByText("Activadas")).toBeInTheDocument();
    expect(within(section).getByText("2 de como máximo 3")).toBeInTheDocument();
    expect(within(section).getByRole("button", { name: "Desactivar vistas previas" })).toBeInTheDocument();
    await expectNoAxeViolations(section);
  });
});

describe("the preview settings and states", () => {
  it("shows a lifetime in days when it is whole days", () => {
    expect(ttlDraft(168)).toEqual({ ttl_hours: "7", unit: "days" });
    expect(ttlDraft(36)).toEqual({ ttl_hours: "36", unit: "hours" });
    expect(lifetime(24)).toBe("1 day");
    expect(lifetime(1)).toBe("1 hour");
    expect(lifetime(36)).toBe("36 hours");
  });

  it("reads the form as the backend will", () => {
    const draft = previewDraftOf(null);
    expect(parsePreviewDraft({ ...draft, base_domain: "previews.example.com" })).toEqual({
      values: { base_domain: "previews.example.com", max_previews: 3, ttl_hours: 168, allow_bots: false, exclude_env: [] },
      errors: {},
    });
    expect(parsePreviewDraft({ ...draft, base_domain: "a.example.com", exclude_env: " A_1, _b\nA_1 ", allow_bots: true }).values).toMatchObject({
      allow_bots: true,
      exclude_env: ["A_1", "_b"],
    });
    expect(parsePreviewDraft({ ...draft, base_domain: "a.example.com", exclude_env: "OK, 9X, A-B" }).errors.exclude_env).toMatch(/^Not variable names: 9X, A-B\./);
    expect(envNamesOf("")).toEqual([]);
    expect(previewDraftOf({ ...SETTINGS, allow_bots: true, exclude_env: ["A", "B"] })).toMatchObject({ allow_bots: true, exclude_env: "A, B" });
    expect(samePreviewDraft({ ...draft, exclude_env: "A B" }, { ...draft, exclude_env: "A, B" })).toBe(true);
    expect(samePreviewDraft({ ...draft, allow_bots: true }, draft)).toBe(false);
    expect(parsePreviewDraft({ ...draft, base_domain: "https://x.example.com" }).errors.base_domain).toMatch(/no scheme/);
    expect(parsePreviewDraft({ ...draft, base_domain: "a.example.com", ttl_hours: "91" }).errors.ttl_hours).toBe("From 1 to 90 days.");
    expect(parsePreviewDraft({ ...draft, base_domain: "a.example.com", ttl_hours: "2161", unit: "hours" }).errors.ttl_hours).toBe(
      "From 1 to 2160 hours (90 days).",
    );
    expect(parsePreviewDraft({ ...draft, base_domain: "a.example.com", max_previews: "0" }).errors.max_previews).toBe(
      "From 1 to 20 previews at once.",
    );
  });

  it("places the backend's refusals by their words", () => {
    expect(previewFieldOf("Not a base domain for previews: 'x'")).toBe("base_domain");
    expect(previewFieldOf("verylong.example.com is too long to put previews under")).toBe("base_domain");
    expect(previewFieldOf("A preview lives from 1 hour to 90 days, not 3000 hours")).toBe("ttl_hours");
    expect(previewFieldOf("An application may have 1 to 20 previews at once, not 30")).toBe("max_previews");
    expect(previewFieldOf("Invalid variable name: '1X'")).toBe("exclude_env");
    expect(previewFieldOf("shop.example.com is not deployed from a git repository")).toBeNull();
  });

  it("draws each state with its own word, and an unknown one as it came", () => {
    expect(previewStatus("pending")).toMatchObject({ state: "deploying", label: "Pending" });
    expect(previewStatus("deploying")).toMatchObject({ state: "deploying", label: "Deploying" });
    expect(previewStatus("ready")).toMatchObject({ state: "running", label: "Ready" });
    expect(previewStatus("failed")).toMatchObject({ state: "failed", label: "Failed" });
    expect(previewStatus("paused")).toMatchObject({ state: "unknown", label: "Paused" });
  });

  it("reads the list again while a preview is on its way, and only then", () => {
    expect(previewsInterval({ previews: [READY] })).toBe(false);
    expect(previewsInterval({ previews: [READY, { ...FAILED, status: "deploying" }] })).toBe(5_000);
    expect(previewsInterval(undefined)).toBe(false);
  });

  it("holds the same rules and states, in Spanish", async () => {
    await setLocale("es");
    expect(lifetime(24, "es")).toBe("1 día");
    expect(lifetime(36, "es")).toBe("36 horas");
    const draft = previewDraftOf(null);
    expect(parsePreviewDraft({ ...draft, base_domain: "" }, "es").errors.base_domain).toMatch(/Indica el dominio/);
    expect(parsePreviewDraft({ ...draft, base_domain: "a.example.com", max_previews: "0" }, "es").errors.max_previews).toBe(
      "De 1 a 20 vistas previas a la vez.",
    );
    expect(previewStatus("ready", "es")).toMatchObject({ state: "running", label: "Lista" });
    expect(previewStatus("failed", "es")).toMatchObject({ state: "failed", label: "Fallida" });
  });
});
