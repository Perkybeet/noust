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

  it("speaks Spanish", async () => {
    const { harness } = wizard();
    await openComposeMode(harness.user);

    await act(() => setLocale("es"));

    expect(await screen.findByRole("button", { name: "Mirarlo" })).toBeInTheDocument();
    expect(screen.getByText(/Un docker-compose.yml escrito o pegado aquí/)).toBeInTheDocument();
  });
});
