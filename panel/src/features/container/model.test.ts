import { describe, expect, it } from "vitest";

import { containerBody, containerProblems, draftOf, emptyDraft, imageProblem, plannedChange, portOf, rowId, volumeProblem } from "./model";
import type { ContainerDraft, ContainerSettings } from "./model";

/** The first of a list a test has shown is not empty. */
function first<T>(items: readonly T[]): T {
  const [item] = items;
  if (item === undefined) throw new Error("nothing there");
  return item;
}

const DOCKERFILE: ContainerSettings = {
  domain: "shop.example.com",
  method: "dockerfile",
  image: null,
  dockerfile: "docker/Dockerfile.prod",
  context: "services/api",
  target: "runtime",
  build_args: { NODE_ENV: "production", REGION: "eu" },
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
};

function edited(draft: ContainerDraft, patch: Partial<ContainerDraft>): ContainerDraft {
  return { ...draft, ...patch };
}

describe("a container application's form", () => {
  it("is what the server says the application is, with no value of a secret to show", () => {
    const draft = draftOf(DOCKERFILE);
    expect(draft).toMatchObject({
      method: "dockerfile",
      dockerfile: "docker/Dockerfile.prod",
      // The root is the empty field, so the server's default stays the server's.
      context: "services/api",
      target: "runtime",
      containerPort: "3000",
      restartPolicy: "unless-stopped",
    });
    expect(draft.buildArgs.map(({ name, value }) => [name, value])).toEqual([
      ["NODE_ENV", "production"],
      ["REGION", "eu"],
    ]);
    // The id of a build secret is known; its value never is.
    expect(draft.buildSecrets).toMatchObject([{ secretId: "npmrc", value: "", stored: true, remove: false }]);
    expect(draft.volumes.map((row) => row.value)).toEqual(["/data"]);
    expect(draftOf({ ...DOCKERFILE, context: "." }).context).toBe("");
    // A method this console does not know is read as the default one, not trusted.
    expect(draftOf({ ...DOCKERFILE, method: "podman" }).method).toBe("dockerfile");
    expect(draftOf(IMAGE)).toMatchObject({ method: "image", image: "ghcr.io/owner/app:1.4", registryUsername: "ci", registryPasswordSet: true, registryPassword: "" });
  });

  it("changes nothing when nothing was touched", () => {
    expect(plannedChange(draftOf(DOCKERFILE), DOCKERFILE)).toEqual({ body: { clear_registry: false }, fields: [] });
    expect(plannedChange(draftOf(IMAGE), IMAGE)).toEqual({ body: { clear_registry: false }, fields: [] });
  });

  it("sends only what differs, and says a setting emptied is a setting cleared", () => {
    const draft = edited(draftOf(DOCKERFILE), { dockerfile: "", target: "", context: "", containerPort: "9000", restartPolicy: "always" });
    const { body, fields } = plannedChange(draft, DOCKERFILE);
    expect(body).toEqual({ clear_registry: false, context: ".", container_port: 9000, restart_policy: "always", clear: ["dockerfile", "target"] });
    expect(fields).toEqual(["dockerfile", "context", "target", "container_port", "restart_policy"]);
  });

  it("replaces the build arguments and the volumes whole when they differ", () => {
    const draft = draftOf(DOCKERFILE);
    const changed = edited(draft, {
      buildArgs: [...draft.buildArgs, { id: rowId("arg"), name: " LOG_LEVEL ", value: "debug" }],
      volumes: [...draft.volumes, { id: rowId("volume"), value: "/uploads/" }],
    });
    const { body } = plannedChange(changed, DOCKERFILE);
    expect(body.build_args).toEqual({ NODE_ENV: "production", REGION: "eu", LOG_LEVEL: "debug" });
    expect(body.volumes).toEqual(["/data", "/uploads"]);
    // An argument with no name is no argument.
    const blank = edited(draft, { buildArgs: [...draft.buildArgs, { id: rowId("arg"), name: " ", value: "" }] });
    expect(plannedChange(blank, DOCKERFILE).fields).toEqual([]);
  });

  it("adds a build secret with its value, and removes a stored one by its id: no value is ever compared", () => {
    const draft = draftOf(DOCKERFILE);
    const secrets = [
      { ...first(draft.buildSecrets), remove: true },
      { id: rowId("secret"), secretId: "pip_token", value: "s3cret", stored: false, remove: false },
    ];
    const { body, fields } = plannedChange(edited(draft, { buildSecrets: secrets }), DOCKERFILE);
    expect(body.build_secrets).toEqual({ npmrc: null, pip_token: "s3cret" });
    expect(fields).toEqual(["build_secrets"]);
    // A stored secret that is left alone is not mentioned at all.
    expect(plannedChange(draft, DOCKERFILE).body.build_secrets).toBeUndefined();
  });

  it("moves between a Dockerfile and Railpack, never to or from an image", () => {
    const railpack = edited(draftOf(DOCKERFILE), { method: "railpack" });
    expect(plannedChange(railpack, DOCKERFILE).body.method).toBe("railpack");
    // The image's own form has no Dockerfile to compare: it asks about the image and the account.
    const retag = edited(draftOf(IMAGE), { image: "ghcr.io/owner/app:1.5" });
    const planned = plannedChange(retag, IMAGE);
    expect(planned.body.image).toBe("ghcr.io/owner/app:1.5");
    expect(planned.body.method).toBeUndefined();
    expect(planned.body.dockerfile).toBeUndefined();
  });

  it("sends a new registry password once, takes the account out on request, and clears an emptied server", () => {
    const draft = draftOf(IMAGE);
    expect(plannedChange(edited(draft, { registryPassword: "tok_123" }), IMAGE).body.registry_password).toBe("tok_123");
    const removed = plannedChange(edited(draft, { clearRegistry: true }), IMAGE);
    expect(removed.body.clear_registry).toBe(true);
    expect(removed.fields).toEqual(["registry"]);
    const emptied = plannedChange(edited(draft, { registryServer: "" }), IMAGE);
    expect(emptied.body.clear).toEqual(["registry_server"]);
    // A form for an image never mentions a Dockerfile's settings.
    expect(Object.keys(plannedChange(edited(draft, { registryUsername: "bot" }), IMAGE).body)).toEqual(["clear_registry", "registry_username"]);
  });

  it("goes back to the method's port when the port is emptied, and only when it was not already that", () => {
    const emptied = plannedChange(edited(draftOf(DOCKERFILE), { containerPort: "" }), DOCKERFILE);
    expect(emptied.body.clear).toEqual(["container_port"]);
    const already = plannedChange(edited(draftOf({ ...DOCKERFILE, container_port: 8080 }), { containerPort: "" }), { ...DOCKERFILE, container_port: 8080 });
    expect(already.fields).toEqual([]);
  });
});

describe("a new container application's request", () => {
  it("carries only what was set, so the server's own defaults stay its own", () => {
    expect(containerBody(emptyDraft("dockerfile"))).toEqual({ clear_registry: false, method: "dockerfile", restart_policy: "unless-stopped" });
  });

  it("names a Dockerfile, its context and stage, the arguments, the secrets, the port and the volumes", () => {
    const draft = edited(emptyDraft("dockerfile"), {
      dockerfile: " docker/Dockerfile.prod ",
      context: "services/api",
      target: "runtime",
      buildArgs: [{ id: rowId("arg"), name: "NODE_ENV", value: "production" }, { id: rowId("arg"), name: "", value: "" }],
      buildSecrets: [{ id: rowId("secret"), secretId: "npmrc", value: "//registry:_authToken=abc", stored: false, remove: false }],
      containerPort: "3000",
      volumes: [{ id: rowId("volume"), value: "/data/" }],
      restartPolicy: "always",
    });
    expect(containerBody(draft)).toEqual({
      clear_registry: false,
      method: "dockerfile",
      dockerfile: "docker/Dockerfile.prod",
      context: "services/api",
      target: "runtime",
      build_args: { NODE_ENV: "production" },
      build_secrets: { npmrc: "//registry:_authToken=abc" },
      container_port: 3000,
      volumes: ["/data"],
      restart_policy: "always",
    });
  });

  it("asks Railpack for no Dockerfile and no stage, but still for its variables and secrets", () => {
    const draft = edited(emptyDraft("railpack", "3000"), {
      dockerfile: "ignored/Dockerfile",
      target: "ignored",
      buildArgs: [{ id: rowId("arg"), name: "RAILPACK_NODE_VERSION", value: "22" }],
    });
    expect(containerBody(draft)).toEqual({
      clear_registry: false,
      method: "railpack",
      build_args: { RAILPACK_NODE_VERSION: "22" },
      container_port: 3000,
      restart_policy: "unless-stopped",
    });
  });

  it("names an image and its account, and sends no build settings with it", () => {
    const draft = edited(emptyDraft("image", "8080"), {
      image: " ghcr.io/owner/app:1.4 ",
      buildArgs: [{ id: rowId("arg"), name: "IGNORED", value: "1" }],
      registryServer: "ghcr.io",
      registryUsername: "ci",
      registryPassword: "tok_123",
    });
    const body = containerBody(draft);
    expect(body).toEqual({
      clear_registry: false,
      method: "image",
      image: "ghcr.io/owner/app:1.4",
      container_port: 8080,
      restart_policy: "unless-stopped",
      registry_server: "ghcr.io",
      registry_username: "ci",
      registry_password: "tok_123",
    });
    expect(body).not.toHaveProperty("build_args");
  });
});

describe("what is wrong with the form before the server is asked", () => {
  it("wants an image for an image, in a shape Docker could read", () => {
    expect(containerProblems(emptyDraft("image"))["image"]).toMatch(/Give the image/);
    for (const bad of ["ghcr.io/o/a b", "-v", "https://ghcr.io/o/a"]) {
      expect(imageProblem(bad), bad).not.toBeNull();
    }
    for (const good of ["nginx", "nginx:1.27-alpine", "ghcr.io/owner/app:1.4", "ghcr.io/o/a@sha256:" + "a".repeat(64)]) {
      expect(imageProblem(good), good).toBeNull();
    }
    // A Dockerfile build has no image to complain about.
    expect(containerProblems(emptyDraft("dockerfile"))).toEqual({});
  });

  it("wants a port from 1 to 65535 or none", () => {
    for (const bad of ["0", "65536", "80a", "-1", "3.5"]) {
      expect(containerProblems(edited(emptyDraft("dockerfile"), { containerPort: bad }))["container_port"], bad).toBeDefined();
    }
    for (const good of ["", "1", "8080", "65535"]) {
      expect(containerProblems(edited(emptyDraft("dockerfile"), { containerPort: good }))["container_port"], good).toBeUndefined();
    }
    expect(portOf({ containerPort: " 80 " })).toBe(80);
    expect(portOf({ containerPort: "" })).toBeNull();
    expect(portOf({ containerPort: "x" })).toBeNaN();
  });

  it("wants absolute volumes, each once", () => {
    expect(volumeProblem("data")).not.toBeNull();
    expect(volumeProblem("/")).not.toBeNull();
    expect(volumeProblem("/a/../b")).not.toBeNull();
    expect(volumeProblem("/data")).toBeNull();
    const first = { id: rowId("volume"), value: "/data" };
    const again = { id: rowId("volume"), value: "/data/" };
    const errors = containerProblems(edited(emptyDraft("dockerfile"), { volumes: [first, again, { id: rowId("volume"), value: "" }] }));
    expect(Object.keys(errors)).toEqual([`volume:${again.id}`]);
  });

  it("wants build arguments named like variables, each once", () => {
    const ok = { id: rowId("arg"), name: "NODE_ENV", value: "production" };
    const bad = { id: rowId("arg"), name: "1BAD", value: "x" };
    const twice = { id: rowId("arg"), name: "NODE_ENV", value: "y" };
    const errors = containerProblems(edited(emptyDraft("dockerfile"), { buildArgs: [ok, bad, twice] }));
    expect(Object.keys(errors)).toEqual([`build_arg_name:${bad.id}`, `build_arg_name:${twice.id}`]);
  });

  it("wants a new build secret to have an id and a value, and a stored one nothing", () => {
    const stored = { id: rowId("secret"), secretId: "npmrc", value: "", stored: true, remove: false };
    const noValue = { id: rowId("secret"), secretId: "pip", value: "", stored: false, remove: false };
    const noId = { id: rowId("secret"), secretId: "bad id", value: "x", stored: false, remove: false };
    const clash = { id: rowId("secret"), secretId: "npmrc", value: "x", stored: false, remove: false };
    const errors = containerProblems(edited(emptyDraft("dockerfile"), { buildSecrets: [stored, noValue, noId, clash] }), { existing: true });
    expect(Object.keys(errors).sort()).toEqual([`build_secret:${noValue.id}`, `build_secret_id:${clash.id}`, `build_secret_id:${noId.id}`].sort());
  });

  it("wants a registry account whole: a user with a password, a server only with a user", () => {
    const base = edited(emptyDraft("image"), { image: "ghcr.io/o/a:1" });
    expect(containerProblems(edited(base, { registryUsername: "ci" }))["registry_password"]).toBeDefined();
    expect(containerProblems(edited(base, { registryServer: "ghcr.io" }))["registry_username"]).toBeDefined();
    expect(containerProblems(edited(base, { registryPassword: "tok" }))["registry_username"]).toBeDefined();
    expect(containerProblems(edited(base, { registryUsername: "ci", registryPassword: "tok" }))).toEqual({});
    // The application already holds a password: the account may be changed without typing it again.
    const kept = edited(draftOf(IMAGE), { registryUsername: "other" });
    expect(containerProblems(kept, { existing: true })).toEqual({});
    expect(containerProblems(edited(base, { registryUsername: "x", registryPasswordSet: true }))["registry_password"]).toBeDefined();
    // Taking the account out needs no password.
    expect(containerProblems(edited(base, { registryUsername: "ci", clearRegistry: true }))).toEqual({});
  });
});
