import type { PageHeaderProps } from "../../app/PageHeader";
import type { T } from "../../i18n";

/**
 * The header both sections share (Projects and Teams, which are the column's), so switching between them moves
 * nothing but what is under it: the same title and sentence and the section's own primary action.
 */
export function projectsHeader(t: T, primaryAction?: PageHeaderProps["primaryAction"]): PageHeaderProps {
  return {
    title: t("teams.projects.title"),
    description: t("teams.frame.description"),
    ...(primaryAction !== undefined ? { primaryAction } : {}),
  };
}
