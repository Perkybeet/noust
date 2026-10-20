import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import type { Inspection } from "./wizard";

/** A repository with a Dockerfile that exposes 3000, as the inspection reads it. */
const INSPECTION: Inspection = {
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
  verdict: "Noust can deploy this as a container, built from its Dockerfile.",
  suggestion: null,
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
  name: "Deploy crate.example.com",
  description: "Deploying",
  status: "running",
  progress: 10,
  total_steps: 100,
  current_step: "Building the image",
  created_at: "2026-10-06T10:21:13",
  deployment_id: null,
  logs: [],
  metadata: { domain: "crate.example.com" },
};

function wizard(extra: Record<string, RouteHandler> = {}) {
  let deployed = false;
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/config/webserver": () => json(200, { webserver: "nginx" }),
    "GET /api/apps/types": () => json(200, APP_TYPES),
    "GET /api/domains/dns": (call) =>
      json(200, { domain: call.search.get("name"), expected_addresses: ["203.0.113.10"], resolved_addresses: ["203.0.113.10"], points_here: true }),
    "POST /api/apps/inspect": () => json(200, INSPECTION),
    "POST /api/apps": () => {
      deployed = true;
      return json(202, { job_id: JOB.id, status: "pending", message: "Deployment queued", job: JOB });
    },
    "GET /api/deployments": () =>
      json(200, {
        items: deployed ? [{ id: 9, domain: "crate.example.com", status: "running", triggered_by: "panel", has_log: true, job_id: JOB.id }] : [],
        total: deployed ? 1 : 0,
        next_before_id: null,
      }),
    [`GET /api/jobs/${JOB.id}`]: () => json(200, JOB),
    "GET /api/jobs/active": () => json(200, { jobs: [JOB], total: 1, active: 1 }),
    ...extra,
  });
  return { backend, harness: renderConsole("/apps/new") };
}

type User = ReturnType<typeof renderConsole>["user"];

/** The start named by a radio of "Where the code is". */
async function start(user: User, name: string) {
  await screen.findByRole("heading", { level: 1, name: "New application" });
  await user.click(await screen.findByRole("radio", { name }));
}

/** An optional field of the container section, by what its label begins with. */
function field(name: string, within_: HTMLElement = document.body): HTMLElement {
  return within(within_).getByLabelText(new RegExp(`^${name}(Optional)?$`));
}

describe("a container application from a repository with a Dockerfile", () => {
  it("says how the image is built and run on the Configuration step, and sends it as the container", { timeout: 60_000 }, async () => {
    const { backend, harness } = wizard();
    const { user } = harness;
    await screen.findByRole("heading", { level: 1, name: "New application" });
    await user.type(screen.getByLabelText("Repository or directory"), "/var/www/src/shop");
    await user.click(screen.getByRole("button", { name: "Inspect source" }));
    await screen.findByRole("heading", { level: 2, name: "Address" });
    await user.type(screen.getByLabelText("Domain"), "crate.example.com");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    // A container is never passed over: there is always a Dockerfile, a port and volumes to look at.
    expect(await screen.findByRole("heading", { level: 2, name: "Configuration" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "App type" })).toHaveTextContent("Container");
    expect(screen.getByRole("radio", { name: /A Dockerfile/ })).toBeChecked();
    expect(screen.getByText(/Empty uses 3000, the one the Dockerfile exposes/)).toBeInTheDocument();
    // The layout and the folders kept in shared/ are not a container's to choose.
    await user.click(screen.getByText("Advanced"));
    expect(screen.queryByRole("radio", { name: /Instant rollback/ })).not.toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));

    await user.type(field("Dockerfile"), "docker/Dockerfile.prod");
    await user.type(field("Build context"), "services/api");
    await user.type(field("Build stage"), "runtime");
    const args = within(screen.getByRole("group", { name: "Build arguments" }));
    await user.click(args.getByRole("button", { name: "Add argument" }));
    await user.type(args.getByLabelText("Name"), "NODE_ENV");
    await user.type(args.getByLabelText("Value"), "production");
    const secrets = within(screen.getByRole("group", { name: "Build secrets" }));
    await user.click(secrets.getByRole("button", { name: "Add secret" }));
    await user.type(secrets.getByLabelText("Id"), "npmrc");
    const secret = secrets.getByLabelText("Value", { selector: "input" });
    expect(secret).toHaveAttribute("type", "password");
    await user.type(secret, "//registry.npmjs.org/:_authToken=abc123");
    const volumes = within(screen.getByRole("group", { name: "Volumes" }));
    await user.click(volumes.getByRole("button", { name: "Add volume" }));
    await user.type(volumes.getByRole("textbox"), "/data");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    // Nothing to ask of the variables: the last step names what is about to happen, and no secret.
    expect(await screen.findByRole("heading", { level: 2, name: "Deploy" })).toBeInTheDocument();
    const main = screen.getByRole("main");
    expect(within(main).getByText("docker/Dockerfile.prod, in the context services/api")).toBeInTheDocument();
    expect(within(main).getByText("/data")).toBeInTheDocument();
    expect(within(main).getByText("1 arguments and 1 secrets")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("abc123");

    await user.click(screen.getByRole("button", { name: "Deploy crate.example.com" }));
    await waitFor(() => expect(backend.callsTo("POST /api/apps")).toHaveLength(1));
    const body = backend.callsTo("POST /api/apps")[0]?.body as Record<string, unknown>;
    expect(body).toMatchObject({
      domain: "crate.example.com",
      source: "/var/www/src/shop",
      app_type: "container",
      container: {
        method: "dockerfile",
        dockerfile: "docker/Dockerfile.prod",
        context: "services/api",
        target: "runtime",
        build_args: { NODE_ENV: "production" },
        build_secrets: { npmrc: "//registry.npmjs.org/:_authToken=abc123" },
        volumes: ["/data"],
        restart_policy: "unless-stopped",
      },
    });
    expect(body).not.toHaveProperty("layout");
    expect(body).not.toHaveProperty("persistent_paths");
    expect(body["container"]).not.toHaveProperty("container_port");
  });

  it("builds with Railpack when asked to, with no Dockerfile or stage to give", { timeout: 60_000 }, async () => {
    const { backend, harness } = wizard();
    const { user } = harness;
    await screen.findByRole("heading", { level: 1, name: "New application" });
    await user.type(screen.getByLabelText("Repository or directory"), "/var/www/src/shop");
    await user.click(screen.getByRole("button", { name: "Inspect source" }));
    await screen.findByRole("heading", { level: 2, name: "Address" });
    await user.type(screen.getByLabelText("Domain"), "crate.example.com");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByRole("heading", { level: 2, name: "Configuration" });
    await user.click(screen.getByRole("radio", { name: /Railpack/ }));
    expect(screen.getByText("Railpack has to be installed on this server")).toBeInTheDocument();
    expect(screen.queryByLabelText(/^Dockerfile/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.click(await screen.findByRole("button", { name: "Deploy crate.example.com" }));
    await waitFor(() => expect(backend.callsTo("POST /api/apps")).toHaveLength(1));
    const container = (backend.callsTo("POST /api/apps")[0]?.body as { container: Record<string, unknown> }).container;
    expect(container["method"]).toBe("railpack");
    expect(container).not.toHaveProperty("dockerfile");
    expect(container).not.toHaveProperty("target");
  });

  it("says what is wrong with an argument before the deploy, beside it", { timeout: 60_000 }, async () => {
    const { backend, harness } = wizard();
    const { user } = harness;
    await screen.findByRole("heading", { level: 1, name: "New application" });
    await user.type(screen.getByLabelText("Repository or directory"), "/var/www/src/shop");
    await user.click(screen.getByRole("button", { name: "Inspect source" }));
    await screen.findByRole("heading", { level: 2, name: "Address" });
    await user.type(screen.getByLabelText("Domain"), "crate.example.com");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByRole("heading", { level: 2, name: "Configuration" });
    const args = within(screen.getByRole("group", { name: "Build arguments" }));
    await user.click(args.getByRole("button", { name: "Add argument" }));
    await user.type(args.getByLabelText("Name"), "1BAD");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findAllByText("A name is letters, digits and _, not starting with a digit.")).not.toHaveLength(0);
    expect(screen.getByRole("heading", { level: 2, name: "Configuration" })).toBeInTheDocument();
    expect(backend.callsTo("POST /api/apps")).toHaveLength(0);
  });
});

describe("a container application from an image in a registry", () => {
  it("starts from the image, needs no inspection, and sends no source", { timeout: 60_000 }, async () => {
    const { backend, harness } = wizard();
    const { user } = harness;
    await start(user, "A Docker image");
    const image = screen.getByLabelText("Image");
    await user.type(image, "ghcr.io/owner/app:1.4");
    await expectNoAxeViolations(screen.getByRole("main"));
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Address" })).toBeInTheDocument();
    expect(backend.callsTo("POST /api/apps/inspect")).toHaveLength(0);
    await user.type(screen.getByLabelText("Domain"), "crate.example.com");
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByRole("heading", { level: 2, name: "Configuration" })).toBeInTheDocument();
    // What is pulled, and not a type to choose: nothing was inspected.
    expect(screen.getAllByText("ghcr.io/owner/app:1.4").length).toBeGreaterThan(0);
    expect(screen.queryByRole("combobox", { name: "App type" })).not.toBeInTheDocument();
    expect(screen.queryByRole("radio", { name: /A Dockerfile/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Build secrets" })).not.toBeInTheDocument();

    // A private registry's account: its password is typed masked and never shown again.
    const registry = within(screen.getByRole("group", { name: "Private registry" }));
    await user.type(registry.getByLabelText(/^Username/), "ci");
    const password = registry.getByLabelText(/^Password/, { selector: "input" });
    expect(password).toHaveAttribute("type", "password");
    await user.type(password, "tok_live_123");
    await user.type(field("Container port"), "8080");
    // The port on this machine is not proposed as 8080: that is the console's own on a default
    // install, and the server refuses it for an application.
    expect(screen.getByLabelText("Port")).not.toHaveValue("8080");
    await expectNoAxeViolations(screen.getByRole("main"));
    await user.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByRole("heading", { level: 2, name: "Deploy" })).toBeInTheDocument();
    const main = screen.getByRole("main");
    expect(within(main).getByText("ghcr.io/owner/app:1.4", { exact: false })).toBeInTheDocument();
    expect(within(main).getByText("ci")).toBeInTheDocument();
    expect(within(main).getByText("Its password is sent once and never shown.")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("tok_live_123");

    await user.click(screen.getByRole("button", { name: "Deploy crate.example.com" }));
    await waitFor(() => expect(backend.callsTo("POST /api/apps")).toHaveLength(1));
    const body = backend.callsTo("POST /api/apps")[0]?.body as Record<string, unknown>;
    expect(body).toMatchObject({
      domain: "crate.example.com",
      app_type: "container",
      container: { method: "image", image: "ghcr.io/owner/app:1.4", container_port: 8080, registry_username: "ci", registry_password: "tok_live_123" },
    });
    expect(body["port"]).not.toBe(8080);
    expect(body).not.toHaveProperty("source");
    expect(body).not.toHaveProperty("branch");
  });

  it("says what an image needs before going on", { timeout: 30_000 }, async () => {
    const { harness } = wizard();
    const { user } = harness;
    await start(user, "A Docker image");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findAllByText("Give the image, such as ghcr.io/owner/app:1.4.")).not.toHaveLength(0);
    await user.type(screen.getByLabelText("Image"), "https://ghcr.io/owner/app");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findAllByText(/An image is name\[:tag\]\[@sha256:digest\]/)).not.toHaveLength(0);
    expect(screen.getByRole("heading", { level: 2, name: "Source" })).toBeInTheDocument();
  });

  it("goes back to the image, with the server's words beside it, when the server refuses it", { timeout: 60_000 }, async () => {
    const { harness } = wizard({
      "POST /api/apps": () =>
        problem(400, "validationerror", "'Owner/App' is not an image reference", {
          hint: "Give the image as name[:tag][@sha256:digest].",
          fields: { image: "'Owner/App' is not an image reference" },
        }),
    });
    const { user } = harness;
    await start(user, "A Docker image");
    await user.type(screen.getByLabelText("Image"), "Owner/App");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.type(await screen.findByLabelText("Domain"), "crate.example.com");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    await user.click(await screen.findByRole("button", { name: "Continue" }));
    await user.click(await screen.findByRole("button", { name: "Deploy crate.example.com" }));
    expect(await screen.findByRole("heading", { level: 2, name: "Source" })).toBeInTheDocument();
    expect(await screen.findByText("'Owner/App' is not an image reference")).toBeInTheDocument();
    expect(screen.getByLabelText("Image")).toHaveAttribute("aria-invalid", "true");
  });

  it("is never offered a database, which a container cannot reach", { timeout: 30_000 }, async () => {
    const { harness } = wizard({ "GET /api/databases": () => json(200, { databases: [], total: 0 }) });
    const { user } = harness;
    await start(user, "A Docker image");
    await user.type(screen.getByLabelText("Image"), "nginx:1.27-alpine");
    await user.click(screen.getByRole("button", { name: "Continue" }));
    const steps = await screen.findByRole("list", { name: "Steps" });
    expect(within(steps).queryByText(/^Database/)).not.toBeInTheDocument();
  });
});
