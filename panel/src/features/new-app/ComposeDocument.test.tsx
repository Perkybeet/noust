import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";

const DOCUMENT = "services:\n  web:\n    image: ghcr.io/acme/web:1.4\n    ports:\n      - '8090:80'\n    environment:\n      SECRET_KEY: ${SECRET_KEY}\n";

const ANALYSIS = {
  deployable: true,
  services: [{ name: "web", image: "ghcr.io/acme/web:1.4", publishes: ["8090:80/tcp"], depends_on: [] }],
  variables: [{ name: "SECRET_KEY", default: null, required: true, message: null, services: ["web"] }],
  web: ["web"],
  refused: [],
  warned: ["web publishes port 8090 on every interface"],
};

const REFUSED = { ...ANALYSIS, deployable: false, refused: ["web runs as root on the host (privileged: true)"], warned: [] };

const APP_TYPES = { types: [{ type: "docker-compose", name: "Docker Compose", default_port: 3000 }] };

const JOB = {
  id: "c0ffee01",
  type: "deploy",
  name: "Deploy stack.example.com",
  description: "Deploying",
  status: "running",
  progress: 10,
  total_steps: 100,
  current_step: "Starting the stack",
  created_at: "2026-10-06T10:21:13",
  deployment_id: null,
  logs: [],
  metadata: { domain: "stack.example.com" },
};

function wizard(extra: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/config/webserver": () => json(200, { webserver: "nginx" }),
    "GET /api/apps/types": () => json(200, APP_TYPES),
    "GET /api/domains/dns": (call) =>
      json(200, { domain: call.search.get("name"), expected_addresses: ["203.0.113.10"], resolved_addresses: ["203.0.113.10"], points_here: true }),
    "POST /api/compose/analyze": () => json(200, ANALYSIS),
    "POST /api/apps": () => json(202, { job_id: JOB.id, status: "pending", message: "Queued", job: JOB }),
    "GET /api/deployments": () => json(200, { items: [], total: 0, next_before_id: null }),
    [`GET /api/jobs/${JOB.id}`]: () => json(200, JOB),
    "GET /api/jobs/active": () => json(200, { jobs: [JOB], total: 1, active: 1 }),
    ...extra,
  });
  return { backend, harness: renderConsole("/apps/new") };
}

type User = ReturnType<typeof renderConsole>["user"];

async function openComposeMode(user: User) {
  await screen.findByRole("heading", { level: 1, name: "New application" });
  await user.click(await screen.findByRole("radio", { name: "Compose" }));
  return screen.findByRole("textbox", { name: /Compose file/ });
}

describe("a Docker Compose file typed into the wizard", { timeout: 60_000 }, () => {
  it("is looked at first, and shows what it is before anything goes on", async () => {
    const { backend, harness } = wizard();
    const { user } = harness;
    const field = await openComposeMode(user);

    await user.click(field);
    await user.paste(DOCUMENT);
    await user.click(screen.getByRole("button", { name: "Look at it" }));

    const services = await screen.findByRole("list", { name: "Services in the file" });
    expect(within(services).getByText("web")).toBeInTheDocument();
    expect(within(services).getByText("8090:80/tcp")).toBeInTheDocument();
    expect(screen.getByText("web publishes port 8090 on every interface")).toBeInTheDocument();
    expect(screen.getByText("It reads 1 variable; SECRET_KEY needs a value.")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/compose/analyze")[0]?.body).toEqual({ document: DOCUMENT });
    // Looking changes nothing: the next press is Continue, and nothing was created.
    expect(screen.getByRole("button", { name: "Continue" })).toBeInTheDocument();
    expect(backend.callsTo("POST /api/apps")).toHaveLength(0);
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("says why a file cannot be deployed, with the server's words, and does not go on", async () => {
    const { harness } = wizard({ "POST /api/compose/analyze": () => json(200, REFUSED) });
    const { user } = harness;
    const field = await openComposeMode(user);

    await user.click(field);
    await user.paste(DOCUMENT);
    await user.click(screen.getByRole("button", { name: "Look at it" }));

    expect(await screen.findByText("This file cannot be deployed as it is")).toBeInTheDocument();
    expect(screen.getByText("web runs as root on the host (privileged: true)")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Continue" })).not.toBeInTheDocument();
  });

  it("shows a file that is not Compose as the server refused it", async () => {
    const { harness } = wizard({ "POST /api/compose/analyze": () => problem(400, "validationerror", "The document has no services", { hint: "A Compose file starts with a services: key." }) });
    const { user } = harness;
    const field = await openComposeMode(user);

    await user.click(field);
    await user.paste("hello");
    await user.click(screen.getByRole("button", { name: "Look at it" }));

    expect(await screen.findByText("The document has no services")).toBeInTheDocument();
  });

  it("asks for a file before looking at nothing", async () => {
    const { backend, harness } = wizard();
    const { user } = harness;
    await openComposeMode(user);

    await user.click(screen.getByRole("button", { name: "Look at it" }));

    expect(backend.callsTo("POST /api/compose/analyze")).toHaveLength(0);
    expect(await screen.findByText("Paste a Compose file first.")).toBeInTheDocument();
  });

  it("deploys the document as the application's source, with no repository", async () => {
    const { backend, harness } = wizard();
    const { user } = harness;
    const field = await openComposeMode(user);
    await user.click(field);
    await user.paste(DOCUMENT);
    await user.click(screen.getByRole("button", { name: "Look at it" }));
    await screen.findByRole("list", { name: "Services in the file" });
    await user.click(screen.getByRole("button", { name: "Continue" }));

    await screen.findByRole("heading", { level: 2, name: "Address" });
    await user.type(screen.getByLabelText("Domain"), "stack.example.com");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    // The variable the file needs is asked on the step that asks for variables.
    await screen.findByRole("heading", { level: 2, name: "Variables" });
    await user.type(screen.getByLabelText(/^SECRET_KEY/), "s3cret");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    await user.click(await screen.findByRole("button", { name: "Deploy stack.example.com" }));
    await waitFor(() => expect(backend.callsTo("POST /api/apps")).toHaveLength(1));
    const body = backend.callsTo("POST /api/apps")[0]?.body as Record<string, unknown>;
    expect(body).toMatchObject({ domain: "stack.example.com", app_type: "docker-compose", compose_document: DOCUMENT, env_vars: { SECRET_KEY: "s3cret" } });
    expect(body).not.toHaveProperty("source");
  });

  describe("a Coolify template", () => {
    const TEMPLATE = "# port: 3000\nservices:\n  wiki:\n    image: example/wiki:2.1\n    environment:\n      - SERVICE_URL_WIKI_3000\n      - BASE_URL=$SERVICE_URL_WIKI\n      - DB_PASSWORD=$SERVICE_PASSWORD_DB\n";
    const CONVERTED = "# Converted by Noust from a Coolify service template.\nservices:\n  wiki:\n    image: example/wiki:2.1\n    environment:\n    - SERVICE_URL_WIKI=${SERVICE_URL_WIKI}\n    - BASE_URL=${SERVICE_URL_WIKI}\n    - DB_PASSWORD=${SERVICE_PASSWORD_DB}\n    ports:\n    - 127.0.0.1:${PORT:-3000}:3000\n";
    const IMPORTED = {
      document: CONVERTED,
      values: { SERVICE_PASSWORD_DB: "G".repeat(32) },
      variables: [
        { name: "SERVICE_PASSWORD_DB", origin: "generated", secret: true, note: "" },
        { name: "SERVICE_URL_WIKI", origin: "ask", secret: false, note: "The address of the stack: give the domain, or set SERVICE_URL_WIKI" },
      ],
      routed_service: "wiki",
      routed_port: 3000,
      metadata: {},
      adjustments: ["wiki is published on 127.0.0.1:${PORT:-3000}:3000 for the web server"],
      warnings: ["A volume of /entrypoint.sh carries inline file content (a Coolify extension); put the file next to the document and mount it"],
      analysis: {
        deployable: true,
        services: [{ name: "wiki", image: "example/wiki:2.1", publishes: ["127.0.0.1:3000:3000/tcp"], depends_on: [] }],
        variables: [
          { name: "SERVICE_URL_WIKI", default: null, required: true, message: null, services: ["wiki"] },
          { name: "SERVICE_PASSWORD_DB", default: null, required: true, message: null, services: ["wiki"] },
          { name: "PORT", default: "3000", required: false, message: null, services: ["wiki"] },
        ],
        web: ["wiki"],
        refused: [],
        warned: [],
      },
    };

    it("is converted in place, says what it changed and left, and is already looked at", async () => {
      const { backend, harness } = wizard({ "POST /api/compose/import": () => json(200, IMPORTED) });
      const { user } = harness;
      const field = await openComposeMode(user);
      await user.click(field);
      await user.paste(TEMPLATE);
      await user.click(screen.getByRole("button", { name: "Convert a Coolify template" }));

      expect(await screen.findByText("Converted from a Coolify template")).toBeInTheDocument();
      expect(backend.callsTo("POST /api/compose/import")[0]?.body).toEqual({ document: TEMPLATE, format: "coolify" });
      expect(screen.getByRole("textbox", { name: /Compose file/ })).toHaveValue(CONVERTED);
      expect(screen.getByText("1 value was made here. The secret stays in this page until you deploy and is not shown again.")).toBeInTheDocument();
      expect(screen.getByRole("list", { name: "What was changed" })).toHaveTextContent("wiki is published on 127.0.0.1");
      expect(screen.getByRole("list", { name: "What was not translated" })).toHaveTextContent("inline file content");
      // The conversion already looked at it: the next press is Continue.
      expect(await screen.findByRole("list", { name: "Services in the file" })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Continue" })).toBeInTheDocument();
      expect(backend.callsTo("POST /api/compose/analyze")).toHaveLength(0);
      await expectNoAxeViolations(screen.getByRole("main"));
    });

    it("deploys with the secret it made and the address of the domain chosen, asking for neither", async () => {
      const { backend, harness } = wizard({ "POST /api/compose/import": () => json(200, IMPORTED) });
      const { user } = harness;
      const field = await openComposeMode(user);
      await user.click(field);
      await user.paste(TEMPLATE);
      await user.click(screen.getByRole("button", { name: "Convert a Coolify template" }));
      await screen.findByRole("list", { name: "Services in the file" });
      await user.click(screen.getByRole("button", { name: "Continue" }));

      await screen.findByRole("heading", { level: 2, name: "Address" });
      await user.type(screen.getByLabelText("Domain"), "wiki.example.com");
      await user.click(screen.getByRole("button", { name: "Continue" }));
      // Nothing is required any more: the secret was made and the address comes from the domain.
      await user.click(await screen.findByRole("button", { name: "Deploy wiki.example.com" }));

      await waitFor(() => expect(backend.callsTo("POST /api/apps")).toHaveLength(1));
      const body = backend.callsTo("POST /api/apps")[0]?.body as Record<string, unknown>;
      expect(body["compose_document"]).toBe(CONVERTED);
      expect(body["env_vars"]).toMatchObject({ SERVICE_PASSWORD_DB: "G".repeat(32), SERVICE_URL_WIKI: "https://wiki.example.com" });
    });

    it("shows the server's words when the text is not a Compose template", async () => {
      const { harness } = wizard({
        "POST /api/compose/import": () => problem(400, "validationerror", "That is not a Compose template: it has no services", { hint: "A Coolify template is a Compose file: services: at the top." }),
      });
      const { user } = harness;
      const field = await openComposeMode(user);
      await user.click(field);
      await user.paste("hello");
      await user.click(screen.getByRole("button", { name: "Convert a Coolify template" }));

      expect(await screen.findByText("That is not a Compose template: it has no services")).toBeInTheDocument();
    });

    it("is not offered for an empty field", async () => {
      const { harness } = wizard();
      await openComposeMode(harness.user);

      expect(screen.getByRole("button", { name: "Convert a Coolify template" })).toBeDisabled();
    });
  });

  it("speaks Spanish", async () => {
    const { harness } = wizard();
    await openComposeMode(harness.user);

    await act(() => setLocale("es"));

    expect(await screen.findByRole("button", { name: "Mirarlo" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Convertir una plantilla de Coolify" })).toBeInTheDocument();
    expect(screen.getByText(/Un docker-compose.yml escrito o pegado aquí/)).toBeInTheDocument();
  });
});
