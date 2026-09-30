import { describe, expect, it } from "vitest";

import { ApiError } from "../../api/errors";
import { generateSecret } from "./secrets";
import {
  WIZARD_STEPS,
  canIncludeWww,
  createAppBody,
  errorsOf,
  nextStep,
  previousStep,
  stepOfField,
  initialReview,
  inspectBody,
  manualInspection,
  persistentPathProblem,
  proposedPort,
  refusalOf,
  reviewProblems,
  sameSource,
  shortSource,
  sourceKind,
  sourceProblems,
  typeName,
  typeOptions,
  withProposal,
} from "./wizard";
import type { AppTypeOption, Inspection, PathRow, PlatformProposal, ReviewForm } from "./wizard";

const INSPECTION: Inspection = {
  app_type: "nextjs",
  detected_types: ["nextjs", "nodejs"],
  package_manager: "npm",
  install_command: ["npm", "ci"],
  build_command: ["npm", "run", "build"],
  start_command: "npm run start",
  default_port: 3000,
  env_keys: [
    { name: "DATABASE_URL", default: null, secret: false, required: true },
    { name: "NEXTAUTH_SECRET", default: null, secret: true, required: true },
    { name: "LOG_LEVEL", default: "info", secret: false, required: false },
  ],
  branch: "main",
  commit: "a1b2c3d",
};

const TYPES: AppTypeOption[] = [
  { type: "docker-compose", name: "Docker Compose", default_port: 3000 },
  { type: "monorepo", name: "Monorepo (Turborepo/pnpm)", default_port: 3000 },
  { type: "nextjs", name: "Next.js", default_port: 3000 },
  { type: "nodejs", name: "Node.js", default_port: 3000 },
  { type: "python", name: "Python (Django/Flask/FastAPI)", default_port: 8000 },
  { type: "static", name: "Static Site", default_port: 80 },
  { type: "vite", name: "Vite (React/Vue/Svelte)", default_port: 5173 },
  { type: "auto", name: "Auto-detect", default_port: 3000 },
];

const NOBODY = { domains: new Set<string>(), ports: new Map<number, string>(), cores: null };

function review(patch: Partial<ReviewForm> = {}): ReviewForm {
  return { ...initialReview(INSPECTION, { webserver: "nginx", taken: new Map() }), domain: "shop.example.com", ...patch };
}

function path(id: string, value: string): PathRow {
  return { id, value };
}

describe("the source", () => {
  it("tells a Git URL, an archive and a directory apart", () => {
    expect(sourceKind("https://github.com/you/app.git")).toBe("git");
    expect(sourceKind("git@github.com:you/app.git")).toBe("git");
    expect(sourceKind("ssh://git@host/you/app")).toBe("git");
    expect(sourceKind("https://example.com/releases/app-1.2.tar.gz")).toBe("archive");
    expect(sourceKind("/var/www/src/storefront")).toBe("local");
    expect(sourceKind("~/code/app")).toBe("local");
    expect(sourceKind("storefront")).toBe("unknown");
    expect(sourceKind("")).toBe("unknown");
  });

  it("asks for something that can be fetched, and a branch that is a branch", () => {
    expect(sourceProblems({ source: "", branch: "" })).toEqual({ source: expect.stringMatching(/Enter a Git URL/) as string });
    expect(sourceProblems({ source: "storefront", branch: "" })).toEqual({ source: expect.stringMatching(/start with \//) as string });
    expect(sourceProblems({ source: "/srv/app", branch: "feature/x y" })).toEqual({ branch: expect.stringMatching(/branch name/) as string });
    expect(sourceProblems({ source: "https://github.com/you/app.git", branch: "release/1.4" })).toEqual({});
  });

  it("shortens a source to its last two parts", () => {
    expect(shortSource("/tmp/noust-console-x/var/www/src/storefront/")).toBe("src/storefront");
    expect(shortSource("https://github.com/you/app.git")).toBe("you/app");
    expect(shortSource("git@github.com:you/app.git")).toBe("you/app");
  });
});

describe("the review the inspection proposes", () => {
  it("lists the detected types first, the closest match on top, then every other type as the API ordered them", () => {
    const options = typeOptions(TYPES, ["nextjs", "nodejs"]);
    expect(options.slice(0, 2)).toEqual([
      { value: "nextjs", label: "Next.js", hint: "Detected, the closest match" },
      { value: "nodejs", label: "Node.js", hint: "Also matches this repository" },
    ]);
    expect(options.map((option) => option.value)).toEqual(expect.arrayContaining(["vite", "python", "static", "monorepo", "docker-compose", "auto"]));
    expect(new Set(options.map((option) => option.value)).size).toBe(options.length);
  });

  it("names a type from the API's list, and falls back to the raw identifier for one it has not seen", () => {
    expect(typeName(TYPES, "nextjs")).toBe("Next.js");
    expect(typeName(TYPES, "static")).toBe("Static Site");
    expect(typeName([], "nextjs")).toBe("nextjs");
  });

  it("starts from the detected type, releases, HTTPS, no www, no persistent paths and no limits", () => {
    const form = initialReview(INSPECTION, { webserver: "apache", taken: new Map() });
    expect(form).toMatchObject({
      appType: "nextjs",
      domain: "",
      includeWww: false,
      webserver: "apache",
      ssl: true,
      port: "3000",
      layout: "releases",
      persistentPaths: [],
      limits: { memory: "", cpu: "", tasks: "" },
    });
    expect(form.env.map((row) => [row.name, row.value, row.required, row.secret])).toEqual([
      ["DATABASE_URL", "", true, false],
      ["NEXTAUTH_SECRET", "", true, true],
      ["LOG_LEVEL", "info", false, false],
    ]);
  });

  it("proposes the next free port when another app holds the default", () => {
    const taken = new Map([
      [3000, "shop.example.com"],
      [3001, "admin.example.com"],
    ]);
    expect(proposedPort(3000, taken)).toBe(3002);
    expect(initialReview(INSPECTION, { webserver: "nginx", taken }).port).toBe("3002");
  });

  it("keeps what the operator typed when the source is inspected again, www and limits included", () => {
    const typed = review({
      appType: "nodejs",
      port: "4100",
      includeWww: true,
      limits: { memory: "512", cpu: "", tasks: "" },
      persistentPaths: [path("path:1", "storage")],
      env: [...review().env],
    });
    typed.env = typed.env.map((row) => (row.name === "DATABASE_URL" ? { ...row, value: "postgres://db" } : row));
    typed.env.push({ id: "added:1", name: "EXTRA", value: "1", secret: false, required: false, declared: false, example: null });
    const again = initialReview({ ...INSPECTION, env_keys: INSPECTION.env_keys.slice(0, 1) }, { webserver: "apache", taken: new Map() }, typed);
    expect(again).toMatchObject({
      appType: "nodejs",
      domain: "shop.example.com",
      port: "4100",
      webserver: "nginx",
      includeWww: true,
      limits: { memory: "512", cpu: "", tasks: "" },
      persistentPaths: [{ id: "path:1", value: "storage" }],
    });
    expect(again.env.map((row) => [row.name, row.value])).toEqual([
      ["DATABASE_URL", "postgres://db"],
      ["EXTRA", "1"],
    ]);
  });

  it("starts with no type when the operator chooses it by hand", () => {
    const form = initialReview(manualInspection({ source: "/srv/app", branch: "" }), { webserver: "nginx", taken: new Map() });
    expect(form.appType).toBe("");
    expect(reviewProblems({ ...form, domain: "a.example.com" }, NOBODY)["appType"]).toMatch(/Choose the app type/);
  });
});

describe("also serving www", () => {
  it("offers it only for a bare two-label domain that is not already www", () => {
    expect(canIncludeWww("example.com")).toBe(true);
    expect(canIncludeWww("Example.COM")).toBe(true);
    expect(canIncludeWww("shop.example.com")).toBe(false);
    expect(canIncludeWww("www.example.com")).toBe(false);
    expect(canIncludeWww("localhost")).toBe(false);
  });
});

describe("persistent paths", () => {
  it("wants a path relative to the application, without '..' or a leading slash", () => {
    expect(persistentPathProblem("")).toMatch(/Enter a folder/);
    expect(persistentPathProblem("/etc/passwd")).toMatch(/relative to the application/);
    expect(persistentPathProblem("../secrets")).toMatch(/relative to the application/);
    expect(persistentPathProblem("storage")).toBeNull();
    expect(persistentPathProblem("public/uploads")).toBeNull();
  });
});

describe("what is wrong with the review", () => {
  it("wants a domain nobody deployed, a usable port and the required variables", () => {
    const problems = reviewProblems(review({ domain: "shop.example.com", port: "3000" }), {
      domains: new Set(["shop.example.com"]),
      ports: new Map([[3000, "admin.example.com"]]),
      cores: null,
    });
    expect(problems["domain"]).toMatch(/already deployed/);
    expect(problems["port"]).toBe("Port 3000 is used by admin.example.com.");
    expect(problems["env:declared:DATABASE_URL"]).toMatch(/expects one/);
    expect(problems["env:declared:NEXTAUTH_SECRET"]).toMatch(/expects one/);
    expect(problems["env:declared:LOG_LEVEL"]).toBeUndefined();
  });

  it("refuses ports the server refuses, and none for a static site", () => {
    expect(reviewProblems(review({ port: "80" }), NOBODY)["port"]).toBeUndefined();
    expect(reviewProblems(review({ port: "22" }), NOBODY)["port"]).toMatch(/below 1024/);
    expect(reviewProblems(review({ port: "abc" }), NOBODY)["port"]).toMatch(/port number/);
    expect(reviewProblems(review({ port: "", appType: "static" }), NOBODY)["port"]).toBeUndefined();
  });

  it("checks added variables' names and ignores an empty added row", () => {
    const form = review();
    form.env = [
      ...form.env.map((row) => ({ ...row, value: "x" })),
      { id: "added:1", name: "1BAD", value: "x", secret: false, required: false, declared: false, example: null },
      { id: "added:2", name: "", value: "", secret: false, required: false, declared: false, example: null },
      { id: "added:3", name: "LOG_LEVEL", value: "debug", secret: false, required: false, declared: false, example: null },
    ];
    expect(reviewProblems(form, NOBODY)).toEqual({
      "env-name:added:1": expect.stringMatching(/letters, digits and underscores/) as string,
      "env-name:added:3": "LOG_LEVEL is set twice.",
    });
  });

  it("checks persistent paths only on the releases layout, and ignores an empty row", () => {
    const releases = review({
      layout: "releases",
      persistentPaths: [path("path:1", ""), path("path:2", "../etc"), path("path:3", "storage"), path("path:4", "storage")],
    });
    const problems = reviewProblems(releases, NOBODY);
    expect(problems["path:path:1"]).toBeUndefined();
    expect(problems["path:path:2"]).toMatch(/relative to the application/);
    expect(problems["path:path:3"]).toBeUndefined();
    expect(problems["path:path:4"]).toBe("storage is listed twice.");

    const inplace = review({ layout: "inplace", persistentPaths: [path("path:1", "../etc")] });
    expect(reviewProblems(inplace, NOBODY)).not.toHaveProperty("path:path:1");
  });

  it("checks resource limits with the same bounds the app's Settings limits use", () => {
    const tooSmall = review({ limits: { memory: "32", cpu: "", tasks: "" } });
    expect(reviewProblems(tooSmall, NOBODY)["limit:memory"]).toMatch(/too small/);

    const tooMuchCpu = review({ limits: { memory: "", cpu: "500", tasks: "" } });
    expect(reviewProblems(tooMuchCpu, { ...NOBODY, cores: 2 })["limit:cpu"]).toMatch(/This machine has 2 CPUs/);
    expect(reviewProblems(tooMuchCpu, NOBODY)["limit:cpu"]).toBeUndefined();

    const notANumber = review({ limits: { memory: "", cpu: "", tasks: "abc" } });
    expect(reviewProblems(notANumber, NOBODY)["limit:tasks"]).toMatch(/whole number/);

    expect(reviewProblems(review({ limits: { memory: "512", cpu: "50", tasks: "256" } }), { ...NOBODY, cores: 4 })).not.toHaveProperty("limit:memory");
  });
});

describe("the request", () => {
  it("is exactly what POST /api/apps takes, www and empty limits included", () => {
    const form = review({ port: "3002", webserver: "apache", ssl: false, layout: "inplace" });
    form.env = form.env.map((row) => ({ ...row, value: row.name === "LOG_LEVEL" ? "debug" : `${row.name.toLowerCase()}-value` }));
    expect(createAppBody({ source: " https://github.com/you/app.git ", branch: " main " }, { ...form, domain: " Shop.Example.com " })).toEqual({
      domain: "shop.example.com",
      source: "https://github.com/you/app.git",
      branch: "main",
      app_type: "nextjs",
      port: 3002,
      webserver: "apache",
      ssl: false,
      layout: "inplace",
      include_www: false,
      memory_max_mb: null,
      cpu_quota_percent: null,
      tasks_max: null,
      env_vars: { DATABASE_URL: "database_url-value", NEXTAUTH_SECRET: "nextauth_secret-value", LOG_LEVEL: "debug" },
      skip_database: false,
    });
  });

  it("sends no branch for a directory and no port for a static site", () => {
    const body = createAppBody({ source: "/srv/landing", branch: "main" }, review({ appType: "static" }));
    expect(body).not.toHaveProperty("branch");
    expect(body).not.toHaveProperty("port");
  });

  it("sends include_www only where it would mean something, even if it was ticked before the domain changed", () => {
    const subdomain = createAppBody({ source: "/srv/app", branch: "" }, review({ domain: "shop.example.com", includeWww: true }));
    expect(subdomain.include_www).toBe(false);
    const bare = createAppBody({ source: "/srv/app", branch: "" }, review({ domain: "example.com", includeWww: true }));
    expect(bare.include_www).toBe(true);
  });

  it("sends persistent paths only on the releases layout, trimmed and without empty rows", () => {
    const releases = createAppBody(
      { source: "/srv/app", branch: "" },
      review({ layout: "releases", persistentPaths: [path("path:1", " storage "), path("path:2", "")] }),
    );
    expect(releases.persistent_paths).toEqual(["storage"]);

    const inplace = createAppBody({ source: "/srv/app", branch: "" }, review({ layout: "inplace", persistentPaths: [path("path:1", "storage")] }));
    expect(inplace).not.toHaveProperty("persistent_paths");
  });

  it("sends resource limits as numbers, or null for an empty field", () => {
    const body = createAppBody({ source: "/srv/app", branch: "" }, review({ limits: { memory: "512", cpu: "", tasks: "256" } }));
    expect(body).toMatchObject({ memory_max_mb: 512, cpu_quota_percent: null, tasks_max: 256 });
  });
});

describe("the steps", () => {
  it("has a configuration step only for an application started from code", () => {
    expect(WIZARD_STEPS.code).toEqual(["source", "address", "configure", "variables", "deploy"]);
    expect(WIZARD_STEPS.recipe).toEqual(["source", "address", "variables", "deploy"]);
    expect(WIZARD_STEPS.import).toEqual(["source", "address", "variables", "deploy"]);
    expect(nextStep("code", "address")).toBe("configure");
    expect(nextStep("recipe", "address")).toBe("variables");
    expect(nextStep("code", "deploy")).toBeNull();
    expect(previousStep("import", "variables")).toBe("address");
    expect(previousStep("code", "source")).toBeNull();
  });

  it("knows which step asks for each field", () => {
    expect(stepOfField("domain")).toBe("address");
    expect(stepOfField("source")).toBe("address");
    expect(stepOfField("appType")).toBe("configure");
    expect(stepOfField("port")).toBe("configure");
    expect(stepOfField("path:path:1")).toBe("configure");
    expect(stepOfField("limit:memory")).toBe("configure");
    expect(stepOfField("health_path")).toBe("configure");
    expect(stepOfField("env:declared:DATABASE_URL")).toBe("variables");
    expect(stepOfField("env-name:added:1")).toBe("variables");
    expect(stepOfField("secret:STRIPE_KEY")).toBe("variables");
    expect(errorsOf({ domain: "a", port: "b", "env:x": "c" }, "configure")).toEqual([["port", "b"]]);
  });
});

describe("refusals", () => {
  it("sends field errors to the step that asks for the field", () => {
    expect(refusalOf(new ApiError(422, "validation_error", "Validation failed", null, { source: "field required" }))).toEqual({
      step: "source",
      fields: { source: "field required" },
    });
    expect(refusalOf(new ApiError(422, "validation_error", "Validation failed", null, { port: "not an integer", app_type: "bad" }))).toEqual({
      step: "configure",
      fields: { port: "not an integer", appType: "bad" },
    });
    expect(refusalOf(new ApiError(422, "validation_error", "Validation failed", null, { domain: "not a domain" }))).toEqual({
      step: "address",
      fields: { domain: "not a domain" },
    });
  });

  it("puts a taken domain, a refused port and an unfetchable source on their fields", () => {
    expect(refusalOf(new ApiError(409, "conflict", "Application already exists: a.com"))).toEqual({ step: "address", fields: { domain: "Application already exists: a.com" } });
    expect(refusalOf(new ApiError(400, "porterror", "Port 3000 is already in use"))).toEqual({ step: "configure", fields: { port: "Port 3000 is already in use" } });
    expect(refusalOf(new ApiError(400, "sourceerror", "Source path does not exist: /x"))).toEqual({ step: "source", fields: { source: "Source path does not exist: /x" } });
    expect(refusalOf(new ApiError(500, "internal", "boom"))).toBeNull();
    expect(refusalOf(new Error("boom"))).toBeNull();
  });
});

describe("a repository chosen on GitHub", () => {
  const chosen = { source: "github:acme/storefront", branch: "production", installationId: 7001 };

  it("is a source of its own kind, valid as it is", () => {
    expect(sourceKind("github:acme/storefront")).toBe("github");
    expect(sourceKind("github:acme")).toBe("unknown");
    expect(sourceKind("github:-acme/app")).toBe("unknown");
    expect(sourceProblems(chosen)).toEqual({});
    expect(shortSource("github:acme/storefront")).toBe("acme/storefront");
  });

  it("carries its installation into the inspection and the deploy", () => {
    expect(inspectBody(chosen)).toEqual({ source: "github:acme/storefront", branch: "production", github_installation_id: 7001 });
    expect(inspectBody({ source: " /srv/app ", branch: "main" })).toEqual({ source: "/srv/app" });
    expect(createAppBody(chosen, review())).toMatchObject({ source: "github:acme/storefront", branch: "production", github_installation_id: 7001 });
    expect(createAppBody({ source: "https://github.com/acme/app.git", branch: "" }, review())).not.toHaveProperty("github_installation_id");
  });

  it("is another source under another installation", () => {
    expect(sameSource(chosen, { ...chosen })).toBe(true);
    expect(sameSource(chosen, { ...chosen, installationId: 7002 })).toBe(false);
    expect(sameSource(chosen, { source: chosen.source, branch: chosen.branch })).toBe(false);
  });

  it("sends a refused installation back to the source", () => {
    expect(refusalOf(new ApiError(422, "validation_error", "Validation failed", null, { github_installation_id: "not covered" }))).toEqual({
      step: "source",
      fields: { source: "not covered" },
    });
  });
});

describe("generated secrets", () => {
  it("are 32 random bytes as URL-safe base64, from the source they are given", () => {
    const secret = generateSecret(32, (array) => array.fill(0xff));
    expect(secret).toBe("_".repeat(42) + "8");
    expect(generateSecret()).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(generateSecret()).not.toBe(generateSecret());
  });
});

describe("another platform's configuration", () => {
  const PROPOSAL: PlatformProposal = {
    platform: "railway",
    files: ["railway.toml"],
    app_type: null,
    install_command: null,
    build_command: "npm run build",
    start_command: "node server.js",
    output_directory: null,
    port: 8080,
    health_path: "/healthz",
    health_timeout: 60,
    env: [
      { name: "DATABASE_URL", value: null, secret: true, generated: false, required: true, note: "${{Postgres.DATABASE_URL}}" },
      { name: "SESSION_SECRET", value: null, secret: true, generated: true, required: false, note: null },
      { name: "NODE_ENV", value: "production", secret: false, generated: false, required: false, note: null },
    ],
    databases: ["postgresql"],
    domains: [],
    persistent_paths: ["data"],
    warnings: ["Railway's cron schedule has no equivalent; add it with noust cron add."],
  };
  const WITH: Inspection = { ...INSPECTION, platform_proposal: PROPOSAL };

  it("fills in its port, its variables .env.example does not declare, and its persistent paths", () => {
    const form = initialReview(WITH, { webserver: "nginx", taken: new Map([[8080, "shop.example.com"]]) });
    expect(form.useProposal).toBe(true);
    // 8080 is taken on this machine, so the next free port after it.
    expect(form.port).toBe("8081");
    expect(form.persistentPaths.map((row) => row.value)).toEqual(["data"]);
    // DATABASE_URL is already declared by .env.example: that row stays the only one.
    expect(form.env.filter((row) => row.name === "DATABASE_URL")).toHaveLength(1);
    const session = form.env.find((row) => row.name === "SESSION_SECRET");
    expect(session?.value).toMatch(/^[A-Za-z0-9_-]{43}$/);
    expect(session).toMatchObject({ secret: true, required: false, declared: true, proposed: { generated: true, note: null } });
    expect(form.env.find((row) => row.name === "NODE_ENV")).toMatchObject({ value: "production", example: "production" });
  });

  it("takes out what it filled in when turned off, and keeps what the operator typed when turned on again", () => {
    const on = initialReview(WITH, { webserver: "nginx", taken: new Map() });
    const typed = { ...on, env: on.env.map((row) => (row.name === "NODE_ENV" ? { ...row, value: "staging" } : row)) };
    const off = withProposal(typed, WITH, new Map(), false);
    expect(off.useProposal).toBe(false);
    expect(off.port).toBe("3000");
    expect(off.persistentPaths).toEqual([]);
    expect(off.env.map((row) => row.name)).toEqual(["DATABASE_URL", "NEXTAUTH_SECRET", "LOG_LEVEL"]);

    const again = withProposal({ ...typed }, WITH, new Map(), true);
    expect(again.env.find((row) => row.name === "NODE_ENV")?.value).toBe("staging");
  });

  it("leaves a port the operator changed alone when it is turned off", () => {
    const on = initialReview(WITH, { webserver: "nginx", taken: new Map() });
    expect(withProposal({ ...on, port: "4000" }, WITH, new Map(), false).port).toBe("4000");
  });

  it("stays off when the source is inspected again after it was turned off", () => {
    const off = withProposal(initialReview(WITH, { webserver: "nginx", taken: new Map() }), WITH, new Map(), false);
    const again = initialReview(WITH, { webserver: "nginx", taken: new Map() }, off);
    expect(again.useProposal).toBe(false);
    expect(again.env.some((row) => row.proposed !== undefined)).toBe(false);
  });

  it("requires a value its configuration does not give, in words that do not blame .env.example", () => {
    const proposal = { ...PROPOSAL, env: [{ name: "STRIPE_KEY", value: null, secret: true, generated: false, required: true, note: null }] };
    const form = { ...initialReview({ ...INSPECTION, env_keys: [], platform_proposal: proposal }, { webserver: "nginx", taken: new Map() }), domain: "shop.example.com" };
    const problems = reviewProblems(form, NOBODY);
    expect(Object.values(problems)).toContain("The platform's configuration gives it no value, so the app expects one.");
  });

  it("sends what it filled in with the rest of the review", () => {
    const form = { ...initialReview(WITH, { webserver: "nginx", taken: new Map() }), domain: "shop.example.com" };
    const body = createAppBody({ source: "/srv/app", branch: "" }, form, PROPOSAL);
    expect(body).toMatchObject({ port: 8080, persistent_paths: ["data"], health_path: "/healthz", health_timeout: 60 });
    expect(body).not.toHaveProperty("health_expect");
    expect(body.env_vars).toMatchObject({ NODE_ENV: "production" });
    expect(body.env_vars?.["SESSION_SECRET"]).toMatch(/^[A-Za-z0-9_-]{43}$/);
  });

  it("sends its health check only while it is used, and only the parts it gives", () => {
    const on = { ...initialReview(WITH, { webserver: "nginx", taken: new Map() }), domain: "shop.example.com" };
    const off = withProposal(on, WITH, new Map(), false);
    expect(createAppBody({ source: "/srv/app", branch: "" }, off, PROPOSAL)).not.toHaveProperty("health_path");
    expect(createAppBody({ source: "/srv/app", branch: "" }, on, { ...PROPOSAL, health_timeout: null })).toMatchObject({ health_path: "/healthz" });
    expect(createAppBody({ source: "/srv/app", branch: "" }, on, { ...PROPOSAL, health_timeout: null })).not.toHaveProperty("health_timeout");
  });

  it("changes nothing for an inspection without one", () => {
    const form = initialReview(INSPECTION, { webserver: "nginx", taken: new Map() });
    expect(form.env.every((row) => row.proposed === undefined)).toBe(true);
    expect(form.port).toBe("3000");
  });
});
