import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../../test/axe";
import { json, problem } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import type { ContainerSettings } from "../../container/model";
import { DOMAIN, RELEASE_APP, first, last, renderSettings, saveBar } from "./testKit";

const APP = { ...RELEASE_APP, app_type: "container", layout: "releases" };

const DOCKERFILE: ContainerSettings = {
  domain: DOMAIN,
  method: "dockerfile",
  image: null,
  dockerfile: "docker/Dockerfile.prod",
  context: "services/api",
  target: "runtime",
  build_args: { NODE_ENV: "production" },
  build_secrets: ["npmrc"],
  container_port: 3000,
  volumes: ["/data"],
  restart_policy: "unless-stopped",
  registry_server: null,
  registry_username: null,
  registry_password_set: false,
  digest: null,
  release: "20261006-101500-ab12cd3",
};

const IMAGE: ContainerSettings = {
  ...DOCKERFILE,
  method: "image",
  image: "ghcr.io/owner/app:1.4",
  dockerfile: null,
  context: ".",
  target: null,
  build_args: {},
  build_secrets: [],
  container_port: 8080,
  volumes: [],
  registry_server: "ghcr.io",
  registry_username: "ci",
  registry_password_set: true,
  digest: "sha256:" + "ab".repeat(32),
};

function container(settings: ContainerSettings, extra: Record<string, RouteHandler> = {}) {
  return renderSettings("/container", "Container", APP, {
    [`GET /api/apps/${DOMAIN}/container`]: () => json(200, settings),
    ...extra,
  });
}

describe("the Container subsection", () => {
  it("is listed for a container application, and only for one", async () => {
    await container(DOCKERFILE);
    const nav = first(screen.getAllByRole("navigation", { name: "Application settings" }));
    expect(within(nav).getByRole("link", { name: "Container" })).toHaveAttribute("href", `/apps/${DOMAIN}/settings/container`);
    // A container is built by its own configuration: no build commands.
    expect(within(nav).queryByRole("link", { name: "Build commands" })).not.toBeInTheDocument();
  });

  it("shows how the image is made and how it runs, and never a secret", { timeout: 20_000 }, async () => {
    const { container: view } = await container(DOCKERFILE);
    const main = screen.getByRole("main");
    // Optional fields say so in their label, so they are found by what their name begins with.
    expect(await within(main).findByLabelText(/^Dockerfile/)).toHaveValue("docker/Dockerfile.prod");
    expect(within(main).getByText("Image made from").closest("div")).toHaveTextContent("A Dockerfile");
    expect(within(main).getByText("20261006-101500-ab12cd3")).toBeInTheDocument();
    expect(within(main).getByLabelText(/^Build context/)).toHaveValue("services/api");
    expect(within(main).getByLabelText(/^Build stage/)).toHaveValue("runtime");
    expect(within(main).getByLabelText(/^Container port/)).toHaveValue("3000");
    expect(within(main).getByDisplayValue("NODE_ENV")).toBeInTheDocument();
    expect(within(main).getByDisplayValue("/data")).toBeInTheDocument();
    // A build secret the server keeps is its id and the fact that it is kept: no field holds a value.
    expect(within(main).getByText("npmrc")).toBeInTheDocument();
    expect(within(main).getByText("Kept on the server")).toBeInTheDocument();
    expect(within(main).queryByLabelText(/Registry|Password/i)).not.toBeInTheDocument();
    expect(screen.getByText(/A change is stored now and takes effect with the next update/)).toBeInTheDocument();
    expect(within(saveBar()).getByText("No unsaved changes")).toBeInTheDocument();
    await expectNoAxeViolations(view);
  });

  it("saves only what changed, in one request, and says when it takes effect", { timeout: 30_000 }, async () => {
    let stored = DOCKERFILE;
    const { user, backend } = await container(DOCKERFILE, {
      [`GET /api/apps/${DOMAIN}/container`]: () => json(200, stored),
      [`PUT /api/apps/${DOMAIN}/container`]: () => {
        stored = { ...stored, container_port: 9000, volumes: ["/data", "/uploads"] };
        return json(200, stored);
      },
    });
    const port = await screen.findByLabelText(/^Container port/);
    await user.clear(port);
    await user.type(port, "9000");
    await user.click(screen.getByRole("button", { name: "Add volume" }));
    const paths = screen.getAllByLabelText(/^Path in the container/);
    await user.type(last(paths), "/uploads");
    expect(within(saveBar()).getByText("2 unsaved changes")).toBeInTheDocument();

    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/container`)).toHaveLength(1));
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/container`)[0]?.body).toEqual({ clear_registry: false, container_port: 9000, volumes: ["/data", "/uploads"] });
    expect(await screen.findByText("Saved. It takes effect with the next update.")).toBeInTheDocument();
    expect(within(saveBar()).getByText("No unsaved changes")).toBeInTheDocument();
  });

  it("adds a build secret with its value, sends it once, and then shows only its id", { timeout: 30_000 }, async () => {
    let stored = DOCKERFILE;
    const { user, backend } = await container(DOCKERFILE, {
      [`GET /api/apps/${DOMAIN}/container`]: () => json(200, stored),
      [`PUT /api/apps/${DOMAIN}/container`]: () => {
        stored = { ...stored, build_secrets: ["npmrc", "pip_token"] };
        return json(200, stored);
      },
    });
    await user.click(await screen.findByRole("button", { name: "Add secret" }));
    const secrets = within(screen.getByRole("group", { name: "Build secrets" }));
    await user.type(secrets.getByLabelText("Id"), "pip_token");
    const value = secrets.getByLabelText("Value", { selector: "input" });
    expect(value).toHaveAttribute("type", "password");
    await user.type(value, "s3cret-value");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/container`)).toHaveLength(1));
    expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/container`)[0]?.body).toEqual({ clear_registry: false, build_secrets: { pip_token: "s3cret-value" } });
    expect(await screen.findByText("pip_token")).toBeInTheDocument();
    // What was typed is gone from the page: nothing holds it any more.
    expect(screen.queryByDisplayValue("s3cret-value")).not.toBeInTheDocument();
    expect(document.body.textContent).not.toContain("s3cret-value");
  });

  it("removes a stored build secret by its id, which can be undone before saving", { timeout: 30_000 }, async () => {
    const { user, backend } = await container(DOCKERFILE, {
      [`PUT /api/apps/${DOMAIN}/container`]: () => json(200, { ...DOCKERFILE, build_secrets: [] }),
    });
    await user.click(await screen.findByRole("button", { name: "Remove npmrc" }));
    expect(screen.getByText("Removed when you save")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Keep npmrc" }));
    expect(within(saveBar()).getByText("No unsaved changes")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Remove npmrc" }));
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/container`)[0]?.body).toEqual({ clear_registry: false, build_secrets: { npmrc: null } }));
  });

  it("puts the server's refusal beside what it is about, in its words", { timeout: 30_000 }, async () => {
    const { user } = await container(DOCKERFILE, {
      [`PUT /api/apps/${DOMAIN}/container`]: () =>
        problem(400, "validationerror", "API_TOKEN looks like a secret, and a build argument is written into the image", {
          hint: "Give it as a build secret instead.",
          fields: { build_args: "API_TOKEN looks like a secret, and a build argument is written into the image" },
        }),
    });
    await user.click(await screen.findByRole("button", { name: "Add argument" }));
    const names = within(screen.getByRole("group", { name: "Build arguments" })).getAllByLabelText("Name");
    await user.type(last(names), "API_TOKEN");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));
    expect(await screen.findByText("API_TOKEN looks like a secret, and a build argument is written into the image")).toBeInTheDocument();
    // Unsaved still: the change is kept so it can be fixed.
    expect(within(saveBar()).getByText("1 unsaved change")).toBeInTheDocument();
  });

  it("says in the words of a Dockerfile and of Railpack what each needs, and that Railpack must be installed", { timeout: 30_000 }, async () => {
    const { user, backend } = await container(DOCKERFILE, {
      [`PUT /api/apps/${DOMAIN}/container`]: () => json(200, { ...DOCKERFILE, method: "railpack" }),
    });
    await user.click(await screen.findByRole("radio", { name: /Railpack/ }));
    expect(screen.getByText("Railpack has to be installed on this server")).toBeInTheDocument();
    expect(screen.getByText("noust setup railpack")).toBeInTheDocument();
    // Railpack has no Dockerfile and no stage, and its arguments are its variables.
    expect(screen.queryByLabelText(/^Dockerfile/)).not.toBeInTheDocument();
    expect(screen.getByText("Railpack variables")).toBeInTheDocument();
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/container`)[0]?.body).toEqual({ clear_registry: false, method: "railpack" }));
  });

  it("is an image's own form for an image: its reference, its digest, its registry account without its password", { timeout: 30_000 }, async () => {
    const { user, backend, container: view } = await container(IMAGE, {
      [`PUT /api/apps/${DOMAIN}/container`]: () => json(200, IMAGE),
    });
    const main = screen.getByRole("main");
    expect(await within(main).findByLabelText("Image")).toHaveValue("ghcr.io/owner/app:1.4");
    expect(within(main).getByText("Image made from").closest("div")).toHaveTextContent("An image from a registry");
    expect(within(main).getByText(`sha256:${"ab".repeat(32)}`)).toBeInTheDocument();
    // An image is built by nobody: no builder to choose, no Dockerfile, no build secrets.
    expect(within(main).queryByRole("radio")).not.toBeInTheDocument();
    expect(within(main).queryByLabelText(/^Dockerfile/)).not.toBeInTheDocument();
    expect(within(main).getByLabelText(/^Username/)).toHaveValue("ci");
    // The password is kept and never comes back: the field is empty and says so.
    const password = within(main).getByLabelText(/^Password/, { selector: "input" });
    expect(password).toHaveValue("");
    expect(password).toHaveAttribute("placeholder", "A value is kept. Leave this empty to keep it.");
    expect(within(main).getByText(/A password is kept\. Type a new one to replace it/)).toBeInTheDocument();
    await expectNoAxeViolations(view);

    // A new password replaces it, once.
    await user.type(password, "new-token");
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/container`)[0]?.body).toEqual({ clear_registry: false, registry_password: "new-token" }));
  });

  it("takes the registry account out on request", { timeout: 30_000 }, async () => {
    const { user, backend } = await container(IMAGE, {
      [`PUT /api/apps/${DOMAIN}/container`]: () => json(200, { ...IMAGE, registry_username: null, registry_server: null, registry_password_set: false }),
    });
    await user.click(await screen.findByRole("checkbox", { name: "Remove the registry account" }));
    expect(screen.getByText("The account and its password are removed when you save.")).toBeInTheDocument();
    await user.click(within(saveBar()).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(backend.callsTo(`PUT /api/apps/${DOMAIN}/container`)[0]?.body).toEqual({ clear_registry: true }));
  });

  it("says plainly that an application that is not a container has none of this", { timeout: 20_000 }, async () => {
    await renderSettings("/container", "Container", RELEASE_APP);
    expect(screen.getByText("This is not a container application.")).toBeInTheDocument();
  });
});
