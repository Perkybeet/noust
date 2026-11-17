/**
 * What the Docker zone decides from what the server says, apart from drawing it: the state a container is shown in, what a row's
 * menu offers (from the server's `can`, never from a rule of the console's own), where an owner leads and how an image's version
 * is marked. Plain functions over the API's types, so each rule is a test and no page repeats one.
 */

import type { DockerCan, DockerContainer, DockerContainerUsage, DockerImage, DockerNetwork, DockerOwner, DockerStack, DockerUsage, DockerVolume } from "../../api/queries/docker";
import type { OwnerKind, OwnerTarget, VersionKind } from "../../components/page";
import type { Status } from "../../components/ui";

/** Why the document of a stack is not edited from the console, as the server says it (`docker.compose.why.*`). */
export const COMPOSE_WHY_CODES = ["repository", "recipe", "generated", "bring_first", "no_file", "not_compose", "owned"] as const;

/** The words a container's state is said in (`docker.state.*`). */
export type StateWord = "running" | "unhealthy" | "loop" | "oom" | "killed" | "error" | "dead" | "terminated" | "ended" | "stopped" | "paused" | "created" | "other";

export interface StateView {
  status: Status;
  word: StateWord;
}

/**
 * The state of a container as the console draws it. The server decides the kind (one word of a closed list, the same on every
 * screen and in the summary); this gives it the shape and the word. A kind the console does not know is drawn as unknown rather
 * than failing the page, so a server that grows a state does not break a console that has not learned it.
 */
export function stateView(kind: string): StateView {
  switch (kind) {
    case "running":
      return { status: "running", word: "running" };
    case "unhealthy":
      return { status: "warning", word: "unhealthy" };
    case "loop":
    case "oom":
    case "killed":
    case "error":
    case "dead":
      return { status: "failed", word: kind };
    case "terminated":
    case "ended":
    case "stopped":
    case "paused":
      return { status: "stopped", word: kind };
    case "created":
      return { status: "queued", word: "created" };
    default:
      return { status: "unknown", word: "other" };
  }
}

/** Whether Docker has the container up: running, restarting or paused. What is up is stopped; what is not is started. */
export function isUp(state: string): boolean {
  return state === "running" || state === "restarting" || state === "paused";
}

/** A verb a row's menu may offer. `backup` is a volume's. */
export type Verb = "logs" | "console" | "restart" | "stop" | "start" | "remove" | "update" | "bring" | "backup";

export interface Verbs {
  /** What works here, in the order the menu draws it: what removes is last. */
  direct: Verb[];
  /** The owner does the rest: the menu says so once, in one line that leads there. */
  viaOwner: boolean;
}

/** Whether the rest of what the server refused is done from an owner that has a page. */
function throughOwner(can: DockerCan, owner: DockerOwner): boolean {
  return can.blocked === "owner" && ownerTarget(owner) !== undefined;
}

/**
 * What a container's menu offers. The logs are always there (reading is for every role that reads Docker); a console only where the
 * server says it may be opened and the container is up; start where it is not up and stop and restart where it is, each only where
 * the server says that owner allows it. What an application's or an engine's container is refused is the owner's to do, said once.
 */
export function containerVerbs(container: Pick<DockerContainer, "state" | "can" | "owner">): Verbs {
  const { can } = container;
  const up = isUp(container.state);
  const direct: Verb[] = ["logs"];
  if (up && can.console) direct.push("console");
  if (up && can.restart) direct.push("restart");
  if (up && can.stop) direct.push("stop");
  if (!up && can.start) direct.push("start");
  if (can.remove) direct.push("remove");
  return { direct, viaOwner: throughOwner(can, container.owner) };
}

/** An image's name as it is typed: the first reference it has, and `<none>:<none>` for one with no name. */
export function imageName(image: Pick<DockerImage, "tags" | "id">): string {
  return image.tags !== undefined && image.tags.length > 0 ? (image.tags[0] ?? "") : "<none>:<none>";
}

/**
 * An image's menu. Update where the server says the update can go ahead from here (an application's image, with the health gate and
 * the way back); bring its stack to Noust where it is the one thing between a stack Noust did not create and an update with a net;
 * remove where the server allows it; and the owner's line where an application keeps it. A new version of what the server says is
 * not updated from here (a container on its own, a database engine's) offers nothing: the reason is on the row, never a disabled verb.
 */
export function imageVerbs(image: Pick<DockerImage, "can" | "owner"> & { source?: DockerImage["source"] | undefined; update?: DockerImage["update"] | undefined }): Verbs {
  const direct: Verb[] = [];
  const update = image.update;
  // What was built on this machine has no registry to ask, so there is no new version of it to take.
  const built = image.source === "built_noust" || image.source === "built_other";
  if (!built && update?.state === "newer" && update.mode === "with_net" && update.apply) direct.push("update");
  if (!built && update?.state === "newer" && update.mode === "bring_first" && typeof update.stack === "string" && update.stack !== "") direct.push("bring");
  if (image.can.remove) direct.push("remove");
  return { direct, viaOwner: throughOwner(image.can, image.owner) };
}

/**
 * A volume's menu: a copy now where the server gives the command that takes one (an application's volume: what Noust did not make
 * has none, and a node that does not say has none to offer), whatever the owner does or does not allow, and remove (which asks for its
 * name) where the server allows it.
 */
export function volumeVerbs(volume: Pick<DockerVolume, "can" | "owner"> & { backup?: DockerVolume["backup"] | undefined }): Verbs {
  const direct: Verb[] = [];
  if (volume.backup !== undefined && volume.backup.via !== "") direct.push("backup");
  if (volume.can.remove) direct.push("remove");
  return { direct, viaOwner: throughOwner(volume.can, volume.owner) };
}

/** Whether a volume is in a copy: a policy takes it, it has a copy no policy takes, none, or the server does not say (a node of 4.2). */
export type VolumeCopyState = "scheduled" | "copied" | "none" | "unknown";

export interface VolumeCopy {
  state: VolumeCopyState;
  /** When the newest copy that holds the volume was made. */
  lastAt: string | null;
  /** The command that takes a copy now, as the server says it. */
  via: string;
}

/** What the server says of a volume's copies, read so that a node that says nothing is unknown and never "none": it is not that there is no copy. */
export function volumeCopyOf(volume: { backup?: DockerVolume["backup"] | undefined }): VolumeCopy {
  const backup = volume.backup;
  if (backup === undefined) return { state: "unknown", lastAt: null, via: "" };
  return { state: backup.state, lastAt: backup.last_at ?? null, via: backup.via };
}

/**
 * The volumes that hold data and are in no copy: how many, and what they hold in bytes, which is known only when every one of them says
 * its size (a size not read yet is not zero, and a sum that skips it would be too small to be told). An anonymous volume is what a
 * container made without being asked, and one of no bytes holds nothing to lose.
 */
export function volumesWithoutCopy(volumes: readonly { backup?: DockerVolume["backup"] | undefined; size_bytes?: number | null | undefined; anonymous?: boolean | undefined }[]): { count: number; bytes: number | null } {
  let count = 0;
  let bytes: number | null = 0;
  for (const volume of volumes) {
    if (volumeCopyOf(volume).state !== "none" || volume.anonymous === true) continue;
    const size = volume.size_bytes ?? null;
    if (size === 0) continue;
    count += 1;
    bytes = size === null || bytes === null ? null : bytes + size;
  }
  return { count, bytes };
}

/** A network's menu. Docker's own networks offer nothing: they cannot be removed and the menu does not pretend. */
export function networkVerbs(network: Pick<DockerNetwork, "can" | "owner" | "builtin">): Verbs {
  if (network.builtin) return { direct: [], viaOwner: false };
  return { direct: network.can.remove ? ["remove"] : [], viaOwner: throughOwner(network.can, network.owner) };
}

/**
 * A stack's menu: it is started, stopped and restarted as a whole where the server says its owner allows it. Stop and restart are of
 * a stack with something running; start is of one that has something stopped. What an application owns is the application's.
 */
export function stackVerbs(stack: Pick<DockerStack, "owner" | "running" | "total"> & { can?: DockerCan | undefined }): Verbs {
  const can = stack.can;
  if (can === undefined) return { direct: [], viaOwner: false };
  const direct: Verb[] = [];
  if (stack.running > 0 && can.restart) direct.push("restart");
  if (stack.running > 0 && can.stop) direct.push("stop");
  if (stack.running < stack.total && can.start) direct.push("start");
  return { direct, viaOwner: throughOwner(can, stack.owner) };
}

/** An owner as the kit draws it: a kind the console does not know is nobody's, in words. */
export function ownerOf(owner: DockerOwner): { kind: OwnerKind; subject: string } {
  if (owner.kind === "app" || owner.kind === "database" || owner.kind === "noust") return { kind: owner.kind, subject: owner.subject };
  return { kind: "nobody", subject: "" };
}

/** Where an owner leads: an application's page, the database engines for an engine's; Noust's own and nobody's lead nowhere. */
export function ownerTarget(owner: DockerOwner): OwnerTarget | undefined {
  if (owner.kind === "app") return { to: "/apps/$domain", params: { domain: owner.subject } };
  if (owner.kind === "database") return { to: "/databases/engines" };
  return undefined;
}

/**
 * How an image's version is marked. What an application keeps to go back to is that first, never a version to take; an image with
 * no name is untagged; what was built on this machine, by Noust or by another hand, is built here, as the server says where the image
 * comes from (`source`: never worked out from the registry it lacks); and the rest is what the last look found. A look that was never
 * made, and an image whose origin nobody knows, are "not looked at yet", which is a state of its own and not a missing value, because
 * looking is a button. A node of 4.2 does not say where its images come from: its images are read as the look it made says.
 */
export function versionKindOf(image: Pick<DockerImage, "way_back" | "dangling"> & { source?: DockerImage["source"] | undefined; update?: DockerImage["update"] | undefined }): VersionKind {
  if (image.way_back) return "keeps";
  if (image.dangling) return "untagged";
  if (image.source === "built_noust" || image.source === "built_other") return "builtHere";
  const update = image.update;
  if (update === null || update === undefined) return "notLooked";
  switch (update.state) {
    case "newer":
      return update.change === "major" ? "major" : "newer";
    case "current":
      return "current";
    case "unknown":
      return "unknown";
    default:
      return "notLooked";
  }
}

/** The reasons there is no reading of a container's use, as the server says them (the same four as its own page). */
export type UsageReason = "not_running" | "restarting" | "too_young" | "no_reading";

/** The reason a container has no reading, as one of the four words; any other word is "no reading", which is true of every one. */
export function usageReason(reason: string): UsageReason {
  return reason === "not_running" || reason === "restarting" || reason === "too_young" ? reason : "no_reading";
}

/**
 * The reading of every container, by id and by name, so a list joins each row to its own: by the id when the list has the long one and
 * by the name when it has a short one. A container called `stats` is read by its id: its name is a route of the API, not of the reading.
 */
export function usageIndex(items: readonly DockerContainerUsage[] | undefined): ReadonlyMap<string, DockerUsage> {
  const index = new Map<string, DockerUsage>();
  for (const item of items ?? []) {
    index.set(item.id, item.usage);
    // A name never takes the place of an id that is already there.
    if (!index.has(item.name)) index.set(item.name, item.usage);
  }
  return index;
}

/** The reading of one container of the list, or undefined where there is none (not read yet, or a server that has no such reading). */
export function usageOf(index: ReadonlyMap<string, DockerUsage>, container: Pick<DockerContainer, "id" | "name">): DockerUsage | undefined {
  return index.get(container.id) ?? index.get(container.name);
}
