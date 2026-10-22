import { X } from "lucide-react";
import { useEffect, useMemo } from "react";

import { CommandHint } from "../../components/page/CommandHint";
import { FilterBar } from "../../components/page/FilterBar";
import { ListPage } from "../../components/page/ListPage";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { ICONS } from "../../components/ui/icons";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";
import { useNode } from "../../nodes/useNode";
import { ProjectsTable } from "./ProjectsTable";
import { ProjectsTabs, projectsHeader } from "./ProjectsTabs";
import type { ProjectsSearch } from "./search";
import { useOrganization } from "./useOrganization";
import { useProjectActions } from "./useProjectActions";
import { useResources } from "./useResources";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";

const ALL = "all";

export interface ProjectsPageProps {
  search: ProjectsSearch;
  onSearchChange: (search: ProjectsSearch, options?: { replace?: boolean }) => void;
}

/**
 * Every project the operator works with, as a T1 list: the name first, the team when several are
 * in view, its environments and what it holds. Creating one is two clicks: "New project", then
 * its name; it opens on its page, with production ready. A viewer, who cannot create one, is not
 * offered the button.
 */
export function ProjectsPage({ search, onSearchChange }: ProjectsPageProps) {
  const t = useT();
  const { node } = useNode();
  const org = useOrganization();
  const resources = useResources();
  const actions = useProjectActions(resources);
  const { create } = actions;

  // Arriving with `?new` (the command palette's "New project"): the dialog opens, and the link is spent.
  const opening = search.new === true;
  useEffect(() => {
    if (!opening || !org.available) return;
    create();
    const { q, team } = search;
    onSearchChange({ ...(q ? { q } : {}), ...(team ? { team } : {}) }, { replace: true });
    // Once per arrival: what it reads is what it was opened with.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opening, org.available]);

  const all = org.scope.projects;
  const needle = (search.q ?? "").toLowerCase();
  const shown = useMemo(
    () =>
      all.filter((project) => {
        if (search.team !== undefined && project.team !== search.team) return false;
        return needle === "" || `${project.name} ${project.slug}`.toLowerCase().includes(needle);
      }),
    [all, search.team, needle],
  );
  const filtered = search.q !== undefined || search.team !== undefined;
  const clear = (): void => onSearchChange({});

  const Add = ICONS.add;
  const header = projectsHeader(
    t,
    node,
    actions.teams.length > 0 ? (
      <Button variant="primary" icon={<Add aria-hidden="true" />} onClick={actions.create}>
        {t("teams.projects.new")}
      </Button>
    ) : undefined,
  );
  const tabs = <ProjectsTabs />;

  if (org.error !== null && org.error !== undefined && !org.available) {
    return (
      <ListPage header={header} tabs={tabs}>
        <ErrorBlock error={org.error} title={t("teams.couldNotLoad")} onRetry={org.reload} />
      </ListPage>
    );
  }

  if (org.available && !org.loading && all.length === 0) {
    return (
      <ListPage header={header} tabs={tabs}>
        <EmptyState
          variant="firstUse"
          icon={<OBJECT_ICONS.project />}
          title={t("teams.projects.emptyTitle")}
          description={t("teams.projects.emptyDescription")}
          {...(actions.teams.length > 0
            ? {
                action: (
                  <Button icon={<Add aria-hidden="true" />} onClick={actions.create}>
                    {t("teams.projects.new")}
                  </Button>
                ),
              }
            : {})}
          command="noust project create 'Mobile app'"
        />
        {actions.dialogs}
      </ListPage>
    );
  }

  const count = !org.available ? "" : filtered ? t("teams.projects.countFiltered", { shown: shown.length, total: all.length }) : t("teams.projects.count", { count: all.length });
  return (
    <ListPage
      header={header}
      tabs={tabs}
      filters={
        <FilterBar
          label={t("teams.projects.filterLabel")}
          search={{
            value: search.q ?? "",
            onChange: (value) => {
              const next: ProjectsSearch = { ...(search.team !== undefined ? { team: search.team } : {}), ...(value !== "" ? { q: value } : {}) };
              onSearchChange(next, { replace: true });
            },
            label: t("teams.projects.searchLabel"),
            placeholder: t("teams.projects.searchPlaceholder"),
          }}
          {...(org.scope.showTeam
            ? {
                filters: (
                  <Select
                    aria-label={t("teams.projects.teamFilter")}
                    value={search.team ?? ALL}
                    onValueChange={(value) => {
                      const next: ProjectsSearch = { ...(search.q !== undefined ? { q: search.q } : {}), ...(value !== ALL ? { team: value } : {}) };
                      onSearchChange(next);
                    }}
                    options={[{ value: ALL, label: t("teams.projects.everyTeam") }, ...org.teams.map((team) => ({ value: team.slug, label: team.name }))]}
                    className="min-w-40"
                  />
                ),
              }
            : {})}
          count={count}
          {...(filtered
            ? {
                actions: (
                  <Button variant="ghost" icon={<X aria-hidden="true" />} onClick={clear}>
                    {t("teams.clearFilters")}
                  </Button>
                ),
              }
            : {})}
        />
      }
      {...(org.available ? { footer: <CommandHint command="noust project list" label={t("teams.fromTerminal")} /> } : {})}
    >
      <ProjectsTable
        projects={shown}
        labels={org.labels}
        resources={resources}
        showTeam={org.scope.showTeam}
        caption={filtered ? t("teams.projects.captionFiltered") : t("teams.projects.caption")}
        loading={org.loading}
        rowActions={actions.rowMenu}
        empty={
          <EmptyState
            variant="inline"
            title={t("teams.projects.noMatch")}
            action={
              <Button size="sm" variant="ghost" onClick={clear}>
                {t("teams.clearFilters")}
              </Button>
            }
          />
        }
      />
      {actions.dialogs}
    </ListPage>
  );
}
