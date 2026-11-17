import { describe, expect, it } from "vitest";

import type { DockerCan, DockerContainer, DockerContainerUsage, DockerImage, DockerNetwork, DockerOwner, DockerVolume } from "../../api/queries/docker";
import { containerVerbs, imageVerbs, isUp, networkVerbs, ownerOf, ownerTarget, stackVerbs, stateView, usageIndex, usageOf, usageReason, versionKindOf, volumeCopyOf, volumeVerbs, volumesWithoutCopy } from "./model";

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

  it("offers a copy where the server says the command that takes one, even for an application's volume the owner changes: a copy is not changing it", () => {
    const via = "noust backup volume create wiki_data";
    expect(volumeVerbs(row(NOBODY, ALL, { backup: { state: "none", via } }) as unknown as DockerVolume)).toEqual({ direct: ["backup", "remove"], viaOwner: false });
    expect(volumeVerbs(row(SHOP, OWNED, { backup: { state: "scheduled", via } }) as unknown as DockerVolume)).toEqual({ direct: ["backup"], viaOwner: true });
    // Nothing is offered where there is no command to show, nor for a node that does not say.
    expect(volumeVerbs(row(NOBODY, ALL, { backup: { state: "none", via: "" } }) as unknown as DockerVolume).direct).toEqual(["remove"]);
  });

  it("offers no update of an image that was built on this machine: there is no registry to ask", () => {
    const built = (source: "built_noust" | "built_other") => imageVerbs({ ...row(NOBODY, ALL, { source }), update: { state: "newer", mode: "with_net", apply: true } as DockerImage["update"] });
    expect(built("built_noust").direct).toEqual(["remove"]);
    expect(built("built_other").direct).toEqual(["remove"]);
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
  const image = (more: Partial<Pick<DockerImage, "way_back" | "dangling" | "source">> & { update: DockerImage["update"] }) => ({ way_back: false, dangling: false, source: "registry" as const, ...more });

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

  it("says built here for what was built on this machine, by Noust or by another hand, as the server says where the image comes from", () => {
    expect(versionKindOf(image({ source: "built_noust", update: update("not_looked", "", null, SHOP) }))).toBe("builtHere");
    expect(versionKindOf(image({ source: "built_other", update: update("not_looked", "", null) }))).toBe("builtHere");
    // Nothing built has a registry to ask, so a look that found something does not change it.
    expect(versionKindOf(image({ source: "built_other", update: null }))).toBe("builtHere");
  });

  it("does not work out where an image comes from by the registry it lacks: only the server says it", () => {
    // Noust's own images are pulled: with no reference they were once taken for built here, and the server's word says they are not.
    expect(versionKindOf(image({ source: "registry", update: update("not_looked", "", null, NOUST) }))).toBe("notLooked");
    expect(versionKindOf(image({ source: "registry", update: update("not_looked", "", null) }))).toBe("notLooked");
  });

  it("says not looked at where nobody knows where the image comes from, a state of its own and not a missing value", () => {
    expect(versionKindOf(image({ source: "unknown", update: update("not_looked", "", null) }))).toBe("notLooked");
    expect(versionKindOf(image({ source: "unknown", update: null }))).toBe("notLooked");
  });

  it("reads an image of a node that does not say where it comes from as one nobody looked at, and keeps what that node did say of its version", () => {
    expect(versionKindOf({ way_back: false, dangling: false, update: update("not_looked", "", null) })).toBe("notLooked");
    expect(versionKindOf({ way_back: false, dangling: false, update: update("newer", "minor", "nginx:1.27") })).toBe("newer");
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

describe("the use of every container, in one reading", () => {
  const usage = (id: string, name: string, more: Partial<DockerContainerUsage["usage"]> = {}): DockerContainerUsage => ({
    id,
    name,
    usage: { available: true, reason: "", cpu_percent: 14, memory_bytes: 222_000_000, memory_limit_bytes: null, memory_percent: null, ...more },
  });
  const web = { id: "a".repeat(64), name: "shop-example-com-web-1" };

  it("is joined to a container by its id, and by its name where the list has a short id", () => {
    const index = usageIndex([usage("a".repeat(64), "shop-example-com-web-1"), usage("b".repeat(64), "adminer")]);
    expect(usageOf(index, web)?.cpu_percent).toBe(14);
    expect(usageOf(index, { id: "aaaaaaaaaaaa", name: "shop-example-com-web-1" })?.cpu_percent).toBe(14);
    expect(usageOf(index, { id: "zzzzzzzzzzzz", name: "unknown" })).toBeUndefined();
  });

  it("joins a container called stats by its id: its name is a route of the API, and the reading is found all the same", () => {
    const index = usageIndex([usage("c".repeat(64), "stats", { cpu_percent: 3 })]);
    expect(usageOf(index, { id: "c".repeat(64), name: "stats" })?.cpu_percent).toBe(3);
  });

  it("is nothing before the reading arrives, and for a node that has none to give", () => {
    expect(usageOf(usageIndex(undefined), web)).toBeUndefined();
    expect(usageOf(usageIndex([]), web)).toBeUndefined();
  });

  it("says why there is no reading with the four words of the closed list, and an unknown one as no reading", () => {
    for (const reason of ["not_running", "restarting", "too_young", "no_reading"] as const) expect(usageReason(reason)).toBe(reason);
    expect(usageReason("something_new")).toBe("no_reading");
    expect(usageReason("")).toBe("no_reading");
  });
});

describe("whether a volume is in a copy", () => {
  const backup = (state: "scheduled" | "copied" | "none", last_at: string | null = null) => ({ backup: { state, last_at, via: "noust backup volume create x" } });

  it("says the three states of the server, when the newest copy was made and the command that takes one", () => {
    expect(volumeCopyOf(backup("scheduled", "2026-11-15T03:00:00Z"))).toEqual({ state: "scheduled", lastAt: "2026-11-15T03:00:00Z", via: "noust backup volume create x" });
    expect(volumeCopyOf(backup("copied", "2026-11-01T03:00:00Z")).state).toBe("copied");
    expect(volumeCopyOf(backup("none")).lastAt).toBeNull();
  });

  it("says nothing of it for a node that does not say, and does not take that for no copy", () => {
    expect(volumeCopyOf({})).toEqual({ state: "unknown", lastAt: null, via: "" });
    expect(volumeCopyOf({ backup: undefined }).state).toBe("unknown");
  });

  it("counts the volumes that hold data and are in no copy, and what they hold only when every one says its size", () => {
    const volume = (state: "scheduled" | "copied" | "none", size: number | null, anonymous = false) => ({ ...backup(state), size_bytes: size, anonymous });
    expect(volumesWithoutCopy([volume("none", 100), volume("none", 50), volume("scheduled", 900), volume("copied", 5)])).toEqual({ count: 2, bytes: 150 });
    // A volume of 0 bytes holds nothing to lose, and an anonymous one is what a container made without being asked.
    expect(volumesWithoutCopy([volume("none", 0), volume("none", 10, true)])).toEqual({ count: 0, bytes: 0 });
    // A size not read yet is not zero: the count stands and the weight waits.
    expect(volumesWithoutCopy([volume("none", 100), volume("none", null)])).toEqual({ count: 2, bytes: null });
    expect(volumesWithoutCopy([{ size_bytes: 10, anonymous: false }])).toEqual({ count: 0, bytes: 0 });
  });
});
