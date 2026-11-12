import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../test/fakes";
import type { Inspection } from "./wizard";

/** A sentence of the server's, as `POST /api/apps/inspect` sends it: the code, its values and its English beside them. */
const said = (code: string, text: string, params: Record<string, string> = {}, hint: string | null = null) => ({ code, params, text, hint });

const SERVER = { form: "server", status: "yes", via: null, reason: null } as const;
const CONTAINER = { form: "container", status: "yes", reason: null } as const;

/** The cases of the approved mock-up, as the server says them. */
const NODE_WITH_DOCKERFILE: Inspection = {
  app_type: "nodejs",
  detected_types: ["nodejs", "container"],
  package_manager: "npm",
  install_command: ["npm", "ci"],
  build_command: [],
  start_command: "npm start",
  default_port: 3000,
  env_keys: [],
  branch: "main",
  commit: "ab12cd3",
  compatible: true,
  ways: {
    choice: true,
    proposed: "server",
    why: said(
      "how_it_runs.why.server_dockerfile",
      "Noust proposes running it directly on the server: it is a Node.js project and starts with its own command. It has a Dockerfile, so it can also run in a container.",
      { type: "Node.js" },
    ),
    server: { ...SERVER, app_type: "nodejs" },
    // The Dockerfile exposes its own port, not Node.js's 3000.
    container: { ...CONTAINER, app_type: "container", via: "dockerfile", port: 8123 },
  },
};

const GO_WITH_DOCKERFILE: Inspection = {
  ...NODE_WITH_DOCKERFILE,
  app_type: "container",
  detected_types: ["container"],
  package_manager: null,
  install_command: [],
  start_command: "",
  ways: {
    choice: true,
    proposed: "container",
    why: said("how_it_runs.why.container_dockerfile", "Noust proposes a container: the repository brings its Dockerfile.", { type: "Go" }),
    server: { ...SERVER, app_type: "go" },
    container: { ...CONTAINER, app_type: "container", via: "dockerfile" },
  },
};

const NO_BUILD = said(
  "how_it_runs.container.no_build",
  "It cannot run in a container yet: the repository has no Dockerfile and Railpack is not installed on this server.",
  { command: "noust setup railpack" },
  "Add a Dockerfile to the repository, or install Railpack on this server (noust setup railpack), which builds without one.",
);

const PYTHON: Inspection = {
  ...NODE_WITH_DOCKERFILE,
  app_type: "python",
  detected_types: ["python"],
  package_manager: null,
  install_command: ["pip", "install", "-r", "requirements.txt"],
  start_command: "gunicorn app:app",
  default_port: 8000,
  ways: {
    choice: false,
    proposed: "server",
    why: said("how_it_runs.why.server_only", "Noust proposes running it directly on the server: it is a Python project.", { type: "Python (Django/Flask/FastAPI)" }),
    server: { ...SERVER, app_type: "python" },
    container: { form: "container", status: "no", app_type: null, via: null, reason: NO_BUILD },
  },
};

const PYTHON_WITH_RAILPACK: Inspection = {
  ...PYTHON,
  ways: {
    choice: true,
    proposed: "server",
    why: said("how_it_runs.why.server_railpack", "Noust proposes running it directly on the server. Railpack is installed here.", { type: "Python (Django/Flask/FastAPI)" }),
    server: { ...SERVER, app_type: "python" },
    container: { ...CONTAINER, app_type: "container", via: "railpack" },
  },
};

const APP_TYPES = {
  types: [
    { type: "container", name: "Container", default_port: 3000 },
    { type: "docker-compose", name: "Docker Compose", default_port: 3000 },
    { type: "go", name: "Go", default_port: 8080 },
    { type: "nodejs", name: "Node.js", default_port: 3000 },
    { type: "python", name: "Python (Django/Flask/FastAPI)", default_port: 8000 },
  ],
};

const JOB = {
  id: "7c1d0e22",
  type: "deploy",
  name: "Deploy crate.example.com",
  description: "Deploying",
  status: "running",
  progress: 10,
  total_steps: 100,
  current_step: "Building",
  created_at: "2026-11-12T10:21:13",
  deployment_id: null,
  logs: [],
  metadata: { domain: "crate.example.com" },
};

function wizard(inspection: Inspection) {
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/config/webserver": () => json(200, { webserver: "nginx" }),
    "GET /api/apps/types": () => json(200, APP_TYPES),
    "GET /api/domains/dns": (call) =>
      json(200, { domain: call.search.get("name"), expected_addresses: ["203.0.113.10"], resolved_addresses: ["203.0.113.10"], points_here: true }),
    "POST /api/apps/inspect": () => json(200, inspection),
    "POST /api/apps": () => json(202, { job_id: JOB.id, status: "pending", message: "Deployment queued", job: JOB }),
    "GET /api/deployments": () => json(200, { items: [], total: 0, next_before_id: null }),
    [`GET /api/jobs/${JOB.id}`]: () => json(200, JOB),
    "GET /api/jobs/active": () => json(200, { jobs: [JOB], total: 1, active: 1 }),
  });
  return { backend, harness: renderConsole("/apps/new") };
}

type User = ReturnType<typeof renderConsole>["user"];

/** Source and address, and what the next step is: the Configuration step when it asks, the last one when it does not. */
async function toTheNextStep(user: User): Promise<void> {
  await screen.findByRole("heading", { level: 1, name: "New application" });
  await user.type(screen.getByLabelText("What to deploy"), "/var/www/src/shop");
  await user.click(screen.getByRole("button", { name: "Continue" }));
  await screen.findByRole("heading", { level: 2, name: "Address" });
  await user.type(screen.getByLabelText("Domain"), "crate.example.com");
  await user.click(screen.getByRole("button", { name: "Continue" }));
}

describe("how it runs, on the Configuration step (item 189)", () => {
  it("shows what Noust proposes and why, and both ways as cards, when a Dockerfile makes both possible", { timeout: 60_000 }, async () => {
    const { harness } = wizard(NODE_WITH_DOCKERFILE);
    await toTheNextStep(harness.user);

    // One type with a Dockerfile beside it: the step asks, and what it asks is how it runs.
    expect(await screen.findByRole("heading", { level: 2, name: "Configuration" })).toBeInTheDocument();
    const section = screen.getByRole("group", { name: "How it runs" });
    expect(within(section).getByText(/Noust proposes running it directly on the server: it is a Node\.js project/)).toBeInTheDocument();
    const server = within(section).getByRole("radio", { name: /Directly on the server/ });
    expect(server).toBeChecked();
    expect(within(section).getByRole("radio", { name: /In a container/ })).not.toBeChecked();
    // What Noust proposes is said on the card that is the proposal.
    expect(within(section).getByText("What Noust proposes")).toBeInTheDocument();
    // Said once, so nobody believes it can be changed on the fly.
    expect(within(section).getByText(/not on the fly: moving from one way to the other is deploying again and moving the domain/)).toBeInTheDocument();
    // The type is no longer the question: it waits under Advanced.
    expect(screen.queryByRole("combobox", { name: "App type" })).not.toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("deploys as a container, built with the Dockerfile, when that card is chosen", { timeout: 60_000 }, async () => {
    const { backend, harness } = wizard(NODE_WITH_DOCKERFILE);
    const { user } = harness;
    await toTheNextStep(user);
    await screen.findByRole("heading", { level: 2, name: "Configuration" });

    await user.click(screen.getByRole("radio", { name: /In a container/ }));
    // A container is looked at where it is built and run.
    expect(await screen.findByRole("radio", { name: /A Dockerfile/ })).toBeChecked();
    // The card is the choice: the type it takes is not "chosen over what was detected".
    expect(screen.queryByText(/The commands above are/)).not.toBeInTheDocument();
    // Where the process listens is what the Dockerfile says, not what the type would have said.
    expect(screen.getByText(/Empty uses 8123, the one the Dockerfile exposes/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Deploy" })).toBeInTheDocument();
    expect(within(screen.getByRole("main")).getByText("In a container")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Deploy crate.example.com" }));
    await waitFor(() => expect(backend.callsTo("POST /api/apps")).toHaveLength(1));
    expect(backend.callsTo("POST /api/apps")[0]?.body).toMatchObject({ app_type: "container", container: { method: "dockerfile" } });
  });

  it("deploys what it always did, as Node.js, when the card is left as it was proposed", { timeout: 60_000 }, async () => {
    const { backend, harness } = wizard(NODE_WITH_DOCKERFILE);
    const { user } = harness;
    await toTheNextStep(user);
    await screen.findByRole("heading", { level: 2, name: "Configuration" });

    await user.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByRole("heading", { level: 2, name: "Deploy" });
    await user.click(screen.getByRole("button", { name: "Deploy crate.example.com" }));
    await waitFor(() => expect(backend.callsTo("POST /api/apps")).toHaveLength(1));
    const body = backend.callsTo("POST /api/apps")[0]?.body as Record<string, unknown>;
    expect(body).toMatchObject({ app_type: "nodejs" });
    expect(body).not.toHaveProperty("container");
  });

  it("proposes a container for Go with a Dockerfile, as it always did, and offers running it directly as Go", { timeout: 60_000 }, async () => {
    const { backend, harness } = wizard(GO_WITH_DOCKERFILE);
    const { user } = harness;
    await toTheNextStep(user);
    await screen.findByRole("heading", { level: 2, name: "Configuration" });
    const section = screen.getByRole("group", { name: "How it runs" });
    expect(within(section).getByRole("radio", { name: /In a container/ })).toBeChecked();

    await user.click(within(section).getByRole("radio", { name: /Directly on the server/ }));
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByRole("heading", { level: 2, name: "Deploy" });
    await user.click(screen.getByRole("button", { name: "Deploy crate.example.com" }));
    await waitFor(() => expect(backend.callsTo("POST /api/apps")).toHaveLength(1));
    expect(backend.callsTo("POST /api/apps")[0]?.body).toMatchObject({ app_type: "go" });
  });

  it("builds a container with Railpack where the repository has no Dockerfile and Railpack is installed", { timeout: 60_000 }, async () => {
    const { backend, harness } = wizard(PYTHON_WITH_RAILPACK);
    const { user } = harness;
    await toTheNextStep(user);
    // One type and nothing to fix would be passed over: a choice is not.
    await screen.findByRole("heading", { level: 2, name: "Configuration" });

    await user.click(screen.getByRole("radio", { name: /In a container/ }));
    expect(await screen.findByRole("radio", { name: /Railpack/ })).toBeChecked();
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByRole("heading", { level: 2, name: "Deploy" });
    await user.click(screen.getByRole("button", { name: "Deploy crate.example.com" }));
    await waitFor(() => expect(backend.callsTo("POST /api/apps")).toHaveLength(1));
    expect(backend.callsTo("POST /api/apps")[0]?.body).toMatchObject({ app_type: "container", container: { method: "railpack" } });
  });

  it("is still a choice of the type, under Advanced, for whoever knows the repository better than detection", { timeout: 60_000 }, async () => {
    const { harness } = wizard(NODE_WITH_DOCKERFILE);
    const { user } = harness;
    await toTheNextStep(user);
    await screen.findByRole("heading", { level: 2, name: "Configuration" });

    await user.click(screen.getByText("Advanced"));
    expect(screen.getByRole("combobox", { name: "App type" })).toHaveTextContent("Node.js");
  });
});

describe("how it runs, when there is nothing to choose (item 189)", () => {
  it("is passed over, as a Python project with nothing to fix always was, and the last step says how it will run", { timeout: 60_000 }, async () => {
    const { harness } = wizard(PYTHON);
    await toTheNextStep(harness.user);

    expect(await screen.findByRole("heading", { level: 2, name: "Deploy" })).toBeInTheDocument();
    const main = screen.getByRole("main");
    expect(within(main).getByText("Directly on the server")).toBeInTheDocument();
    // The other way is not left unsaid when a Dockerfile would make it possible.
    expect(within(main).getByText(/It cannot run in a container yet: the repository has no Dockerfile and Railpack is not installed/)).toBeInTheDocument();
    // It says it was not asked, and offers to open what it passed over.
    expect(within(main).getAllByRole("button", { name: /Change/ }).length).toBeGreaterThan(0);
  });

  it("says why a container cannot be, and what would make it possible, where the server says no", { timeout: 60_000 }, async () => {
    const { harness } = wizard({ ...PYTHON, compatible: false, verdict: "This server does not have python3." });
    await toTheNextStep(harness.user);

    // Not deployable as it is: the step asks, and says first.
    await screen.findByRole("heading", { level: 2, name: "Configuration" });
    const section = screen.getByRole("group", { name: "How it runs" });
    expect(within(section).getAllByRole("radio")).toHaveLength(1);
    expect(within(section).getByText("In a container it is not possible yet")).toBeInTheDocument();
    expect(within(section).getByText(/the repository has no Dockerfile and Railpack is not installed on this server/)).toBeInTheDocument();
    expect(within(section).getByText("noust setup railpack")).toBeInTheDocument();
    // No choice, so nothing to say about changing one's mind.
    expect(within(section).queryByText(/not on the fly/)).not.toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("says it in one sentence, with no cards, for a Compose stack", { timeout: 60_000 }, async () => {
    const stack: Inspection = {
      ...NODE_WITH_DOCKERFILE,
      app_type: "docker-compose",
      detected_types: ["docker-compose", "container"],
      package_manager: null,
      ways: {
        choice: false,
        proposed: "container",
        why: said("how_it_runs.why.compose", "A Compose file always runs in containers: that is its form."),
        server: { form: "server", status: "hidden", app_type: null, via: null, reason: said("how_it_runs.server.nothing_recognised", "Nothing in it runs directly on the server.") },
        container: { ...CONTAINER, app_type: "docker-compose", via: "compose" },
      },
    };
    const { harness } = wizard(stack);
    await toTheNextStep(harness.user);

    // Two types match (the stack and the Dockerfile inside it): the step asks, and says it in one sentence.
    await screen.findByRole("heading", { level: 2, name: "Configuration" });
    const section = screen.getByRole("group", { name: "How it runs" });
    expect(within(section).queryAllByRole("radio")).toHaveLength(0);
    expect(within(section).getByText("In containers, as its Compose says")).toBeInTheDocument();
    expect(within(section).getByText("There is no other way: there is nothing to choose.")).toBeInTheDocument();
  });
});

describe("how it runs, in Spanish (item 189)", () => {
  it("says it in the console's words, and the server's sentences by their code", { timeout: 60_000 }, async () => {
    await act(async () => {
      await setLocale("es");
    });
    const { harness } = wizard(PYTHON_WITH_RAILPACK);
    await screen.findByRole("heading", { level: 1, name: "Nueva aplicación" });
    await harness.user.type(screen.getByLabelText("Qué desplegar"), "/var/www/src/shop");
    await harness.user.click(screen.getByRole("button", { name: "Continuar" }));
    await screen.findByRole("heading", { level: 2, name: "Dirección" });
    await harness.user.type(screen.getByLabelText("Dominio"), "crate.example.com");
    await harness.user.click(screen.getByRole("button", { name: "Continuar" }));

    const section = await screen.findByRole("group", { name: "Cómo se ejecuta" });
    // The sentence arrives in English beside its code, and the console says its own.
    expect(within(section).getByText(/Noust propone ejecutarla directamente en el servidor: es un proyecto Python/)).toBeInTheDocument();
    expect(within(section).queryByText(/Noust proposes/)).not.toBeInTheDocument();
    expect(within(section).getByRole("radio", { name: /Directamente en el servidor/ })).toBeChecked();
    expect(within(section).getByRole("radio", { name: /En un contenedor/ })).not.toBeChecked();
    expect(within(section).getByText(/no sobre la marcha: pasar de una forma a otra es desplegar de nuevo y mover el dominio/)).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});
