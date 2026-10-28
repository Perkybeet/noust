import { strip, tab } from "../../app/navCore";
import { PROJECTS_TAB } from "./appsTabs";

/**
 * Projects, the second view of Applications, and the Teams tab under it (D-5). The register is
 * app/nav.ts; this is one group of it.
 */
export const PROJECTS_SCREENS = strip(PROJECTS_TAB, (projects) => [tab(projects, "teams", "teams.list.title", { keywords: "nav.screens.teams.keywords" })]);
