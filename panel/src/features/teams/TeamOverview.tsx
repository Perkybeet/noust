import { CommandHint } from "../../components/page/CommandHint";
import { KeyValueList } from "../../components/page/KeyValueList";
import type { KeyValueItem } from "../../components/page/KeyValueList";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Section, Sections } from "../../components/page/Section";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { EmptyState } from "../../components/ui/EmptyState";
import { ICONS } from "../../components/ui/icons";
import { Notice } from "../../components/ui/Notice";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import { heldBy } from "./organization";
import { ProjectsTable } from "./ProjectsTable";
import { teamRoleDescription, teamRoleLabel } from "./roles";
import { isTeamRole } from "../../api/queries/teams";
import { useTeam } from "./TeamLayout";
import { useOrganization } from "./useOrganization";
import { useProjectActions } from "./useProjectActions";
import { useResources } from "./useResources";

/**
 * What a team is and holds: its facts, what it says about isolation (a team separates who sees
 * and who may act, not where code runs), and its projects, with "New project" for whoever
 * governs it.
 */
export function TeamOverview() {
  const t = useT();
  const team = useTeam();
  const org = useOrganization();
  const resources = useResources();
  const actions = useProjectActions(resources);
  const projects = org.projects.filter((project) => project.team === team.slug);
  const governs = org.can("teams.manage", team.slug);
  const { apps, sites, databases, schedules } = heldBy(team.resources);

  const prose = { mono: false, copy: false } as const;
  const items: KeyValueItem[] = [
    { label: t("teams.overview.shortName"), value: team.slug },
    {
      label: t("teams.overview.yourRole"),
      value: team.role === null || team.role === undefined ? t("teams.team.notAMember") : teamRoleLabel(t, team.role),
      ...prose,
      ...(isTeamRole(team.role) ? { hint: teamRoleDescription(t, team.role) } : {}),
    },
    {
      label: t("teams.overview.members"),
      value: (
        <TextLink to="/teams/$team/members" params={{ team: team.slug }}>
          {t("teams.overview.membersCount", { count: team.members })}
        </TextLink>
      ),
      ...prose,
    },
    { label: t("teams.overview.created"), value: <RelativeTime value={team.created_at} />, ...prose },
    ...(team.created_by !== null && team.created_by !== undefined ? [{ label: t("teams.overview.createdBy"), value: team.created_by }] : []),
    {
      label: t("teams.overview.holds"),
      value: t("teams.overview.holdsValue", { apps, sites, databases, schedules }),
      ...prose,
    },
  ];

  const Add = ICONS.add;
  return (
    <Sections>
      <Notice title={t("teams.list.boundaryTitle")}>{t("teams.list.boundary")}</Notice>
      <Card padding="none">
        <KeyValueList items={items} className="px-5 py-1" />
      </Card>
      <Section
        title={t("teams.overview.projects")}
        description={t("teams.overview.projectsDescription")}
        {...(governs
          ? {
              actions: (
                <Button icon={<Add aria-hidden="true" />} onClick={actions.create}>
                  {t("teams.projects.new")}
                </Button>
              ),
            }
          : {})}
      >
        <ProjectsTable
          projects={projects}
          labels={org.labels}
          resources={resources}
          showTeam={false}
          caption={t("teams.overview.projectsCaption", { name: team.name })}
          loading={org.loading}
          rowActions={actions.rowMenu}
          empty={<EmptyState variant="inline" title={t("teams.overview.noProjects")} />}
        />
        <TextLink to="/projects" search={{ team: team.slug }} size="ui" className="self-start">
          {t("teams.overview.allProjects")}
        </TextLink>
      </Section>
      <CommandHint command={`noust team members ${team.slug}`} label={t("teams.fromTerminal")} />
      {actions.dialogs}
    </Sections>
  );
}
