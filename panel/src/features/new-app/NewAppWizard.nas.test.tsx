import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, signedInRoutes } from "../../test/fakes";
import { nasSession } from "../../test/nas";
import type { Inspection } from "./wizard";

/** A sentence of the server's, as `POST /api/apps/inspect` sends it: the code, its values and its English beside them. */
const said = (code: string, text: string, params: Record<string, string> = {}, hint: string | null = null) => ({ code, params, text, hint });

const HERE = said("features.containers_only", "Applications: not available here, which deploys containers and nothing else", { feature: "Applications" });

/** A repository with a Dockerfile, on a machine that only runs containers: the one way that there is. */
const DOCKERFILE: Inspection = {
  app_type: "container",
  detected_types: ["container"],
  package_manager: null,
  install_command: [],
  build_command: [],
  start_command: "",
  default_port: 3000,
  env_keys: [],
  branch: "main",
  commit: "ab12cd3",
  compatible: true,
  ways: {
    choice: false,
    proposed: "container",
    why: said("how_it_runs.why.container_dockerfile", "Noust proposes a container: the repository brings its Dockerfile.", { type: "Node.js" }),
    server: { form: "server", status: "hidden", app_type: null, via: null, reason: HERE },
    container: { form: "container", status: "yes", app_type: "container", via: "dockerfile", reason: null, port: 8080 },
  },
};

/** A repository with nothing that runs on this machine: no Dockerfile and no Compose file. */
const NOTHING: Inspection = {
  ...DOCKERFILE,
  app_type: "nodejs",
  detected_types: ["nodejs"],
  package_manager: "npm",
  install_command: ["npm", "ci"],
  start_command: "npm start",
  compatible: false,
  // What the inspector says of a machine's own toolchain: advice for a server, which a machine that only has Docker is not.
  verdict: "This is a Node.js project, but this server does not have node, npm.",
  suggestion: "Install what it needs with noust setup init, then deploy it.",
  ways: {
    choice: false,
    proposed: "container",
    why: said(
      "how_it_runs.why.nothing_runs_here",
      "Nothing in this repository runs on this machine: it deploys containers and nothing else, and the repository has no Dockerfile or Compose file to make one.",
      {},
      "Add a Dockerfile or a Compose file to the repository and deploy it again, or deploy an image from a registry.",
    ),
    server: { form: "server", status: "hidden", app_type: null, via: null, reason: HERE },
    container: {
      form: "container",
      status: "no",
      app_type: null,
      via: null,
      reason: said("how_it_runs.container.no_build_here", "It cannot run as a container here: the repository has no Dockerfile, and what builds a container without one is not available on this machine.", {}, "Add a Dockerfile to the repository, or deploy an image from a registry or a Compose file."),
    },
  },
};

const APP_TYPES = {
  types: [
    { type: "container", name: "Container", default_port: 3000 },
    { type: "docker-compose", name: "Docker Compose", default_port: 3000 },
    { type: "nodejs", name: "Node.js", default_port: 3000 },
  ],
};

const JOB = {
  id: "7c1d0e22",
  type: "deploy",
  name: "Deploy vault",
  description: "Deploying",
  status: "running",
  progress: 10,
  total_steps: 100,
  current_step: "Building",
  created_at: "2026-11-12T10:21:13",
  deployment_id: null,
  logs: [],
  metadata: { domain: "vault" },
};

function wizard(inspection: Inspection) {
  const backend = fakeBackend({
    ...signedInRoutes(nasSession()),
    "GET /api/apps/types": () => json(200, APP_TYPES),
    "POST /api/apps/inspect": () => json(200, inspection),
    "POST /api/apps": () => json(202, { job_id: JOB.id, status: "pending", message: "Deployment queued", job: JOB }),
    "GET /api/deployments": () => json(200, { items: [], total: 0, next_before_id: null }),
    [`GET /api/jobs/${JOB.id}`]: () => json(200, JOB),
    "GET /api/jobs/active": () => json(200, { jobs: [JOB], total: 1, active: 1 }),
  });
  return { backend, harness: renderConsole("/apps/new") };
}

type User = ReturnType<typeof renderConsole>["user"];

async function fromTheSource(user: User): Promise<void> {
  await screen.findByRole("heading", { level: 1, name: "New application" });
  await user.type(screen.getByLabelText("What to deploy"), "/var/www/src/vault");
  await user.click(screen.getByRole("button", { name: "Continue" }));
}

describe("a new application on a machine that only has Docker (items 187 and 188)", () => {
  it("opens on a port of the machine, with the address it makes already written, and asks for no domain", { timeout: 60_000 }, async () => {
    const { harness } = wizard(DOCKERFILE);
    await fromTheSource(harness.user);

    expect(await screen.findByRole("heading", { level: 2, name: "How it opens" })).toBeInTheDocument();
    // There is no domain, no www and no HTTPS of Noust's to ask for: ports 80 and 443 are the machine's.
    expect(screen.queryByLabelText("Domain")).not.toBeInTheDocument();
    expect(screen.queryByText(/DNS|certificate/i)).not.toBeInTheDocument();
    // The port is the first one from 8081, and the address is written beside it.
    expect(screen.getByLabelText("Port of this machine")).toHaveValue("8081");
    expect(screen.getByRole("group", { name: "It will open at" })).toHaveTextContent("http://localhost:8081");
    await harness.user.clear(screen.getByLabelText("Port of this machine"));
    await harness.user.type(screen.getByLabelText("Port of this machine"), "8090");
    expect(screen.getByRole("group", { name: "It will open at" })).toHaveTextContent("http://localhost:8090");
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("says in one line that there is one way to run it and draws no choice, and offers no web server", { timeout: 60_000 }, async () => {
    const { harness } = wizard(DOCKERFILE);
    await fromTheSource(harness.user);
    await screen.findByRole("heading", { level: 2, name: "How it opens" });
    await harness.user.type(screen.getByLabelText("Name"), "vault");
    await harness.user.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByRole("heading", { level: 2, name: "Configuration" })).toBeInTheDocument();
    const how = screen.getByRole("group", { name: "How it runs" });
    expect(within(how).getByText("There is no other way: there is nothing to choose.")).toBeInTheDocument();
    expect(within(how).queryByRole("radio")).not.toBeInTheDocument();
    // What the machine does not have is not asked: no web server to choose, no port of the machine here (the address step has it).
    expect(screen.queryByText("Web server")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Port")).not.toBeInTheDocument();
    // Docker runs it: no web server serves its files, which is what the line says of a static site.
    expect(screen.getByText("Docker runs it")).toBeInTheDocument();
    expect(screen.queryByText("No process: the web server serves the files")).not.toBeInTheDocument();
  });

  it("deploys with the name and the port, and asks neither for HTTPS nor for a www name", { timeout: 60_000 }, async () => {
    const { backend, harness } = wizard(DOCKERFILE);
    const { user } = harness;
    await fromTheSource(user);
    await screen.findByRole("heading", { level: 2, name: "How it opens" });
    await user.type(screen.getByLabelText("Name"), "vault");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByRole("heading", { level: 2, name: "Configuration" });
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByRole("heading", { level: 2, name: "Deploy" })).toBeInTheDocument();
    expect(within(screen.getByRole("main")).getByText("http://localhost:8081")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Deploy vault" }));
    await waitFor(() => expect(backend.callsTo("POST /api/apps")).toHaveLength(1));
    expect(backend.callsTo("POST /api/apps")[0]?.body).toMatchObject({ domain: "vault", port: 8081, ssl: false, include_www: false, app_type: "container" });
  });

  it("takes a name with no dot, which a domain would not be, and refuses one that is not a name", { timeout: 60_000 }, async () => {
    const { harness } = wizard(DOCKERFILE);
    await fromTheSource(harness.user);
    await screen.findByRole("heading", { level: 2, name: "How it opens" });

    await harness.user.type(screen.getByLabelText("Name"), "my app");
    await harness.user.click(screen.getByRole("button", { name: "Continue" }));

    // Said by the field and announced by the step's list of what to fix: both are the same sentence, so more than one is not a second problem.
    expect((await screen.findAllByText(/Use lowercase letters, digits, dots and dashes/)).length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { level: 2, name: "How it opens" })).toBeInTheDocument();
  });

  it("offers no way forward when nothing in the source can run here, and says what is missing and the one fix", { timeout: 60_000 }, async () => {
    const { harness } = wizard(NOTHING);
    await fromTheSource(harness.user);

    // The first thing seen is why, before an address is asked for.
    expect(await screen.findByRole("heading", { level: 2, name: "Configuration" })).toBeInTheDocument();
    const how = screen.getByRole("group", { name: "How it runs" });
    expect(within(how).getByText(/Nothing in this repository runs on this machine/)).toBeInTheDocument();
    expect(within(how).getByText(/Add a Dockerfile to the repository, or deploy an image from a registry or a Compose file/)).toBeInTheDocument();
    // The way it runs says what is missing: the toolchain a server would be told to install is not what is missing here.
    expect(screen.queryByText(/does not have node, npm/)).not.toBeInTheDocument();
    expect(screen.queryByText(/noust setup init/)).not.toBeInTheDocument();
    // Back is all there is: a Continue that deploys what cannot run is not drawn, and neither is a disabled one.
    expect(screen.getByRole("button", { name: "Back" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Continue" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Deploy/ })).not.toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("shows no plan of the toolchain, no choice of type and nothing to set when nothing can run natively, only what was read, what is missing and the fix", { timeout: 60_000 }, async () => {
    const { harness } = wizard(NOTHING);
    await fromTheSource(harness.user);
    await screen.findByRole("heading", { level: 2, name: "Configuration" });

    // What was read stays: it is why a Dockerfile is missing.
    expect(screen.getByText(/Looks like a Node\.js app/)).toBeInTheDocument();
    // The plan of a service of the system (install, build, start) is not what this machine would do, and the type is not a choice here.
    for (const word of ["Install", "Build", "Start"]) expect(screen.queryByText(word)).not.toBeInTheDocument();
    expect(screen.queryByText("npm ci")).not.toBeInTheDocument();
    expect(screen.queryByText("npm start")).not.toBeInTheDocument();
    expect(screen.queryByText("App type")).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(screen.queryByText("Advanced")).not.toBeInTheDocument();
    expect(screen.queryByText(/No \.env\.example|variables in \.env\.example/)).not.toBeInTheDocument();
    // The summary beside it does not say the source runs on the server: it runs nowhere here.
    expect(screen.queryByText("Directly on the server")).not.toBeInTheDocument();
  });

  it("keeps the plan of a repository that does run here: its Dockerfile is what Noust builds", { timeout: 60_000 }, async () => {
    const { harness } = wizard(DOCKERFILE);
    await fromTheSource(harness.user);
    await screen.findByRole("heading", { level: 2, name: "How it opens" });
    await harness.user.type(screen.getByLabelText("Name"), "vault");
    await harness.user.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByRole("heading", { level: 2, name: "Configuration" });
    expect(screen.getByText("Advanced")).toBeInTheDocument();
  });
});
