import type { ReactNode } from "react";

import type { Project } from "../../api/queries/teams";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { Mono } from "../../components/ui/Mono";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import type { Labels } from "./organization";
import { projectPath } from "./organization";
import type { Resources } from "./useResources";

export interface ProjectsTableProps {
  projects: readonly Project[];
  labels: Labels;
  resources: Pick<Resources, "counts" | "known">;
  /** Says which team each project is in: several teams are in view at once. */
  showTeam: boolean;
  caption: string;
  loading?: boolean;
  skeletonRows?: number;
  rowActions?: (project: Project) => ReactNode;
  empty?: ReactNode;
}

/**
 * The projects of a team or of every team, identity first: the name (which opens the project)
 * with its short name under it, the team when several are in view, its environments and what
 * it holds. A count is drawn only once every list was read: a zero that is really "not read
 * yet" would say a project is empty when it is not.
 */
export function ProjectsTable({ projects, labels, resources, showTeam, caption, loading = false, skeletonRows = 3, rowActions, empty }: ProjectsTableProps) {
  const t = useT();
  const count = (kind: keyof Resources["counts"], project: Project): number | null => {
    if (!resources.known) return null;
    return resources.counts[kind].get(`${project.team}/${project.slug}`) ?? 0;
  };
  const number = (kind: keyof Resources["counts"]) => (project: Project): ReactNode => {
    const value = count(kind, project);
    if (value === null) return <EmptyCell reason={t("teams.projects.notRead")} />;
    return value;
  };

  const columns: Column<Project>[] = [
    {
      id: "project",
      header: t("teams.projects.columnProject"),
      card: "title",
      sortValue: (project) => project.name,
      cell: (project) => (
        <span className="flex min-w-0 flex-col">
          <TextLink {...projectPath(project)} title={project.name} variant="row" className="-mx-1 block max-w-full truncate px-1 py-0.5">
            {project.name}
          </TextLink>
          <Mono tone="faint" truncate>
            {project.slug}
          </Mono>
        </span>
      ),
    },
    ...(showTeam
      ? ([
          {
            id: "team",
            header: t("teams.projects.columnTeam"),
            width: "w-44",
            hideBelow: "sm",
            sortValue: (project) => labels.team(project.team),
            cell: (project) => <span className="text-fg-muted">{labels.team(project.team)}</span>,
          },
        ] satisfies Column<Project>[])
      : []),
    {
      id: "environments",
      header: t("teams.projects.columnEnvironments"),
      width: "w-56",
      hideBelow: "sm",
      cell: (project) => <span className="truncate text-fg-muted">{(project.environments ?? []).map((slug) => labels.environment(project.team, project.slug, slug)).join(", ")}</span>,
    },
    {
      id: "apps",
      header: t("teams.projects.columnApps"),
      align: "end",
      mono: true,
      width: "w-28",
      sortValue: (project) => count("apps", project),
      cell: number("apps"),
    },
    {
      id: "databases",
      header: t("teams.projects.columnDatabases"),
      align: "end",
      mono: true,
      width: "w-28",
      hideBelow: "md",
      sortValue: (project) => count("databases", project),
      cell: number("databases"),
    },
    {
      id: "sites",
      header: t("teams.projects.columnSites"),
      align: "end",
      mono: true,
      width: "w-24",
      hideBelow: "md",
      sortValue: (project) => count("sites", project),
      cell: number("sites"),
    },
  ];

  return (
    <DataTable
      columns={columns}
      rows={projects}
      getRowId={(project) => `${project.team}/${project.slug}`}
      caption={caption}
      loading={loading}
      skeletonRows={skeletonRows}
      defaultSort={{ column: "project", direction: "ascending" }}
      pagination="projects"
      {...(rowActions !== undefined ? { rowActions } : {})}
      {...(empty !== undefined ? { empty } : {})}
    />
  );
}
