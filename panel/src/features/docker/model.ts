/**
 * What the Docker zone decides from what the server says, apart from drawing it: the state a container is shown in, what a row's
 * menu offers (from the server's `can`, never from a rule of the console's own), where an owner leads and how an image's version
 * is marked. Plain functions over the API's types, so each rule is a test and no page repeats one.
 */

import type { DockerCan, DockerContainer, DockerImage, DockerNetwork, DockerOwner, DockerStack, DockerVolume } from "../../api/queries/docker";
import type { OwnerKind, OwnerTarget, VersionKind } from "../../components/page";
import type { Status } from "../../components/ui";

/** Why the document of a stack is not edited from the console, as the server says it (`docker.compose.why.*`). */
export const COMPOSE_WHY_CODES = ["repository", "recipe", "generated", "bring_first", "no_file", "not_compose"] as const;

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
export type Verb = "logs" | "console" | "restart" | "stop" | "start" | "remove" | "update" | "bring";

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
export function imageVerbs(image: Pick<DockerImage, "can" | "owner"> & { update?: DockerImage["update"] | undefined }): Verbs {
  const direct: Verb[] = [];
  const update = image.update;
  if (update?.state === "newer" && update.mode === "with_net" && update.apply) direct.push("update");
  if (update?.state === "newer" && update.mode === "bring_first" && typeof update.stack === "string" && update.stack !== "") direct.push("bring");
  if (image.can.remove) direct.push("remove");
  return { direct, viaOwner: throughOwner(image.can, image.owner) };
}

/** A volume's menu: remove (which asks for its name) where the server allows it. */
export function volumeVerbs(volume: Pick<DockerVolume, "can" | "owner">): Verbs {
  return { direct: volume.can.remove ? ["remove"] : [], viaOwner: throughOwner(volume.can, volume.owner) };
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
 * no name is untagged; and the rest is what the last look found. A look that was never made is "not looked at yet", which is a state
 * of its own and not a missing value, because looking is a button. An image with no registry to ask is built here, except Noust's own,
 * which is pulled and whose version is Noust's to say.
 */
export function versionKindOf(image: Pick<DockerImage, "way_back" | "dangling"> & { update?: DockerImage["update"] | undefined }): VersionKind {
  if (image.way_back) return "keeps";
  if (image.dangling) return "untagged";
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
      return (update.reference === null || update.reference === undefined || update.reference === "") && update.owner.kind !== "noust" ? "builtHere" : "notLooked";
  }
}
