import { Link, useNavigate } from "@tanstack/react-router";
import { FolderKanban } from "lucide-react";
import { useMemo } from "react";

import type { ProjectEnvironment } from "../../api/queries/teams";
import { CommandHint } from "../../components/page/CommandHint";
import { DetailPage } from "../../components/page/DetailPage";
import { ErrorBlock } from "../../components/page/QueryState";
import { LoadingRegion } from "../../components/page/LoadingRegion";
import { Section } from "../../components/page/Section";
import { SegmentedControl } from "../../components/page/SegmentedControl";
import { Badge } from "../../components/ui/Badge";
import { Button, buttonClassName } from "../../components/ui/Button";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { EmptyState } from "../../components/ui/EmptyState";
import { ICONS } from "../../components/ui/icons";
import { Mono } from "../../components/ui/Mono";
import { Skeleton } from "../../components/ui/Skeleton";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import { useNode } from "../../nodes/useNode";
import { AppRowActions } from "../apps/AppRowActions";
import { AppsTable } from "../apps/AppsTable";
import type { Deployment } from "../apps/data";
import { engineName } from "../databases/engines";
import { SiteState, Tls } from "../domains/SitesPage";
import { filterByOwner } from "./organization";
import type { ProjectSearch } from "./search";
import { useEnvironmentActions } from "./useEnvironmentActions";
import { useOrganization } from "./useOrganization";
import { useProjectActions } from "./useProjectActions";
import { useResources } from "./useResources";
import { SharedVariables } from "../variables/SharedVariables";

const NO_DEPLOYS: ReadonlyMap<string, Deployment> = new Map();

/** Production first, as every project has it; the rest in the order the server lists them. */
function inOrder(environments: readonly ProjectEnvironment[]): ProjectEnvironment[] {
  return [...environments].sort((a, b) => Number(b.slug === "production") - Number(a.slug === "production"));
}

export interface ProjectPageProps {
  team: string;
  project: string;
  search: ProjectSearch;
  onSearchChange: (search: ProjectSearch, options?: { replace?: boolean }) => void;
}

/**
 * One project, as a T2 page: its name and team, "New application" as the one primary action,
 * and the project's environments as a segmented control (production first), each showing what
 * lives in it grouped by kind: applications, databases, sites. Creating an environment is two
 * clicks and always one place, never behind a single-environment shortcut; renaming and deleting
 * are one menu away, deleting guarded where it would be refused (the last environment, anything
 * that holds something).
 */
export function ProjectPage({ team, project: slug, search, onSearchChange }: ProjectPageProps) {
  const t = useT();
  const { node } = useNode();
  const navigate = useNavigate();
  const org = useOrganization();
  const resources = useResources();

  const project = org.projects.find((candidate) => candidate.team === team && candidate.slug === slug);
  const environments = useMemo(() => inOrder(org.environments.filter((environment) => environment.team === team && environment.project === slug)), [org.environments, team, slug]);
  const current = environments.find((environment) => environment.slug === search.env) ?? environments[0];
  const governs = org.can("teams.manage", team);
  const canCreateApps = org.can("apps.manage", team);

  const projectActions = useProjectActions(resources, {
    afterDelete: () => {
      void navigate({ to: "/projects" });
    },
  });
  const environmentActions = useEnvironmentActions(project, resources, {
    governs,
    onCreated: (created) => onSearchChange({ env: created.slug }),
    afterDelete: () => onSearchChange({}),
  });

  const breadcrumbs = [{ label: t("teams.projects.title"), to: "/projects" }];

  if (!org.available || org.loading) {
    const header = { title: slug, mono: true, breadcrumbs, server: node };
    // The strip of environments has its place from the first frame, so nothing is pushed down when it arrives.
    return (
      <DetailPage header={header} {...(org.loading || org.error === null || org.error === undefined ? { tabs: <LoadingRegion label={t("teams.project.loadingTabs", { name: slug })}><Skeleton className="h-control-md w-64 rounded-control" /></LoadingRegion> } : {})}>
        {org.loading || org.error === null || org.error === undefined ? (
          <LoadingRegion label={t("teams.project.loading", { name: slug })}>
            <Skeleton className="h-48 w-full rounded-card" />
          </LoadingRegion>
        ) : (
          <ErrorBlock error={org.error} title={t("teams.couldNotLoad")} onRetry={org.reload} />
        )}
      </DetailPage>
    );
  }

  if (project === undefined) {
    return (
      <DetailPage header={{ title: slug, mono: true, breadcrumbs, server: node }}>
        <EmptyState
          variant="firstUse"
          icon={<FolderKanban />}
          title={t("teams.project.notFoundTitle")}
          description={t("teams.project.notFoundDescription", { project: slug, team })}
          action={
            <Link to="/projects" className={buttonClassName("secondary")}>
              {t("teams.project.allProjects")}
            </Link>
          }
          command="noust project list"
        />
      </DetailPage>
    );
  }

  const here = current === undefined ? { team, project: slug } : { team, project: slug, environment: current.slug };
  const apps = filterByOwner(resources.apps, here);
  const databases = filterByOwner(resources.databases, here);
  const sites = filterByOwner(resources.sites, here);
  const empty = resources.known && apps.length + databases.length + sites.length === 0;
  const Add = ICONS.add;

  const newApp = (variant: "primary" | "secondary") => (
    <Link
      to="/apps/new"
      search={{ team, project: slug, ...(current !== undefined ? { environment: current.slug } : {}) } as never}
      className={buttonClassName(variant)}
    >
      <Add aria-hidden="true" />
      {t("teams.project.newApplication")}
    </Link>
  );

  const databaseColumns: Column<(typeof databases)[number]>[] = [
    {
      id: "name",
      header: t("common.words.name"),
      card: "title",
      sortValue: (database) => database.name,
      cell: (database) => (
        <TextLink to="/databases/$engine/$name" params={{ engine: database.engine, name: database.name }} translate="no" variant="row" className="-mx-1 block max-w-full truncate px-1 py-0.5">
          {database.name}
        </TextLink>
      ),
    },
    { id: "engine", header: t("teams.project.columnEngine"), width: "w-52", sortValue: (database) => database.engine, cell: (database) => <span className="text-fg-muted">{engineName(database.engine)}</span> },
    {
      id: "app",
      header: t("teams.project.columnApp"),
      hideBelow: "md",
      sortValue: (database) => database.app ?? "",
      cell: (database) => (database.app ? <Mono tone="muted">{database.app}</Mono> : <EmptyCell reason={t("teams.project.noApp")} />),
    },
  ];

  const siteColumns: Column<(typeof sites)[number]>[] = [
    {
      id: "name",
      header: t("common.words.name"),
      card: "title",
      sortValue: (site) => site.name,
      cell: (site) => (
        <TextLink to="/domains/sites/$site" params={{ site: site.name }} translate="no" variant="row" className="-mx-1 block max-w-full truncate px-1 py-0.5">
          {site.name}
        </TextLink>
      ),
    },
    { id: "state", header: t("teams.project.columnState"), width: "w-32", card: "status", sortValue: (site) => (site.enabled ? 1 : 0), cell: (site) => <SiteState enabled={site.enabled} /> },
    { id: "tls", header: t("teams.project.columnServes"), width: "w-32", sortValue: (site) => (site.has_ssl ? 0 : 1), cell: (site) => <Tls secure={site.has_ssl} /> },
  ];

  const header = {
    title: project.name,
    breadcrumbs,
    server: node,
    meta: (
      <>
        {org.teams.length > 1 || org.scope.showTeam ? <span>{org.labels.team(team)}</span> : null}
        <Mono tone="muted">{slug}</Mono>
        <span>{t("teams.project.environmentsFact", { count: environments.length })}</span>
      </>
    ),
    description: t("teams.project.description"),
    ...(canCreateApps ? { primaryAction: newApp("primary") } : {}),
    ...(governs ? { overflow: projectActions.menuItems(project) } : {}),
  };

  const toolbar =
    current === undefined ? undefined : (
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <SegmentedControl
          label={t("teams.project.environmentLabel")}
          value={current.slug}
          options={environments.map((environment) => ({ value: environment.slug, label: environment.name }))}
          onValueChange={(value) => onSearchChange({ env: value })}
        />
        {governs ? (
          <Button size="sm" icon={<Add aria-hidden="true" />} onClick={environmentActions.create}>
            {t("teams.environments.new")}
          </Button>
        ) : null}
        {environmentActions.menu(current, environments)}
      </div>
    );

  return (
    <DetailPage header={header} {...(toolbar !== undefined ? { tabs: toolbar } : {})}>
      <div className="flex min-w-0 flex-col gap-8">
        {resources.loading ? (
          // What lives here arrives from three lists, in any order: nothing is drawn until all
          // have, or a section would appear above one already on screen and push it down.
          <LoadingRegion label={t("teams.project.loadingResources", { name: current?.name ?? slug })}>
            <Skeleton className="h-48 w-full rounded-card" />
          </LoadingRegion>
        ) : empty && current !== undefined ? (
          <EmptyState
            variant="firstUse"
            icon={<FolderKanban />}
            title={t("teams.project.emptyTitle", { name: current.name })}
            description={t("teams.project.emptyDescription")}
            {...(canCreateApps ? { action: newApp("secondary") } : {})}
            command={`noust create --domain app.example.com --source <repository> --team ${team} --project ${slug} --environment ${current.slug}`}
          />
        ) : (
          <>
            <Section title={t("teams.project.applications")} badge={<Badge>{apps.length}</Badge>}>
              {apps.length === 0 ? (
                <EmptyState variant="inline" title={t("teams.project.noApps")} />
              ) : (
                <AppsTable apps={apps} deploys={NO_DEPLOYS} metrics={undefined} caption={t("teams.project.appsCaption", { name: current?.name ?? slug })} rowActions={(app) => <AppRowActions app={app} />} />
              )}
            </Section>
            {databases.length > 0 ? (
              <Section title={t("teams.project.databases")} badge={<Badge>{databases.length}</Badge>}>
                <DataTable columns={databaseColumns} rows={databases} getRowId={(database) => `${database.engine}:${database.name}`} caption={t("teams.project.databasesCaption", { name: current?.name ?? slug })} mobile="cards" pagination="databases" defaultSort={{ column: "name", direction: "ascending" }} />
              </Section>
            ) : null}
            {sites.length > 0 ? (
              <Section title={t("teams.project.sites")} badge={<Badge>{sites.length}</Badge>}>
                <DataTable columns={siteColumns} rows={sites} getRowId={(site) => `${site.webserver}:${site.name}`} caption={t("teams.project.sitesCaption", { name: current?.name ?? slug })} mobile="cards" pagination="sites" defaultSort={{ column: "name", direction: "ascending" }} />
              </Section>
            ) : null}
          </>
        )}
        {/* What the project's applications share, defined once and referred to: after what lives here. */}
        {resources.loading ? null : (
          <>
            <SharedVariables scope={{ kind: "project", team, project: slug }} name={project.name} canManage={canCreateApps} command={false} inlineEmpty />
            {current !== undefined ? (
              <SharedVariables scope={{ kind: "environment", team, project: slug, environment: current.slug }} name={current.name} canManage={canCreateApps} command={false} inlineEmpty />
            ) : null}
          </>
        )}
        {/* Drawn with what it is about, not before: under a body of unknown height it would only be pushed down. */}
        {resources.loading ? null : <CommandHint command={`noust list --team ${team} --project ${slug}`} label={t("teams.fromTerminal")} />}
      </div>
      {environmentActions.dialogs}
      {projectActions.dialogs}
    </DetailPage>
  );
}
