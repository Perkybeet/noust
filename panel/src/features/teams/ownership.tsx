/**
 * What a list that says where its rows live (applications, databases, sites) adds to itself:
 * the team the operator works in narrows it, a Project and an Environment filter narrow it
 * further, and two columns say where each row is. All of it appears only where there is more
 * than one place to tell apart (`describeScope`); a server nobody organised draws none of it.
 */

import { useMemo } from "react";
import type { ReactNode } from "react";

import { Badge } from "../../components/ui/Badge";
import type { Column } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { filterByOwner, optionsFor, ownerOf } from "./organization";
import type { Labels, OwnedRow, Scope } from "./organization";
import type { OwnerSearch } from "./search";
import { useOrganization } from "./useOrganization";

const ALL = "all";

export interface OwnerScope<Row extends OwnedRow> {
  /** The rows of the team in view, before the project and environment filters. */
  inTeam: Row[];
  /** The rows after every filter. */
  shown: Row[];
  /** A project or an environment filter is on. */
  filtered: boolean;
  /** Where rows live is still being read: the list keeps its placeholders until it is. */
  loading: boolean;
  /** Where rows live is worth showing: which columns and filters there are. */
  scope: Scope;
  labels: Labels;
  /** The Project and Environment selects, for the FilterBar's `filters`; null when there is nothing to choose. */
  filters: (search: OwnerSearch, onChange: (patch: OwnerSearch) => void) => ReactNode;
}

/**
 * The rows a list shows once the team in view and the project and environment filters are
 * applied, with the filters and what the columns need. Nothing is narrowed on a server that
 * answered no teams (an older node), so the list is what it always was.
 */
export function useOwnerScope<Row extends OwnedRow>(rows: readonly Row[], search: OwnerSearch): OwnerScope<Row> {
  const t = useT();
  const org = useOrganization();
  const active = org.available ? org.active : null;

  const inTeam = useMemo(() => (active === null ? [...rows] : filterByOwner(rows, { team: active })), [rows, active]);
  const project = org.available ? (search.project ?? null) : null;
  const environment = org.available ? (search.environment ?? null) : null;
  const shown = useMemo(() => filterByOwner(inTeam, { project, environment }), [inTeam, project, environment]);
  const { scope } = org;

  const filters: OwnerScope<Row>["filters"] = (current, onChange) => {
    if (!org.available || (!scope.showProject && !scope.showEnvironment)) return null;
    const projects = new Map(scope.projects.map((item) => [item.slug, item.name]));
    const environments = optionsFor(scope.environments, current.project !== undefined ? { project: current.project } : {});
    return (
      <>
        {scope.showProject ? (
          <Select
            aria-label={t("teams.filters.projectLabel")}
            value={current.project ?? ALL}
            onValueChange={(value) => {
              const next = value === ALL ? undefined : value;
              // An environment this project does not have would leave the list empty for no reason.
              const keep = current.environment !== undefined && (next === undefined || optionsFor(scope.environments, { project: next }).some((option) => option.slug === current.environment));
              onChange({ ...(next !== undefined ? { project: next } : {}), ...(keep && current.environment !== undefined ? { environment: current.environment } : {}) });
            }}
            options={[{ value: ALL, label: t("teams.filters.everyProject") }, ...[...projects].map(([slug, name]) => ({ value: slug, label: name }))]}
            className="min-w-36"
          />
        ) : null}
        {scope.showEnvironment ? (
          <Select
            aria-label={t("teams.filters.environmentLabel")}
            value={current.environment ?? ALL}
            onValueChange={(value) => {
              onChange({ ...(current.project !== undefined ? { project: current.project } : {}), ...(value !== ALL ? { environment: value } : {}) });
            }}
            options={[{ value: ALL, label: t("teams.filters.everyEnvironment") }, ...environments.map((option) => ({ value: option.slug, label: option.name }))]}
            className="min-w-36"
          />
        ) : null}
      </>
    );
  };

  return { inTeam, shown, filtered: project !== null || environment !== null, loading: org.loading, scope, labels: org.labels, filters };
}

/**
 * The two columns that say where a row lives: its project (with its team under it when several
 * teams are in view) and its environment. Empty where the list needs neither. A row nobody
 * owns (a site file or a database Noust never recorded) says so instead of leaving a gap.
 */
export function ownerColumns<Row extends OwnedRow>(t: T, scope: Scope, labels: Labels): Column<Row>[] {
  const columns: Column<Row>[] = [];
  if (scope.showProject || scope.showTeam) {
    columns.push({
      id: "project",
      header: t("teams.filters.columnProject"),
      width: "w-44",
      hideBelow: "md",
      card: "meta",
      sortValue: (row) => {
        const { team, project } = ownerOf(row);
        return team === null || project === null ? "" : labels.project(team, project);
      },
      cell: (row) => {
        const { team, project } = ownerOf(row);
        if (team === null || project === null) return <EmptyCell reason={t("teams.filters.unowned")} />;
        return (
          <span className="flex min-w-0 flex-col">
            <span className="truncate text-fg">{labels.project(team, project)}</span>
            {scope.showTeam ? <span className="truncate text-12 text-fg-muted">{labels.team(team)}</span> : null}
          </span>
        );
      },
    });
  }
  if (scope.showEnvironment) {
    columns.push({
      id: "environment",
      header: t("teams.filters.columnEnvironment"),
      width: "w-36",
      hideBelow: "md",
      card: "meta",
      sortValue: (row) => ownerOf(row).environment ?? "",
      cell: (row) => {
        const { team, project, environment } = ownerOf(row);
        if (team === null || project === null || environment === null) return <EmptyCell reason={t("teams.filters.unowned")} />;
        return <Badge>{labels.environment(team, project, environment)}</Badge>;
      },
    });
  }
  return columns;
}

/** A column of rows of one kind drawn in a table of rows that hold one: the table keeps its own row shape. */
export function inRows<Row, Outer>(column: Column<Row>, pick: (outer: Outer) => Row): Column<Outer> {
  const { sortValue, cell, ...rest } = column;
  return {
    ...rest,
    cell: (outer) => cell(pick(outer)),
    ...(sortValue !== undefined ? { sortValue: (outer: Outer) => sortValue(pick(outer)) } : {}),
  };
}
