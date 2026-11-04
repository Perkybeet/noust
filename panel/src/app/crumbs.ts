/**
 * The breadcrumbs of a page, generated from the register and never written by the page (docs/DESIGN.md,
 * NV-15): the entries above the page in the tree (`crumbsOf`), the page's own last, and the name of a
 * resource where the step is one (an application's domain, a database's name). A page of two levels or
 * more has them; an area's own first page does not, because it is where the bar says you are.
 *
 * Pure: the route's path with its parameters goes in, the steps come out. `Crumbs.tsx` draws them.
 */

import type { PlainKey } from "../i18n";
import { DESTS, EXCEPTION_CRUMBS, areaTarget, crumbsOf, dest } from "./nav";
import type { ConsolePath, Dest, Scope } from "./nav";

/** What names a step: a catalog key, or a parameter of the route (the resource's name, which is never translated). */
export type CrumbName = { key: PlainKey } | { param: string; prefix?: string };

export interface CrumbStep {
  id: string;
  name: CrumbName;
  /** Where the step leads, with its parameters not yet filled in. */
  to: ConsolePath;
  /** The step is a resource of its list (an application), whose siblings are the other resources. */
  resource: boolean;
  entry: Dest;
}

/** The last `$parameter` of a path pattern: the name of the resource that page is about. */
function lastParam(to: string): string | undefined {
  return [...to.matchAll(/\$(\w+)/g)].map((match) => match[1]).at(-1);
}

/** The entry a route is: its own, or the one an exception is opened from, and what names the exception. */
function entryOf(route: string): { entry: Dest; tail?: CrumbName } | undefined {
  const own = DESTS.find((candidate) => candidate.to === route);
  if (own !== undefined) return { entry: own };
  const exception = EXCEPTION_CRUMBS[route];
  return exception === undefined ? undefined : { entry: dest(exception.from), tail: exception.name };
}

/** The area an entry stands for in a chain: the fleet's face of an area is that area, so it is said once. */
function standIn(entry: Dest): Dest {
  return entry.faceOf === undefined ? entry : dest(entry.faceOf);
}

/**
 * The steps of the page at `route` (the matched route's path, `fullPath`, parameters included). `scope`
 * is whose pages these are: an area opens at its fleet face from the fleet's pages.
 */
export function crumbsFor(route: string, scope: Scope): CrumbStep[] {
  const path = route.length > 1 ? route.replace(/\/+$/, "") : route;
  const found = entryOf(path);
  if (found === undefined) return [];
  const steps: CrumbStep[] = [];
  for (const entry of [...crumbsOf(found.entry.id), found.entry].map(standIn)) {
    if (steps.at(-1)?.id === entry.id) continue;
    // A resource's own page (an application, a database) is a step named by what it is: its domain, its name.
    // It is the one hidden entry with parameters that is exact: its sections (Diagnose) are not resources.
    const param = lastParam(entry.to);
    const resource = entry.kind === "hidden" && entry.exact === true && param !== undefined;
    steps.push({
      id: entry.id,
      name: resource ? { param } : { key: entry.label },
      to: entry.kind === "area" ? areaTarget(entry, scope) : entry.to,
      resource,
      entry,
    });
  }
  if (found.tail !== undefined) steps.push({ id: `${found.entry.id}.page`, name: found.tail, to: path as ConsolePath, resource: false, entry: found.entry });
  // The first level alone is the area itself: the bar already says it.
  return steps.length >= 2 ? steps : [];
}
