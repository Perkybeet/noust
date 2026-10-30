/**
 * The fleet's applications, read from `GET /api/views/apps` as pure functions so each rule is tested
 * without a page: the rows as the applications list draws them, and what the strip of servers says of
 * each (how many applications it runs and the worst state among them).
 */

import type { ViewEnvelope } from "../../api/queries/views";
import { appStatus } from "../../components/page/status";
import type { Status } from "../../components/ui/StatusPill";
import { isWell } from "../../components/ui/FleetCourses";
import type { FleetApp } from "../apps/AppsTable";

/**
 * Every application of every server, in the order the view gave them. A row without a domain (a server
 * older than the view's contract) is not an application anyone can open and is left out.
 */
export function fleetApps(view: Pick<ViewEnvelope, "items"> | undefined): FleetApp[] {
  return (view?.items ?? []).filter((item) => typeof item["domain"] === "string" && typeof item["node"] === "string").map((item) => item as unknown as FleetApp);
}

export interface ServerSegment {
  /** What names the server in the strip and in the address (`server`): the name the operator gave it. */
  name: string;
  count: number;
  /** The worst state among its applications, or null when all are well. */
  worst: Status | null;
}

/** How bad each state is, worst first: the one a server's segment tells. */
const ORDER: readonly Status[] = ["failed", "warning", "unknown", "deploying", "queued", "stopped"];

/** The servers of the view, this central first, each with how many applications it runs and the worst state among them. */
export function serverSegments(view: Pick<ViewEnvelope, "servers"> | undefined, apps: readonly FleetApp[], locale: Parameters<typeof appStatus>[1]): ServerSegment[] {
  return (view?.servers ?? []).map((server) => {
    const own = apps.filter((app) => app.node === server.name);
    const states = own.map((app) => appStatus(app.status, locale).state).filter((state) => !isWell(state));
    const worst = ORDER.find((state) => states.includes(state)) ?? null;
    return { name: server.name, count: own.length, worst };
  });
}

/** The projects and the environments the rows say they are in, alphabetically: a filter is offered only where there is more than one to tell apart. */
export function placesOf(apps: readonly FleetApp[]): { projects: string[]; environments: string[] } {
  const distinct = (pick: (app: FleetApp) => string | null | undefined): string[] =>
    [...new Set(apps.map(pick).filter((slug): slug is string => typeof slug === "string" && slug !== ""))].sort();
  return { projects: distinct((app) => app.project), environments: distinct((app) => app.environment) };
}
