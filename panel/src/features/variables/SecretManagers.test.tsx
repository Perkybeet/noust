import { act, configure, screen, waitFor, within } from "@testing-library/react";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { screenWidth } from "../app/testRoutes";

vi.setConfig({ testTimeout: 20_000 });
beforeAll(() => {
  configure({ asyncUtilTimeout: 5_000 });
});
afterAll(() => {
  configure({ asyncUtilTimeout: 1_000 });
});

const KINDS = {
  kinds: [
    {
      kind: "vault",
      default_address: null,
      required: { mount: "The KV mount, such as secret" },
      optional: { namespace: "A Vault Enterprise namespace" },
      path: "secret/path#field",
    },
    { kind: "doppler", default_address: "https://api.doppler.com", required: { project: "The Doppler project" }, optional: {}, path: "the secret's name" },
  ],
};

const PROVIDERS = {
  total: 2,
  providers: [
    { name: "vault", kind: "vault", address: "https://vault.example.com:8200", settings: { mount: "secret" }, team: null, has_token: true, reference: "{{secret:vault/PATH}}", created_at: "2026-10-01T10:00:00+00:00", created_by: "ana" },
    { name: "doppler", kind: "doppler", address: null, settings: { project: "shop" }, team: "acme", has_token: false, reference: "{{secret:doppler/NAME}}", created_at: "2026-10-02T10:00:00+00:00", created_by: "ana" },
  ],
};

function page(extra: Record<string, RouteHandler> = {}) {
  screenWidth(1440);
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/secret-providers": () => json(200, PROVIDERS),
    "GET /api/secret-providers/kinds": () => json(200, KINDS),
    ...extra,
  });
  const harness = renderConsole("/settings/secret-managers");
  return { ...harness, backend };
}

async function loadedTable(): Promise<HTMLElement> {
  await screen.findByRole("table", { name: "Secret managers set up on this server" });
  await waitFor(() => {
    expect(screen.getByRole("table", { name: "Secret managers set up on this server" })).not.toHaveAttribute("aria-busy");
  });
  return screen.getByRole("table", { name: "Secret managers set up on this server" });
}

describe("the secret managers", () => {
  it("lists each manager with where it is, who may name it and whether its token is there", async () => {
    const { container } = page();
    const table = await loadedTable();
    expect(within(table).getByText("https://vault.example.com:8200")).toBeInTheDocument();
    expect(within(table).getByText("Hosted service")).toBeInTheDocument();
    expect(within(table).getByText("Stored")).toBeInTheDocument();
    expect(within(table).getByText("Missing")).toBeInTheDocument();
    expect(within(table).getByText("HashiCorp Vault")).toBeInTheDocument();
    // How to refer to a secret is said where the managers are.
    expect(screen.getByText("{{secret:NAME/path}}")).toBeInTheDocument();
    await expectNoAxeViolations(container);
  });

  it("tests a manager and says what it proved", async () => {
    const { user, backend } = page({
      "POST /api/secret-providers/vault/test": () => json(200, { name: "vault", message: "Vault 1.17 answered and the token is valid" }),
    });
    await loadedTable();
    await user.click(screen.getByRole("button", { name: "Actions for vault" }));
    await user.click(await screen.findByRole("menuitem", { name: "Test connection" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/secret-providers/vault/test")).toHaveLength(1);
    });
    expect(await screen.findByText("vault answered and accepted its token")).toBeInTheDocument();
    expect(screen.getByText("Vault 1.17 answered and the token is valid")).toBeInTheDocument();
  });

  it("shows why a test failed in the server's own words", async () => {
    const { user } = page({
      "POST /api/secret-providers/vault/test": () => problem(502, "integrationerror", "vault answered 403: permission denied"),
    });
    await loadedTable();
    await user.click(screen.getByRole("button", { name: "Actions for vault" }));
    await user.click(await screen.findByRole("menuitem", { name: "Test connection" }));
    expect(await screen.findByText("vault answered 403: permission denied")).toBeInTheDocument();
  });

  it("adds a manager with the settings of its kind and a token that is sent once", async () => {
    const { user, backend } = page({
      "PUT /api/secret-providers/infra": () => json(200, { ...PROVIDERS.providers[0], name: "infra" }),
    });
    await loadedTable();
    await user.click(screen.getByRole("button", { name: "Add secret manager" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a secret manager" });
    await user.type(within(dialog).getByLabelText("Name"), "infra");
    await user.type(within(dialog).getByLabelText("Address"), "https://vault.infra.example.com");
    await user.type(within(dialog).getByLabelText("mount"), "kv");
    await user.type(within(dialog).getByLabelText("Token"), "s.very-secret");
    await user.click(within(dialog).getByRole("button", { name: "Add secret manager" }));

    await waitFor(() => {
      expect(backend.callsTo("PUT /api/secret-providers/infra")[0]?.body).toEqual({
        kind: "vault",
        address: "https://vault.infra.example.com",
        settings: { mount: "kv" },
        token: "s.very-secret",
      });
    });
  });

  it("asks for what a kind needs before sending anything", async () => {
    const { user, backend } = page();
    await loadedTable();
    await user.click(screen.getByRole("button", { name: "Add secret manager" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a secret manager" });
    await user.click(within(dialog).getByRole("button", { name: "Add secret manager" }));
    expect(await within(dialog).findByText("Give it a name.")).toBeInTheDocument();
    expect(within(dialog).getByText("This manager has no hosted address: say where it is.")).toBeInTheDocument();
    expect(within(dialog).getByText("A token is needed.")).toBeInTheDocument();
    expect(backend.calls.filter((call) => call.method === "PUT")).toHaveLength(0);
  });

  it("changes a manager's settings and keeps the token it has when none is typed", async () => {
    const { user, backend } = page({
      "PUT /api/secret-providers/vault": () => json(200, PROVIDERS.providers[0]),
    });
    await loadedTable();
    await user.click(screen.getByRole("button", { name: "Actions for vault" }));
    await user.click(await screen.findByRole("menuitem", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog", { name: "Edit vault" });
    expect(within(dialog).getByLabelText("Name")).toBeDisabled();
    expect(within(dialog).getByLabelText(/^Token/)).toHaveValue("");
    await user.clear(within(dialog).getByLabelText("mount"));
    await user.type(within(dialog).getByLabelText("mount"), "kv2");
    await user.click(within(dialog).getByRole("button", { name: "Save changes" }));
    await waitFor(() => {
      expect(backend.callsTo("PUT /api/secret-providers/vault")[0]?.body).toEqual({
        kind: "vault",
        address: "https://vault.example.com:8200",
        settings: { mount: "kv2" },
      });
    });
  });

  it("shows the server's refusal in the dialog, as it worded it", async () => {
    const { user } = page({
      "PUT /api/secret-providers/infra": () => problem(422, "validation_error", "The address of a secret manager must be https"),
    });
    await loadedTable();
    await user.click(screen.getByRole("button", { name: "Add secret manager" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a secret manager" });
    await user.type(within(dialog).getByLabelText("Name"), "infra");
    await user.type(within(dialog).getByLabelText("Address"), "http://vault.example.com");
    await user.type(within(dialog).getByLabelText("mount"), "kv");
    await user.type(within(dialog).getByLabelText("Token"), "t");
    await user.click(within(dialog).getByRole("button", { name: "Add secret manager" }));
    expect(await within(dialog).findByText("The address of a secret manager must be https")).toBeInTheDocument();
  });

  it("removes a manager after one question that says what stops working", async () => {
    const { user, backend } = page({
      "DELETE /api/secret-providers/vault": () => json(200, { name: "vault" }),
    });
    await loadedTable();
    await user.click(screen.getByRole("button", { name: "Actions for vault" }));
    await user.click(await screen.findByRole("menuitem", { name: "Remove" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Remove vault?" });
    expect(within(dialog).getByText(/Variables that refer to it fail their next deploy/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Remove secret manager" }));
    await waitFor(() => {
      expect(backend.callsTo("DELETE /api/secret-providers/vault")).toHaveLength(1);
    });
  });

  it("invites a first manager when there is none", async () => {
    page({ "GET /api/secret-providers": () => json(200, { providers: [], total: 0 }) });
    expect(await screen.findByRole("heading", { name: "No secret manager" })).toBeInTheDocument();
  });

  it("is in Spanish, with no accessibility violations", async () => {
    const { user } = page();
    await loadedTable();
    await act(() => setLocale("es"));
    expect(screen.getByRole("table", { name: "Gestores de secretos configurados en este servidor" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Añadir gestor de secretos" }));
    const dialog = await screen.findByRole("dialog", { name: "Añadir un gestor de secretos" });
    await expectNoAxeViolations(dialog);
  });
});
