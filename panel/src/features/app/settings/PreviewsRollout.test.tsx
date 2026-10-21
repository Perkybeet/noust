import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { setLocale } from "../../../app/locale";
import { renderConsole } from "../../../test/console";
import { APPS, SESSION, fakeBackend, json, signedInRoutes } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { DEFAULT_TEMPLATE, parsePreviewDraft, previewDraftOf, previewStatus, samePreviewDraft, templateExample, templateProblem } from "./previews";

// Under a busy machine a page that reads the app, its previews and their form takes longer than the default.
vi.setConfig({ testTimeout: 20_000 });

const DOMAIN = "shop.example.com";
const APP = { ...APPS[0], path: "/var/www/apps/shop-example-com", layout: "releases", keep_releases: 5 };
const ELEVATED = { ...SESSION, elevated_until: "2999-01-01T00:00:00+00:00" };
const PUT = `PUT /api/apps/${DOMAIN}/previews/settings`;

const SETTINGS = {
  base_domain: "previews.example.com",
  max_previews: 3,
  ttl_hours: 168,
  allow_bots: false,
  exclude_env: [] as string[],
  inherit_env: "allowlist",
  allow_env: [] as string[],
  data_mode: "empty",
  domain_template: null as string | null,
  noindex: true,
  fork_policy: "refuse",
  created_at: "2026-09-20T10:00:00+00:00",
  updated_at: "2026-09-20T10:00:00+00:00",
};

const IN_FIVE_DAYS = new Date(Date.now() + 5 * 86_400_000 + 3_600_000).toISOString();

const AWAITING = {
  domain: "pr-21-shop-example-com.previews.example.com",
  url: "https://pr-21-shop-example-com.previews.example.com",
  number: 21,
  kind: "pr",
  ref: null,
  databases: [] as string[],
  branch: "patch-1",
  head_sha: "abcdef0123456789",
  provider: "github",
  repository: "stranger/storefront",
  status: "awaiting",
  error: null,
  expires_at: IN_FIVE_DAYS,
  created_at: "2026-10-16T10:00:00+00:00",
  updated_at: "2026-10-16T10:00:00+00:00",
};

const ON = { domain: DOMAIN, enabled: true, settings: SETTINGS, previews: [] as object[], total: 0, has_database: true };

async function previewsOf(state: () => object, extra: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend({
    ...signedInRoutes(ELEVATED),
    [`GET /api/apps/${DOMAIN}`]: () => json(200, APP),
    "GET /api/certs": () => json(200, { certificates: [], total: 0 }),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
    [`GET /api/apps/${DOMAIN}/previews`]: () => json(200, state()),
    ...extra,
  });
  const harness = renderConsole(`/apps/${DOMAIN}/settings/previews`);
  const section = await screen.findByRole("region", { name: "Pull request previews" });
  return { ...harness, backend, section };
}

describe("what a preview is called", () => {
  it("shows the name a template gives and sends the template with the rest", async () => {
    const { user, backend, section } = await previewsOf(() => ON, {
      [PUT]: () => json(200, { ...SETTINGS, domain_template: "{{pr_id}}.{{domain}}" }),
    });

    const field = await within(section).findByRole("textbox", { name: /Name of a preview/ });
    expect(field).toHaveAttribute("placeholder", DEFAULT_TEMPLATE);
    expect(within(section).getByTestId("template-example")).toHaveTextContent("For example, pr-12-shop-example-com.previews.example.com");
    fireEvent.change(field, { target: { value: "{{pr_id}}.{{domain}}" } });
    expect(within(section).getByTestId("template-example")).toHaveTextContent("For example, 12.shop.example.com");
    await user.click(await within(section).findByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo(PUT)[0]?.body).toMatchObject({ domain_template: "{{pr_id}}.{{domain}}", noindex: true, fork_policy: "refuse" });
    });
  });

  it("refuses a template that would give two previews the same name, before asking", async () => {
    const { user, backend, section } = await previewsOf(() => ON);

    fireEvent.change(await within(section).findByRole("textbox", { name: /Name of a preview/ }), { target: { value: "{{name}}.{{base}}" } });
    await user.click(await within(section).findByRole("button", { name: "Save" }));

    expect(await within(section).findByText("The name needs {{pr_id}} or {{random}}, or two previews would share it.")).toBeInTheDocument();
    expect(backend.callsTo(PUT)).toHaveLength(0);
  });

  it("puts the backend's refusal of a template beside the field", async () => {
    const { user, section } = await previewsOf(() => ON, {
      [PUT]: () =>
        json(400, {
          error: "validation_error",
          detail: "Not a placeholder of a domain template: {{nope}}",
          hint: "Use {{pr_id}}, {{random}}, {{domain}}, {{name}}, {{base}}.",
          fields: { domain_template: "Not a placeholder of a domain template: {{nope}}" },
          output: null,
        }),
    });

    // Known placeholders pass the quick check, so the refusal is the backend's own (a name that does not fit).
    fireEvent.change(await within(section).findByRole("textbox", { name: /Name of a preview/ }), { target: { value: "{{pr_id}}-{{name}}-{{name}}-{{name}}.{{base}}" } });
    await user.click(await within(section).findByRole("button", { name: "Save" }));

    expect(await within(section).findByText("Not a placeholder of a domain template: {{nope}}")).toBeInTheDocument();
  });

  it("keeps search engines out unless told not to", async () => {
    const { user, backend, section } = await previewsOf(() => ON, { [PUT]: () => json(200, { ...SETTINGS, noindex: false }) });

    const box = await within(section).findByRole("checkbox", { name: "Keep search engines out" });
    expect(box).toBeChecked();
    await user.click(box);
    await user.click(await within(section).findByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo(PUT)[0]?.body).toMatchObject({ noindex: false });
    });
  });
});

describe("pull requests from forks", () => {
  it("refuses them unless told to wait for an approval, and warns when it will", async () => {
    const { user, backend, section } = await previewsOf(() => ON, { [PUT]: () => json(200, { ...SETTINGS, fork_policy: "approve" }) });

    expect(await within(section).findByRole("radio", { name: "Refuse them" })).toBeChecked();
    expect(within(section).queryByText(/An approved commit runs on this server with the variables previews are given/)).not.toBeInTheDocument();
    await user.click(within(section).getByRole("radio", { name: "Wait for my approval" }));
    expect(within(section).getByText(/An approved commit runs on this server with the variables previews are given/)).toBeInTheDocument();
    await user.click(await within(section).findByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo(PUT)[0]?.body).toMatchObject({ fork_policy: "approve" });
    });
  });

  it("shows a commit that waits, and approves it after asking", async () => {
    let state: object = { ...ON, settings: { ...SETTINGS, fork_policy: "approve" }, previews: [AWAITING], total: 1 };
    const { user, backend, section } = await previewsOf(() => state, {
      [`POST /api/apps/${DOMAIN}/previews/21/approve`]: () => {
        state = { ...ON, previews: [{ ...AWAITING, status: "pending" }], total: 1 };
        return json(202, { preview: { ...AWAITING, status: "pending" }, job_id: "j1" });
      },
    });

    const list = await within(section).findByRole("list", { name: "Previews" });
    expect(within(list).getByText("Awaiting approval")).toBeInTheDocument();
    expect(within(list).getByText(/waits for an operator/)).toBeInTheDocument();
    // Nothing of it has been built, so there is nothing to build again.
    expect(within(list).queryByRole("button", { name: /Redeploy/ })).not.toBeInTheDocument();
    await user.click(within(list).getByRole("button", { name: "Approve #21" }));
    const confirm = await screen.findByRole("alertdialog", { name: "Approve commit abcdef0 of #21?" });
    expect(backend.callsTo(`POST /api/apps/${DOMAIN}/previews/21/approve`)).toHaveLength(0);
    await user.click(within(confirm).getByRole("button", { name: "Approve and build" }));

    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${DOMAIN}/previews/21/approve`)).toHaveLength(1);
    });
    expect(await within(list).findByText("Pending")).toBeInTheDocument();
  });

  it("turns down a commit that waits", async () => {
    let state: object = { ...ON, previews: [AWAITING], total: 1 };
    const { user, backend, section } = await previewsOf(() => state, {
      [`POST /api/apps/${DOMAIN}/previews/21/reject`]: () => {
        state = { ...ON, previews: [], total: 0 };
        return json(200, AWAITING);
      },
    });

    await user.click(await within(section).findByRole("button", { name: "Reject #21" }));

    await waitFor(() => {
      expect(backend.callsTo(`POST /api/apps/${DOMAIN}/previews/21/reject`)).toHaveLength(1);
    });
    await waitFor(() => {
      expect(within(section).queryByText("Awaiting approval")).not.toBeInTheDocument();
    });
  });

  it("speaks Spanish", async () => {
    await previewsOf(() => ({ ...ON, previews: [AWAITING], total: 1 }));
    await screen.findByText("Awaiting approval");

    await act(() => setLocale("es"));

    expect(await screen.findByText("Esperando aprobación")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Aprobar #21" })).toBeInTheDocument();
  });
});

describe("a copy of the data", () => {
  it("offers a database that starts as a copy of the application's", async () => {
    const { user, backend, section } = await previewsOf(() => ON, { [PUT]: () => json(200, { ...SETTINGS, data_mode: "clone" }) });

    await user.click(await within(section).findByRole("radio", { name: "A copy of the database" }));
    await user.click(await within(section).findByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(backend.callsTo(PUT)[0]?.body).toMatchObject({ data_mode: "clone" });
    });
  });
});

describe("the settings model", () => {
  it("reads the new fields and falls back to the safe ones", () => {
    expect(previewDraftOf(null)).toMatchObject({ domain_template: "", noindex: true, fork_policy: "refuse", data_mode: "empty" });
    expect(previewDraftOf({ ...SETTINGS, domain_template: "{{random}}.{{base}}", noindex: false, fork_policy: "approve", data_mode: "clone" })).toMatchObject({
      domain_template: "{{random}}.{{base}}",
      noindex: false,
      fork_policy: "approve",
      data_mode: "clone",
    });
    // A word the console does not know is never taken for the permissive one.
    expect(previewDraftOf({ ...SETTINGS, fork_policy: "allow" }).fork_policy).toBe("refuse");
  });

  it("counts the new fields as changes", () => {
    const draft = previewDraftOf(SETTINGS);
    expect(samePreviewDraft(draft, { ...draft, domain_template: " " })).toBe(true);
    expect(samePreviewDraft(draft, { ...draft, noindex: false })).toBe(false);
    expect(samePreviewDraft(draft, { ...draft, fork_policy: "approve" })).toBe(false);
  });

  it("judges a template the way the backend does", () => {
    expect(templateProblem("")).toBeNull();
    expect(templateProblem("{{pr_id}}-{{name}}.{{base}}")).toBeNull();
    expect(templateProblem("{{ random }}.{{domain}}")).toBeNull();
    expect(templateProblem("{{name}}.{{base}}")).toMatch(/needs \{\{pr_id\}\} or \{\{random\}\}/);
    expect(templateProblem("{{pr_id}}.{{nope}}")).toBe("Unknown placeholder: {{nope}}.");
    expect(templateProblem(`{{pr_id}}${"x".repeat(200)}`)).toBe("At most 200 characters.");
  });

  it("sends an empty template to go back to the default, and the typed one trimmed", () => {
    const draft = { ...previewDraftOf(null), base_domain: "previews.example.com" };
    expect(parsePreviewDraft(draft).values?.domain_template).toBe("");
    expect(parsePreviewDraft({ ...draft, domain_template: "  {{random}}.{{base}} " }).values?.domain_template).toBe("{{random}}.{{base}}");
    expect(parsePreviewDraft({ ...draft, domain_template: "{{name}}" }).errors.domain_template).toBeDefined();
  });

  it("draws an example for pull request 12", () => {
    expect(templateExample("", "shop.example.com", "previews.example.com")).toBe("pr-12-shop-example-com.previews.example.com");
    expect(templateExample("{{random}}.{{base}}", "shop.example.com", "p.example.com")).toBe("k3x9f2ab.p.example.com");
    expect(templateExample("{{pr_id}}.{{base}}", "shop.example.com", "")).toBe("12.previews.example.com");
    expect(templateExample("{{pr_id}}.{{other}}", "shop.example.com", "p.example.com")).toBe("12.{{other}}");
  });

  it("tells a preview that waits for a person apart from one being built", () => {
    expect(previewStatus("awaiting")).toMatchObject({ label: "Awaiting approval", state: "stopped", attention: true });
    expect(previewStatus("pending").state).toBe("deploying");
  });
});
