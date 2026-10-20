import { FolderKanban } from "lucide-react";

import { area, strip, tab } from "../../app/navCore";

/** Projects and the Teams tab under it (D-5). The register is app/nav.ts; this is one group of it. */
export const PROJECTS_SCREENS = strip(
  area("projects", "/projects", "nav.projects.label", FolderKanban, {
    group: "runs",
    shortcut: ["g", "p"],
    goTo: "nav.projects.goTo",
    keywords: "nav.projects.keywords",
    when: "organised",
  }),
  (projects) => [tab(projects, "teams", "teams.list.title", { keywords: "nav.screens.teams.keywords" })],
);
