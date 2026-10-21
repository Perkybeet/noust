import { LinkTabs } from "../../app/LinkTabs";
import { tabsOf, withCounts } from "../../app/navCore";
import type { PageHeaderProps } from "../../app/PageHeader";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { PROJECTS_SCREENS } from "../nav/projects";
import { useOrganization } from "./useOrganization";

/**
 * The two views of Projects as URLs (D-5): the projects, and the teams that decide who sees them and
 * who may act. Each has its count, kept in room while it is read so the strip does not change width
 * when it arrives. Read from the register, like every strip.
 */
export function ProjectsTabs() {
  const t = useT();
  const org = useOrganization();
  const tabs = withCounts(tabsOf(PROJECTS_SCREENS), {
    "/projects": org.available ? org.scope.projects.length : null,
    "/projects/teams": org.available ? org.teams.length : null,
  });
  return <LinkTabs label={t("nav.landmarks.projectSections")} tabs={tabs} />;
}

/**
 * The header both views share, so switching between them moves nothing but what is under the strip:
 * the same title and sentence, the server named on a fleet, and the view's own primary action.
 */
export function projectsHeader(t: T, node: string | null, primaryAction?: PageHeaderProps["primaryAction"]): PageHeaderProps {
  return {
    title: t("teams.projects.title"),
    description: t("teams.frame.description"),
    server: node,
    ...(primaryAction !== undefined ? { primaryAction } : {}),
  };
}
