import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../../test/fakes";
import type { RecordedCall, RouteHandler } from "../../../test/fakes";
import { screenWidth } from "../testRoutes";
import { audienceChanges, audienceLabel, effectiveAudience, storedAudience } from "./audience";

const DOMAIN = "shop.example.com";
const ENV_PATH = `/api/apps/${DOMAIN}/env`;

const MASKED = {
  NODE_ENV: "production",
  STRIPE_KEY: "***",
  API_URL: "https://shop.example.com/api",
  NEXT_PUBLIC_REGION: "eu-west",
};

const CLEAR = {
  NODE_ENV: "production",
  STRIPE_KEY: "sk_live_resolved_value",
  API_URL: "https://shop.example.com/api",
  NEXT_PUBLIC_REGION: "eu-west",
};

const SECRETS = {
  NODE_ENV: { secret: false, reason: "plain", marked: false },
  STRIPE_KEY: { secret: true, reason: "name", marked: false },
  API_URL: { secret: false, reason: "plain", marked: false },
  NEXT_PUBLIC_REGION: { secret: false, reason: "plain", marked: false },
};

const PREDEFINED = {
  NOUST_URL: "https://shop.example.com",
  NOUST_DOMAIN: "shop.example.com",
  NOUST_BRANCH: "main",
  NOUST_COMMIT: "a1b2c3d",
  NOUST_RELEASE: "20261012-101500",
};

function envRoute(extra: Record<string, unknown> = {}): RouteHandler {
  return (call: RecordedCall) => {
    const unmasked = call.search.get("unmask") === "true";
    return json(200, {
      domain: DOMAIN,
      variables: unmasked ? CLEAR : MASKED,
      unmasked,
      secrets: SECRETS,
      audience: { NEXT_PUBLIC_REGION: "build" },
      references: { STRIPE_KEY: "{{secret:vault/shop/stripe#key}}", API_URL: "{{app.URL}}/api" },
      predefined: PREDEFINED,
      ...extra,
    });
  };
}

async function environmentTab(extra: Record<string, RouteHandler> = {}) {
  screenWidth(1440);
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/certs": () => json(200, { certificates: [], total: 0 }),
    "GET /api/jobs/active": () => json(200, { jobs: [], total: 0, active: 0 }),
    [`GET ${ENV_PATH}`]: envRoute(),
    ...extra,
  });
  const harness = renderConsole(`/apps/${DOMAIN}/environment`);
  await waitFor(() => {
    expect(screen.getByRole("table", { name: `Environment variables of ${DOMAIN}` })).not.toHaveAttribute("aria-busy");
  });
  return { ...harness, backend };
}

async function rowAction(user: ReturnType<typeof renderConsole>["user"], name: string, item: string | RegExp): Promise<void> {
  await user.click(screen.getByRole("button", { name: `Actions for ${name}` }));
  await user.click(await screen.findByRole("menuitem", { name: item }));
}

describe("who gets a variable", () => {
  it("reads a name the server does not list as both, and counts only what differs from it", () => {
    const recorded = { NEXT_PUBLIC_REGION: "build" };
    expect(storedAudience(recorded, "NODE_ENV")).toBe("both");
    expect(storedAudience(recorded, "NEXT_PUBLIC_REGION")).toBe("build");
    expect(storedAudience({ X: "unheard-of" }, "X")).toBe("both");
    const staged = new Map([
      ["NEXT_PUBLIC_REGION", "build" as const],
      ["NODE_ENV", "runtime" as const],
      ["GONE", "build" as const],
    ]);
    expect(effectiveAudience(recorded, staged, "NODE_ENV")).toBe("runtime");
    // A removed variable takes its audience with it; a choice equal to the stored one is not a change.
    expect(audienceChanges(recorded, staged, new Set(["NODE_ENV", "NEXT_PUBLIC_REGION"]))).toEqual([{ name: "NODE_ENV", before: "both", after: "runtime" }]);
  });

  it("names the three audiences in both languages", async () => {
    expect(audienceLabel("both", "en")).toBe("Build and runtime");
    expect(audienceLabel("build", "en")).toBe("Build only");
    // Spanish is its own chunk: it is there once the language has been chosen.
    await act(() => setLocale("es"));
    expect(audienceLabel("runtime", "es")).toBe("Solo ejecución");
  });
});

describe("the environment tab and what Noust resolves", () => {
  it("shows a reference as written, not as what it resolved to, and never offers to reveal it", async () => {
    await environmentTab();
    const table = screen.getByRole("table", { name: `Environment variables of ${DOMAIN}` });
    expect(within(table).getByText("{{secret:vault/shop/stripe#key}}")).toBeInTheDocument();
    expect(within(table).getByText("{{app.URL}}/api")).toBeInTheDocument();
    // The resolved secret is masked in the file's own listing and stays out of the page.
    expect(within(table).queryByText("sk_live_resolved_value")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Reveal the value of STRIPE_KEY" })).not.toBeInTheDocument();
  });

  it("marks a build-only variable, and lists what Noust itself gives apart from the file's own", async () => {
    await environmentTab();
    const table = screen.getByRole("table", { name: `Environment variables of ${DOMAIN}` });
    expect(within(table).getByText("Build only")).toBeInTheDocument();
    const given = screen.getByRole("region", { name: "Given by Noust" });
    expect(within(given).getByText("NOUST_URL")).toBeInTheDocument();
    expect(within(given).getByText("a1b2c3d")).toBeInTheDocument();
    // They are not rows of the file: nothing there can be edited or removed.
    expect(within(table).queryByText("NOUST_URL")).not.toBeInTheDocument();
  });

  it("edits a reference from its expression without asking to read anything in clear", async () => {
    const { user, backend } = await environmentTab();
    await rowAction(user, "STRIPE_KEY", "Edit");
    const dialog = await screen.findByRole("dialog", { name: "Edit STRIPE_KEY" });
    expect(within(dialog).getByLabelText("Value")).toHaveValue("{{secret:vault/shop/stripe#key}}");
    expect(backend.calls.filter((call) => call.search.get("unmask") === "true")).toHaveLength(0);
  });

  it("stages a change of who gets a variable, and saves it with the values in one request", async () => {
    const { user, backend } = await environmentTab({
      [`PUT ${ENV_PATH}`]: () => json(200, { domain: DOMAIN, restart_required: true }),
    });
    await rowAction(user, "NODE_ENV", "Runtime only");
    expect(screen.getByText("1 unsaved change")).toBeInTheDocument();
    expect(within(screen.getByRole("table", { name: `Environment variables of ${DOMAIN}` })).getByText("Runtime only, unsaved")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Review and save" }));
    const review = await screen.findByRole("dialog", { name: "Review changes" });
    expect(within(review).getByText("Only who gets the variables changes. The values stay as they are.")).toBeInTheDocument();
    await user.click(within(review).getByRole("button", { name: "Save changes" }));

    await waitFor(() => {
      expect(backend.callsTo(`PUT ${ENV_PATH}`)[0]?.body).toEqual({ variables: CLEAR, audience: { NODE_ENV: "runtime" } });
    });
  });

  it("lets the dialog choose who gets a new variable", async () => {
    const { user, backend } = await environmentTab({
      [`PUT ${ENV_PATH}`]: () => json(200, { domain: DOMAIN, restart_required: true }),
    });
    await user.click(screen.getByRole("button", { name: "Add variable" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a variable" });
    await user.type(within(dialog).getByLabelText("Name"), "COMMIT");
    await user.type(within(dialog).getByLabelText("Value"), "x");
    await user.click(within(dialog).getByRole("combobox", { name: "Given to" }));
    await user.click(await screen.findByRole("option", { name: "Build only" }));
    await user.click(within(dialog).getByRole("button", { name: "Add variable" }));

    await user.click(screen.getByRole("button", { name: "Review and save" }));
    const review = await screen.findByRole("dialog", { name: "Review changes" });
    await user.click(within(review).getByRole("button", { name: "Save changes" }));
    await waitFor(() => {
      expect(backend.callsTo(`PUT ${ENV_PATH}`)[0]?.body).toEqual({ variables: { ...CLEAR, COMMIT: "x" }, audience: { COMMIT: "build" } });
    });
  });

  it("undoes a staged audience with the row's own undo, and discards it with the rest", async () => {
    const { user } = await environmentTab();
    await rowAction(user, "NODE_ENV", "Build only");
    expect(screen.getByText("1 unsaved change")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Discard" }));
    expect(screen.getByRole("region", { name: "Unsaved changes" })).toHaveTextContent("No unsaved changes");
  });

  it("resolves the references again on request, asking nothing of the file's own variables", async () => {
    const { user, backend } = await environmentTab({
      [`POST ${ENV_PATH}/resolve`]: () => json(200, { domain: DOMAIN, changed: ["STRIPE_KEY"], restart_required: true }),
    });
    await user.click(screen.getByRole("button", { name: "Resolve references" }));
    await waitFor(() => {
      expect(backend.callsTo(`POST ${ENV_PATH}/resolve`)).toHaveLength(1);
    });
    expect(backend.callsTo(`PUT ${ENV_PATH}`)).toHaveLength(0);
  });

  it("offers no resolving when the file holds no reference", async () => {
    await environmentTab({ [`GET ${ENV_PATH}`]: envRoute({ references: {}, audience: {} }) });
    expect(screen.queryByRole("button", { name: "Resolve references" })).not.toBeInTheDocument();
  });

  it("speaks Spanish and has no accessibility violations", { timeout: 30_000 }, async () => {
    const { user } = await environmentTab();
    await act(() => setLocale("es"));
    expect(screen.getByRole("button", { name: "Resolver referencias" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Entregadas por Noust" })).toBeInTheDocument();
    expect(within(screen.getByRole("table", { name: `Variables de entorno de ${DOMAIN}` })).getByText("Solo compilación")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));

    await user.click(screen.getByRole("button", { name: "Añadir variable" }));
    const dialog = await screen.findByRole("dialog", { name: "Añadir una variable" });
    expect(within(dialog).getByRole("combobox", { name: "Se entrega a" })).toBeInTheDocument();
    await expectNoAxeViolations(dialog);
  });
});
