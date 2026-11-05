import type { PlainKey } from "../../i18n";

/**
 * The fixed views of an area that is a single page: what its second column lists where the area has no
 * sections of its own. A view is the page with a search the address carries (`/logs?source=system`), so
 * it is shared, bookmarked and reached with Back like any other address; nothing is saved per person
 * (4.2 design, 3.9.5). The first view of a list is the page with no search, which is the area's own
 * address, and every other is a preset of the page's own filters, so a view is never a page that does
 * not exist yet: the area's page already reads each of them.
 *
 * Keyed by the area's id. An area whose pages are routes of their own (Domains, Alerts) has its sections
 * in the register and is not here.
 */
export interface AreaView {
  id: string;
  label: PlainKey;
  /** What the view sets in the address beside the page's own path; none for the page as it opens. */
  search?: Readonly<Record<string, string>>;
}

export const VIEWS: Readonly<Record<string, readonly AreaView[]>> = {
  // The applications (features/apps/filters.ts): all of them, grouped by project or by state, and the deployments of all.
  apps: [
    { id: "all", label: "nav.views.appsAll" },
    { id: "projects", label: "nav.views.appsProjects", search: { view: "projects" } },
    { id: "states", label: "nav.views.appsStates", search: { view: "states" } },
    { id: "deployments", label: "nav.views.appsDeployments", search: { view: "deployments" } },
  ],
  // Where the lines come from (features/logs/search.ts): the applications', or the system's journal.
  logs: [
    { id: "apps", label: "nav.views.logsApps" },
    { id: "system", label: "nav.views.logsSystem", search: { source: "system" } },
  ],
  // What happened (features/activity/search.ts): every change, the deployments, or who signed in.
  activity: [
    { id: "changes", label: "nav.views.activityChanges" },
    { id: "deployments", label: "nav.views.activityDeployments", search: { kind: "deployments" } },
    { id: "access", label: "nav.views.activityAccess", search: { kind: "access" } },
  ],
};
