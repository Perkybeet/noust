import { lazyWithPreload } from "../../lib/lazyWithPreload";

// The sections of an application, a database, a service, a team and a server read the details of each: one chunk, drawn
// only where one is entered. It is read ahead (`preloadResourceColumns`) by whoever sends the operator to a resource that was
// just made, because the column would otherwise be its loading shapes beside a page that is already drawn.
const AppColumn = lazyWithPreload<{ domain: string }>(() => import("./resourceColumns").then((module) => ({ default: module.AppColumn })));
const DatabaseColumn = lazyWithPreload<{ engine: string; name: string }>(() => import("./resourceColumns").then((module) => ({ default: module.DatabaseColumn })));
const ServiceColumn = lazyWithPreload<{ name: string }>(() => import("./resourceColumns").then((module) => ({ default: module.ServiceColumn })));
const TeamColumn = lazyWithPreload<{ team: string }>(() => import("./resourceColumns").then((module) => ({ default: module.TeamColumn })));
const ServerResourceColumn = lazyWithPreload<object>(() => import("./resourceColumns").then((module) => ({ default: module.ServerResourceColumn })));

export { AppColumn, DatabaseColumn, ServerResourceColumn, ServiceColumn, TeamColumn };

/**
 * Reads the chunk of the resources' columns, so that the next one drawn is drawn in its first frame. The five columns are
 * one chunk: each is asked for here because each keeps its own settled state, which is what lets it skip the fallback.
 *
 * @returns Resolves when every column can be drawn at once; rejects if the chunk could not be read (the column then loads
 *   when it is drawn, as it always did).
 */
export function preloadResourceColumns(): Promise<void> {
  return Promise.all([AppColumn, DatabaseColumn, ServiceColumn, TeamColumn, ServerResourceColumn].map((column) => column.preload())).then(() => undefined);
}
