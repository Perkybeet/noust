import { area, strip, tab } from "../../app/navCore";
import type { Strip } from "../../app/navCore";

/** The area of the applications: the first entry of its own strip. */
export const APPS_AREA = area("apps", "/apps", "nav.apps.label", {
  scope: "both",
  group: "runs",
  feature: "applications",
  fleetTo: "/fleet/apps",
  shortcut: ["g", "a"],
  goTo: "nav.apps.goTo",
  keywords: "nav.apps.keywords",
});

/**
 * Projects is the second view of the list, how a person who organised their applications filters
 * them (D-5): a tab of Applications and not an area of the bar, offered only to a server whose people
 * organised theirs. It keeps the address it always had.
 */
export const PROJECTS_TAB = tab(APPS_AREA, "projects", "nav.projects.label", {
  to: "/projects",
  exact: true,
  shortcut: ["g", "p"],
  goTo: "nav.projects.goTo",
  keywords: "nav.projects.keywords",
  when: "organised",
  feature: "applications",
});

/**
 * The views of Applications the list draws as tabs. Kept apart from the register's group
 * (nav/apps.ts, which also names the wizard) so that a page drawing the strip does not ask for the
 * texts of the screens it does not draw.
 */
export const APPS_TABS: Strip = strip(APPS_AREA, () => [PROJECTS_TAB]);
