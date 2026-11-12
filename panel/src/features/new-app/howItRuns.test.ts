import { describe, expect, it } from "vitest";

import { askedHowItRuns, chosenForm, formOfType, keepsType, withForm } from "./howItRuns";
import type { Ways } from "./howItRuns";
import { createAppBody, initialReview, stepAsks } from "./wizard";
import type { Inspection } from "./wizard";

const sentence = (code: string) => ({ code, params: {}, text: code, hint: null });

/** The four ways a source can be, as `POST /api/apps/inspect` says them, for each case of the approved mock-up. */
const NODE_WITH_DOCKERFILE: Ways = {
  choice: true,
  proposed: "server",
  why: sentence("how_it_runs.why.server_dockerfile"),
  server: { form: "server", status: "yes", app_type: "nodejs", via: null, reason: null },
  container: { form: "container", status: "yes", app_type: "container", via: "dockerfile", reason: null },
};

const GO_WITH_DOCKERFILE: Ways = {
  choice: true,
  proposed: "container",
  why: sentence("how_it_runs.why.container_dockerfile"),
  server: { form: "server", status: "yes", app_type: "go", via: null, reason: null },
  container: { form: "container", status: "yes", app_type: "container", via: "dockerfile", reason: null },
};

const PYTHON: Ways = {
  choice: false,
  proposed: "server",
  why: sentence("how_it_runs.why.server_only"),
  server: { form: "server", status: "yes", app_type: "python", via: null, reason: null },
  container: { form: "container", status: "no", app_type: null, via: null, reason: sentence("how_it_runs.container.no_build") },
};

const PYTHON_WITH_RAILPACK: Ways = {
  ...PYTHON,
  choice: true,
  why: sentence("how_it_runs.why.server_railpack"),
  container: { form: "container", status: "yes", app_type: "container", via: "railpack", reason: null },
};

const COMPOSE: Ways = {
  choice: false,
  proposed: "container",
  why: sentence("how_it_runs.why.compose"),
  server: { form: "server", status: "hidden", app_type: null, via: null, reason: sentence("how_it_runs.server.nothing_recognised") },
  container: { form: "container", status: "yes", app_type: "docker-compose", via: "compose", reason: null },
};

/** A machine with no web server: what is proposed moves to the container, which is what can run. */
const NODE_WITHOUT_A_WEB_SERVER: Ways = {
  choice: false,
  proposed: "container",
  why: sentence("how_it_runs.why.container_machine"),
  server: { form: "server", status: "no", app_type: null, via: null, reason: sentence("features.needs_web_server") },
  container: { form: "container", status: "yes", app_type: "container", via: "dockerfile", reason: null },
};

const BASE: Inspection = {
  app_type: "nodejs",
  detected_types: ["nodejs", "container"],
  package_manager: "npm",
  install_command: ["npm", "ci"],
  build_command: [],
  start_command: "npm start",
  default_port: 3000,
  env_keys: [],
  branch: "main",
  commit: "a1b2c3d",
  compatible: true,
};

const inspection = (patch: Partial<Inspection>): Inspection => ({ ...BASE, ...patch });
const defaults = { webserver: "nginx" as const, taken: new Map<number, string>() };

describe("which form a type runs in", () => {
  it("is a container for a container and a Compose stack, and a service of the system for every other type", () => {
    expect(formOfType("container")).toBe("container");
    expect(formOfType("docker-compose")).toBe("container");
    for (const type of ["nodejs", "nextjs", "go", "python", "static", "monorepo"]) expect(formOfType(type)).toBe("server");
  });
});

describe("what the wizard starts with", () => {
  it("is what Noust has always proposed: Node.js with a Dockerfile starts as Node.js, Go with one as a container", () => {
    const node = inspection({ ways: NODE_WITH_DOCKERFILE });
    expect(initialReview(node, defaults).appType).toBe("nodejs");
    const go = inspection({ app_type: "container", detected_types: ["container"], ways: GO_WITH_DOCKERFILE });
    expect(initialReview(go, defaults).appType).toBe("container");
  });

  it("is what it always was against a server that does not say how it runs (a node of 4.2 under a 4.3 central)", () => {
    const old = inspection({});
    expect(old.ways).toBeUndefined();
    expect(initialReview(old, defaults).appType).toBe("nodejs");
    expect(askedHowItRuns(old)).toBe(false);
    expect(askedHowItRuns(inspection({ ways: null }))).toBe(false);
  });

  it("moves to the other form only when this machine cannot run the one that was proposed", () => {
    const stuck = inspection({ ways: NODE_WITHOUT_A_WEB_SERVER });
    expect(initialReview(stuck, defaults).appType).toBe("container");
  });

  it("builds a container with Railpack when that is how the server says it is built", () => {
    const railpack: Ways = { ...NODE_WITHOUT_A_WEB_SERVER, container: { ...NODE_WITHOUT_A_WEB_SERVER.container, via: "railpack" } };
    const review = initialReview(inspection({ ways: railpack }), defaults);
    expect(review.appType).toBe("container");
    expect(review.container.method).toBe("railpack");
  });

  it("is a Compose stack when that is what the source is", () => {
    const stack = inspection({ app_type: "docker-compose", detected_types: ["docker-compose"], ways: COMPOSE });
    expect(initialReview(stack, defaults).appType).toBe("docker-compose");
    expect(chosenForm(stack.ways ?? null, "docker-compose")).toBe("container");
  });
});

describe("choosing how it runs", () => {
  it("is asked about when both can be, and only then", () => {
    expect(askedHowItRuns(inspection({ ways: NODE_WITH_DOCKERFILE }))).toBe(true);
    expect(askedHowItRuns(inspection({ ways: PYTHON_WITH_RAILPACK }))).toBe(true);
    expect(askedHowItRuns(inspection({ ways: PYTHON }))).toBe(false);
    expect(askedHowItRuns(inspection({ ways: COMPOSE }))).toBe(false);
  });

  it("makes the Configuration step ask when there is something to choose, even for one type with nothing else to fix", () => {
    const python = inspection({ app_type: "python", detected_types: ["python"], package_manager: null, ways: PYTHON_WITH_RAILPACK });
    const asks = (value: Inspection) => stepAsks("configure", "code", { inspection: value, review: initialReview(value, defaults), context: { domains: new Set(), ports: new Map(), cores: null } });
    expect(asks(python)).toBe(true);
    // As it was: one type, nothing to choose, nothing to fix.
    expect(asks({ ...python, ways: PYTHON })).toBe(false);
    expect(asks({ ...python, ways: null })).toBe(false);
  });

  it("takes the type of the card that was chosen, and a container the way it is built", () => {
    const start = initialReview(inspection({ ways: NODE_WITH_DOCKERFILE }), defaults);
    const container = withForm(start, NODE_WITH_DOCKERFILE, "container");
    expect(container.appType).toBe("container");
    expect(container.container.method).toBe("dockerfile");
    expect(chosenForm(NODE_WITH_DOCKERFILE, container.appType)).toBe("container");
    const back = withForm(container, NODE_WITH_DOCKERFILE, "server");
    expect(back.appType).toBe("nodejs");
    expect(chosenForm(NODE_WITH_DOCKERFILE, back.appType)).toBe("server");
  });

  it("builds without a Dockerfile with Railpack", () => {
    const start = initialReview(inspection({ app_type: "python", detected_types: ["python"], ways: PYTHON_WITH_RAILPACK }), defaults);
    expect(withForm(start, PYTHON_WITH_RAILPACK, "container").container.method).toBe("railpack");
  });

  it("runs a Go repository directly as the type Go, which the server says is possible", () => {
    const start = initialReview(inspection({ app_type: "container", detected_types: ["container"], ways: GO_WITH_DOCKERFILE }), defaults);
    expect(withForm(start, GO_WITH_DOCKERFILE, "server").appType).toBe("go");
  });

  it("does not take a form that cannot be", () => {
    const start = initialReview(inspection({ app_type: "python", detected_types: ["python"], ways: PYTHON }), defaults);
    expect(withForm(start, PYTHON, "container")).toBe(start);
    expect(withForm(start, COMPOSE, "server")).toBe(start);
  });

  it("is sent as the type of the way and, for a container, how it is built", () => {
    const start = { ...initialReview(inspection({ ways: NODE_WITH_DOCKERFILE }), defaults), domain: "shop.example.com" };
    const container = withForm(start, NODE_WITH_DOCKERFILE, "container");
    expect(createAppBody({ source: "https://github.com/example/shop", branch: "" }, container)).toMatchObject({ app_type: "container", container: { method: "dockerfile" } });
    const direct = createAppBody({ source: "https://github.com/example/shop", branch: "" }, start);
    expect(direct).toMatchObject({ app_type: "nodejs" });
    expect(direct).not.toHaveProperty("container");
  });

  it("keeps a type picked by hand when the repository is read again", () => {
    const type = inspection({ app_type: "container", detected_types: ["container"], ways: GO_WITH_DOCKERFILE });
    expect(keepsType(type, "go")).toBe(true);
    expect(keepsType(type, "container")).toBe(true);
    expect(keepsType(type, "vite")).toBe(false);
    const first = withForm(initialReview(type, defaults), GO_WITH_DOCKERFILE, "server");
    expect(initialReview(type, defaults, first).appType).toBe("go");
    // A server that says nothing of how it runs keeps the rule it always had.
    expect(keepsType(inspection({}), "container")).toBe(true);
    expect(keepsType(inspection({}), "go")).toBe(false);
  });
});
