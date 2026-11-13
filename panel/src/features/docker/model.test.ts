import { describe, expect, it } from "vitest";

import type { DockerCan, DockerContainer, DockerImage, DockerNetwork, DockerOwner, DockerVolume } from "../../api/queries/docker";
import { containerVerbs, imageVerbs, isUp, networkVerbs, ownerOf, ownerTarget, stackVerbs, stateView, versionKindOf, volumeVerbs } from "./model";

const NOBODY: DockerOwner = { kind: "nobody", subject: "", project: "" };
const SHOP: DockerOwner = { kind: "app", subject: "shop.example.com", project: "" };
const PG: DockerOwner = { kind: "database", subject: "postgresql@pg16", project: "" };
const NOUST: DockerOwner = { kind: "noust", subject: "buildkit", project: "" };

const ALL: DockerCan = { start: true, stop: true, restart: true, remove: true, console: true, via: "", blocked: "" };
const OWNED: DockerCan = { start: false, stop: false, restart: false, remove: false, console: false, via: "Do it to the whole application: noust stop shop.example.com.", blocked: "owner" };

const container = (state: string, can: DockerCan, owner: DockerOwner): Pick<DockerContainer, "state" | "can" | "owner"> => ({ state, can, owner });

describe("which state a container is drawn in", () => {
  it("gives each kind of the closed list a state of the console's own and a word, never colour alone", () => {
    expect(stateView("running")).toEqual({ status: "running", word: "running" });
    expect(stateView("unhealthy")).toEqual({ status: "warning", word: "unhealthy" });
    for (const kind of ["loop", "oom", "killed", "error", "dead"] as const) expect(stateView(kind).status).toBe("failed");
    for (const kind of ["terminated", "ended", "stopped", "paused"] as const) expect(stateView(kind).status).toBe("stopped");
    expect(stateView("created")).toEqual({ status: "queued", word: "created" });
  });

  it("draws a kind it does not know as stopped, in its own words, instead of failing a page", () => {
    expect(stateView("hibernating")).toEqual({ status: "unknown", word: "other" });
  });

  it("says a container is up while Docker has it running, restarting or paused", () => {
    expect(isUp("running")).toBe(true);
    expect(isUp("restarting")).toBe(true);
    expect(isUp("paused")).toBe(true);
    expect(isUp("exited")).toBe(false);
    expect(isUp("created")).toBe(false);
  });
});

describe("what a container's menu offers", () => {
  it("offers a running container nobody of Noust made everything that works on a running one, with remove last", () => {
    expect(containerVerbs(container("running", ALL, NOBODY))).toEqual({ direct: ["logs", "console", "restart", "stop", "remove"], viaOwner: false });
  });

  it("offers a stopped one to start and not to stop, restart or open a console in", () => {
    expect(containerVerbs(container("exited", ALL, NOBODY))).toEqual({ direct: ["logs", "start", "remove"], viaOwner: false });
  });

  it("does not offer what the server says cannot be done, and says once that the owner does the rest", () => {
    expect(containerVerbs(container("running", OWNED, SHOP))).toEqual({ direct: ["logs"], viaOwner: true });
    expect(containerVerbs(container("exited", OWNED, PG))).toEqual({ direct: ["logs"], viaOwner: true });
  });

  it("offers Noust's own containers their logs and nothing else, and no line to an owner that has no page", () => {
    expect(containerVerbs(container("running", OWNED, NOUST))).toEqual({ direct: ["logs"], viaOwner: false });
  });

  it("offers a console only when the server says it may be opened", () => {
    const withoutConsole: DockerCan = { ...ALL, console: false };
    expect(containerVerbs(container("running", withoutConsole, NOBODY)).direct).not.toContain("console");
  });
});

const row = <T extends object>(owner: DockerOwner, can: DockerCan, more: T): T & { owner: DockerOwner; can: DockerCan } => ({ owner, can, ...more });

describe("what an image, a volume and a network offer", () => {
  it("offers to remove only what the server says may be removed", () => {
    expect(imageVerbs(row(NOBODY, ALL, { way_back: false }) as unknown as DockerImage)).toEqual({ direct: ["remove"], viaOwner: false });
    expect(imageVerbs(row(SHOP, OWNED, { way_back: true }) as unknown as DockerImage)).toEqual({ direct: [], viaOwner: true });
    expect(volumeVerbs(row(NOBODY, ALL, { anonymous: false }) as unknown as DockerVolume)).toEqual({ direct: ["remove"], viaOwner: false });
    expect(volumeVerbs(row(SHOP, OWNED, {}) as unknown as DockerVolume)).toEqual({ direct: [], viaOwner: true });
    expect(networkVerbs(row(NOBODY, ALL, { builtin: false }) as unknown as DockerNetwork)).toEqual({ direct: ["remove"], viaOwner: false });
  });

  it("offers nothing on Docker's own networks: they cannot be removed and it does not pretend", () => {
    expect(networkVerbs(row(NOBODY, { ...ALL, remove: false, blocked: "builtin" }, { builtin: true }) as unknown as DockerNetwork)).toEqual({ direct: [], viaOwner: false });
  });
});

describe("what an image's menu offers of updating", () => {
  const image = (update: object | null, can: DockerCan = ALL, owner: DockerOwner = NOBODY) => ({ owner, can, update: update as DockerImage["update"] });

  it("offers to update an image the server says goes ahead with a net, before remove", () => {
    expect(imageVerbs(image({ state: "newer", mode: "with_net", apply: true }, ALL, SHOP)).direct).toEqual(["update", "remove"]);
  });

  it("offers to bring the stack first when that is what stands between a stack nobody made and an update with a net", () => {
    expect(imageVerbs(image({ state: "newer", mode: "bring_first", stack: "nextcloud", apply: false })).direct).toEqual(["bring", "remove"]);
  });

  it("offers neither for what is current, not looked at, loose or an owner's: the reason is on the row, never a disabled verb", () => {
    expect(imageVerbs(image({ state: "current", mode: "none", apply: false })).direct).toEqual(["remove"]);
    expect(imageVerbs(image(null)).direct).toEqual(["remove"]);
    expect(imageVerbs(image({ state: "newer", mode: "loose", apply: false })).direct).toEqual(["remove"]);
    expect(imageVerbs(image({ state: "newer", mode: "via_owner", apply: false }, OWNED, PG))).toEqual({ direct: [], viaOwner: true });
  });
});

describe("what a stack's menu offers", () => {
  const stack = (owner: DockerOwner, can: DockerCan | undefined, running: number, total: number) => ({ owner, can, running, total });

  it("offers a stack that runs to stop and restart, and one that is partly stopped to start as well", () => {
    expect(stackVerbs(stack(NOBODY, ALL, 3, 3))).toEqual({ direct: ["restart", "stop"], viaOwner: false });
    expect(stackVerbs(stack(NOBODY, ALL, 2, 3))).toEqual({ direct: ["restart", "stop", "start"], viaOwner: false });
  });

  it("offers a stack that is entirely stopped to start and nothing else", () => {
    expect(stackVerbs(stack(NOBODY, ALL, 0, 3))).toEqual({ direct: ["start"], viaOwner: false });
  });

  it("offers an application's stack nothing of its own and sends to the application, which has the health gate", () => {
    expect(stackVerbs(stack(SHOP, OWNED, 3, 3))).toEqual({ direct: [], viaOwner: true });
  });

  it("offers nothing where the server gave no verdict, and does not guess one", () => {
    expect(stackVerbs(stack(NOBODY, undefined, 3, 3))).toEqual({ direct: [], viaOwner: false });
  });
});

describe("where an owner leads", () => {
  it("leads an application's thing to the application and a database engine's to the engines", () => {
    expect(ownerTarget(SHOP)).toEqual({ to: "/apps/$domain", params: { domain: "shop.example.com" } });
    expect(ownerTarget(PG)).toEqual({ to: "/databases/engines" });
  });

  it("leads Noust's own and nobody's nowhere", () => {
    expect(ownerTarget(NOUST)).toBeUndefined();
    expect(ownerTarget(NOBODY)).toBeUndefined();
  });

  it("reads an owner of a kind this console does not know as nobody's, in words", () => {
    expect(ownerOf({ kind: "robot", subject: "x", project: "" })).toEqual({ kind: "nobody", subject: "" });
    expect(ownerOf(SHOP)).toEqual({ kind: "app", subject: "shop.example.com" });
  });
});

describe("how an image's version is marked", () => {
  const update = (state: string, change: string, reference: string | null, owner: DockerOwner = NOBODY) =>
    ({ state, change, reference, owner, mode: "none" }) as unknown as NonNullable<DockerImage["update"]>;
  const image = (more: Partial<Pick<DockerImage, "way_back" | "dangling">> & { update: DockerImage["update"] }) => ({ way_back: false, dangling: false, ...more });

  it("keeps the way back first: an image an application keeps for going back is never offered as a version to take", () => {
    expect(versionKindOf(image({ way_back: true, update: update("newer", "minor", "nginx:1.27") }))).toBe("keeps");
  });

  it("says untagged before anything else it could say of an image with no name", () => {
    expect(versionKindOf(image({ dangling: true, update: null }))).toBe("untagged");
  });

  it("says not looked at where nobody has looked, and could not be asked where a look failed", () => {
    expect(versionKindOf(image({ update: null }))).toBe("notLooked");
    expect(versionKindOf(image({ update: update("not_looked", "", "nginx:1.27") }))).toBe("notLooked");
    expect(versionKindOf(image({ update: update("unknown", "", "nginx:1.27") }))).toBe("unknown");
  });

  it("says built here for an image with no registry to ask, except Noust's own, which is not built here", () => {
    expect(versionKindOf(image({ update: update("not_looked", "", null) }))).toBe("builtHere");
    expect(versionKindOf(image({ update: update("not_looked", "", null, NOUST) }))).toBe("notLooked");
  });

  it("says a new version is new, and a new major is a major one, which is a decision and not an update", () => {
    expect(versionKindOf(image({ update: update("newer", "patch", "nginx:1.27") }))).toBe("newer");
    expect(versionKindOf(image({ update: update("newer", "minor", "nginx:1.27") }))).toBe("newer");
    expect(versionKindOf(image({ update: update("newer", "unknown", "nginx:latest") }))).toBe("newer");
    expect(versionKindOf(image({ update: update("newer", "major", "postgres:16") }))).toBe("major");
  });

  it("says up to date", () => {
    expect(versionKindOf(image({ update: update("current", "", "nginx:1.27") }))).toBe("current");
  });
});
