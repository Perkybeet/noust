/**
 * How an application that exists runs, for its page: the words for each form, and where to look at what runs it.
 * The decision is the server's (`GET /api/apps/{domain}/runs`, `noust.deployers.how_it_runs`); this only says it.
 */

import type { AppRuns } from "../../api/queries/apps";

/**
 * Where the Docker zone lists an application's containers: the zone's Containers page, filtered by the application.
 * The zone is its own part of the console (Server, Docker), so the link is to the path it will have and not to a
 * route of this page's; a test holds the path.
 */
export const DOCKER_CONTAINERS_PATH = "/server/docker/containers";

/** The address of the Docker zone's Containers page with only this application's containers. */
export function dockerContainersHref(domain: string): string {
  return `${DOCKER_CONTAINERS_PATH}?${new URLSearchParams({ app: domain }).toString()}`;
}

/** The catalog keys that say how it runs: one line under the title, and the value of the row "Form". */
export interface RunsWords {
  line: "appPages.overview.howServerLine" | "appPages.overview.howDockerfileLine" | "appPages.overview.howRailpackLine" | "appPages.overview.howImageLine" | "appPages.overview.howStackLine";
  form: "appPages.overview.howServer" | "appPages.overview.howStatic" | "appPages.overview.howDockerfile" | "appPages.overview.howRailpack" | "appPages.overview.howImageForm" | "appPages.overview.howStack";
}

/** The words for a form; a static site is a service of the system that is served by the web server and runs no process. */
export function runsWords(runs: AppRuns, staticSite: boolean): RunsWords {
  switch (runs.via) {
    case "dockerfile":
      return { line: "appPages.overview.howDockerfileLine", form: "appPages.overview.howDockerfile" };
    case "railpack":
      return { line: "appPages.overview.howRailpackLine", form: "appPages.overview.howRailpack" };
    case "image":
      return { line: "appPages.overview.howImageLine", form: "appPages.overview.howImageForm" };
    case "compose":
      return { line: "appPages.overview.howStackLine", form: "appPages.overview.howStack" };
    default:
      return { line: "appPages.overview.howServerLine", form: staticSite ? "appPages.overview.howStatic" : "appPages.overview.howServer" };
  }
}
