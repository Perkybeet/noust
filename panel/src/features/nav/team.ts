import { hidden, tab, tabsOf } from "../../app/navCore";
import type { Strip, TabItem } from "../../app/navCore";

const team = hidden("team", "/teams/$team", "nav.teamTabs.overview.label", "projects.teams", { parent: "projects.teams", exact: true });

/** A team: under the Teams tab of Projects, with a strip of its own. The register is app/nav.ts; this is one group of it. */
export const TEAM_SCREENS: Strip = [
  team,
  tab(team, "members", "nav.teamTabs.members.label", { keywords: "nav.teamTabs.members.keywords" }),
  tab(team, "variables", "variables.nav.teamTab.label", { keywords: "variables.nav.teamTab.keywords" }),
  tab(team, "settings", "nav.teamTabs.settings.label", { keywords: "nav.teamTabs.settings.keywords" }),
];

/** A team's sections, each a URL under /teams/$team: what it is, who is in it, and its settings. */
export const TEAM_TABS: readonly TabItem[] = tabsOf(TEAM_SCREENS);
