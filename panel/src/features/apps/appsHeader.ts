import type { PageHeaderProps } from "../../app/PageHeader";
import type { T } from "../../i18n";

/**
 * The header of the list of applications: the title and the one sentence, and the view's own actions.
 * Where the page is in the console is the top bar's, and the server is named there and in the second
 * column, never above a title.
 */
export function appsHeader(t: T, view: Pick<PageHeaderProps, "primaryAction" | "secondaryActions"> = {}): PageHeaderProps {
  return { title: t("apps.page.title"), description: t("apps.page.description"), ...view };
}
