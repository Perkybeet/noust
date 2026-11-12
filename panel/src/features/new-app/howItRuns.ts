/**
 * How an application runs, on the new-application wizard's side: directly on the server (a service of
 * the system) or in a container. The decision is the server's (`ways` of `POST /api/apps/inspect`,
 * `noust.deployers.how_it_runs`); this only turns the card that was chosen into the type and the
 * container settings a request carries, and says whether the wizard has anything to ask. Pure, so it
 * is tested without rendering a step.
 *
 * Against a server that says nothing of how it runs (a node of 4.2 under a 4.3 central) there are no
 * `ways`, and every function here answers what the wizard has always done.
 */

import type { Inspection, ReviewForm } from "./wizard";

export type Ways = NonNullable<Inspection["ways"]>;
export type Way = Ways["server"];
export type WayForm = Ways["proposed"];

/** The types that run in containers: a container Noust writes the Compose file of, and a stack the repository describes. */
const CONTAINER_TYPES: ReadonlySet<string> = new Set(["container", "docker-compose"]);

/** The form a type runs in: every type but those two is a service of the system. */
export function formOfType(appType: string): WayForm {
  return CONTAINER_TYPES.has(appType) ? "container" : "server";
}

/** Whether there is something to choose: both ways can be. */
export function askedHowItRuns(inspection: Inspection): boolean {
  return inspection.ways?.choice === true;
}

/** Which form a type is, for the card that shows it chosen: a type the server does not name is judged by what it is. */
export function chosenForm(ways: Ways | null, appType: string): WayForm {
  if (ways !== null && ways.container.app_type === appType) return "container";
  if (ways !== null && ways.server.app_type === appType) return "server";
  return formOfType(appType);
}

/**
 * The review with the way that was chosen: its type, and for a container how it is built (Railpack
 * where the repository has no Dockerfile). A form that cannot be is not taken.
 */
export function withForm(form: ReviewForm, ways: Ways, to: WayForm): ReviewForm {
  const way = ways[to];
  if (way.status !== "yes" || way.app_type === null || way.app_type === undefined) return form;
  const method = to === "container" && (way.via === "railpack" || way.via === "dockerfile") ? way.via : null;
  return {
    ...form,
    appType: way.app_type,
    container: method !== null && form.container.method !== "image" ? { ...form.container, method } : form.container,
  };
}

/**
 * The first review of an inspection: what Noust proposes. The type is the one detection settled on, as it always was,
 * unless that is not the form proposed (this machine cannot run it and can run the other), and a container is built
 * the way the server says (Railpack where the repository has no Dockerfile).
 */
export function startedWithWay(form: ReviewForm, inspection: Inspection): ReviewForm {
  const ways = inspection.ways ?? null;
  return ways === null ? form : withForm(form, ways, ways.proposed);
}

/**
 * What stands in the way of a way and can be fixed in the source: the Dockerfile or Railpack a container is built with.
 * What the machine lacks (Docker, a web server) is the machine's state, which its own pages say, and is not repeated on
 * every deployment's summary. Null when the way can be, or when the reason is the machine's.
 */
export function reasonInTheSource(way: Way): NonNullable<Way["reason"]> | null {
  const reason = way.reason ?? null;
  return way.status === "no" && reason?.code.startsWith("how_it_runs.") === true ? reason : null;
}

/**
 * Whether a type picked before (by hand, or by a card) is still one of this inspection's: a type
 * detected, or the type of either way. Read again after another branch or a fix, the choice stays.
 */
export function keepsType(inspection: Inspection, appType: string): boolean {
  const ways = inspection.ways ?? null;
  return inspection.detected_types.includes(appType) || ways?.server.app_type === appType || ways?.container.app_type === appType;
}
