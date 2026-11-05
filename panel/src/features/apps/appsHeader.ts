import type { PageHeaderProps } from "../../app/PageHeader";
import type { T } from "../../i18n";
import type { AppsView } from "./filters";

/**
 * The header of the list of applications: the title and the view's own actions.
 * Where the page is in the console is the top bar's, and the server is named there and in the second
 * column, never above a title.
 */
export function appsHeader(t: T, view: Pick<PageHeaderProps, "primaryAction" | "secondaryActions"> = {}, shown?: AppsView): PageHeaderProps {
  const title = shown === "projects" ? t("apps.views.projectsTitle") : shown === "states" ? t("apps.views.statesTitle") : shown === "deployments" ? t("apps.views.deploymentsTitle") : t("apps.page.title");
  return { title, ...view };
}
