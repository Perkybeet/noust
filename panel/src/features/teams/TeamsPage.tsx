import { useQueries, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { PanelTop, Users, X } from "lucide-react";
import { useEffect, useMemo } from "react";
import { useState } from "react";

import { createTeam, teamQuery } from "../../api/queries/teams";
import type { Team, TeamDetail } from "../../api/queries/teams";
import { announce } from "../../app/Announcer";
import { CommandHint } from "../../components/page/CommandHint";
import { FilterBar } from "../../components/page/FilterBar";
import { ListPage } from "../../components/page/ListPage";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { EmptyState } from "../../components/ui/EmptyState";
import { ICONS } from "../../components/ui/icons";
import { IconButton } from "../../components/ui/IconButton";
import { Menu, MenuItem } from "../../components/ui/Menu";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import { useNode } from "../../nodes/useNode";
import { reportActionError } from "../apps/useAppActions";
import { NameDialog } from "./NameDialog";
import { ProjectsTabs, projectsHeader } from "./ProjectsTabs";
import { heldBy } from "./organization";
import { refreshOrganization } from "./refresh";
import { teamRoleLabel } from "./roles";
import type { TeamsSearch } from "./search";
import { useOrganization } from "./useOrganization";

export interface TeamsPageProps {
  search: TeamsSearch;
  onSearchChange: (search: TeamsSearch, options?: { replace?: boolean }) => void;
}

/** What a team holds, summed: its applications, sites and databases. */
function held(detail: TeamDetail | undefined): number | null {
  if (detail === undefined) return null;
  const { apps, sites, databases } = heldBy(detail.resources);
  return apps + sites + databases;
}

/**
 * The teams the operator sees, as a T1 list: the team first, the operator's role in it, who is
 * in it and what it holds. A team is a boundary of who sees and who may act, and the page says
 * it is not one of execution. Only an administrator, who sees every team, creates one; anyone
 * else sees their own and no button to make another.
 */
export function TeamsPage({ search, onSearchChange }: TeamsPageProps) {
  const t = useT();
  const { node } = useNode();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const org = useOrganization();
  const [creating, setCreating] = useState(false);
  const mayCreate = org.available && org.seesAll && org.can("teams.manage");

  // Arriving with `?new` (the command palette's "New team") opens the dialog, and closing it spends the link.
  const asked = search.new === true && mayCreate;
  const openCreate = (): void => setCreating(true);
  const closeCreate = (next: boolean): void => {
    setCreating(next);
    if (!next && search.new === true) onSearchChange(search.q !== undefined ? { q: search.q } : {}, { replace: true });
  };
  const refused = search.new === true && org.available && !mayCreate;
  useEffect(() => {
    // Nobody may create a team here: the link is spent without opening anything.
    if (refused) onSearchChange(search.q !== undefined ? { q: search.q } : {}, { replace: true });
    // Once per arrival: what it reads is what it was opened with.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refused]);

  const details = useQueries({ queries: org.teams.map((team) => ({ ...teamQuery(team.slug), retry: false })) });
  const detailOf = (team: Team): TeamDetail | undefined => details[org.teams.indexOf(team)]?.data;

  const needle = (search.q ?? "").toLowerCase();
  const shown = useMemo(() => org.teams.filter((team) => needle === "" || `${team.name} ${team.slug}`.toLowerCase().includes(needle)), [org.teams, needle]);
  const filtered = search.q !== undefined;
  const clear = (): void => onSearchChange({});

  const columns: Column<Team>[] = [
    {
      id: "team",
      header: t("teams.list.columnTeam"),
      card: "title",
      sortValue: (team) => team.name,
      cell: (team) => (
        <span className="flex min-w-0 flex-col">
          <TextLink to="/teams/$team" params={{ team: team.slug }} title={team.name} variant="row" className="-mx-1 block max-w-full truncate px-1 py-0.5">
            {team.name}
          </TextLink>
          <Mono tone="faint" truncate>
            {team.slug}
          </Mono>
        </span>
      ),
    },
    {
      id: "role",
      header: t("teams.list.columnRole"),
      width: "w-40",
      sortValue: (team) => team.role ?? "",
      cell: (team) => (team.role === null || team.role === undefined ? <EmptyCell reason={t("teams.list.notAMember")} /> : <span className="text-fg-muted">{teamRoleLabel(t, team.role)}</span>),
    },
    {
      id: "members",
      header: t("teams.list.columnMembers"),
      align: "end",
      mono: true,
      width: "w-28",
      sortValue: (team) => detailOf(team)?.members ?? null,
      cell: (team) => detailOf(team)?.members ?? <EmptyCell reason={t("teams.list.notRead")} />,
    },
    {
      id: "projects",
      header: t("teams.list.columnProjects"),
      align: "end",
      mono: true,
      width: "w-28",
      hideBelow: "sm",
      sortValue: (team) => detailOf(team)?.projects ?? null,
      cell: (team) => detailOf(team)?.projects ?? <EmptyCell reason={t("teams.list.notRead")} />,
    },
    {
      id: "resources",
      header: t("teams.list.columnResources"),
      align: "end",
      mono: true,
      width: "w-28",
      hideBelow: "md",
      sortValue: (team) => held(detailOf(team)),
      cell: (team) => held(detailOf(team)) ?? <EmptyCell reason={t("teams.list.notRead")} />,
    },
  ];

  const Add = ICONS.add;
  const header = projectsHeader(
    t,
    node,
    mayCreate ? (
      <Button variant="primary" icon={<Add aria-hidden="true" />} onClick={openCreate}>
        {t("teams.list.new")}
      </Button>
    ) : undefined,
  );
  const tabs = <ProjectsTabs />;

  const notice = <Notice title={t("teams.list.boundaryTitle")}>{t("teams.list.boundary")}</Notice>;

  const dialog = (
    <NameDialog
      open={creating || asked}
      onOpenChange={closeCreate}
      title={t("teams.list.newTitle")}
      description={t("teams.list.newDescription")}
      nameLabel={t("teams.list.nameLabel")}
      submitLabel={t("teams.list.create")}
      slug={{ label: t("teams.shortName"), help: t("teams.shortNameHelp") }}
      onSubmit={async ({ name, slug }) => {
        const created = await createTeam(name, slug === "" ? undefined : slug);
        await refreshOrganization(queryClient);
        announce(t("teams.list.created", { name: created.name }));
        // The next thing a new team needs is people.
        void navigate({ to: "/teams/$team/members", params: { team: created.slug } });
      }}
    />
  );

  if (org.error !== null && org.error !== undefined && !org.available) {
    return (
      <ListPage header={header} tabs={tabs}>
        <ErrorBlock error={org.error} title={t("teams.couldNotLoad")} onRetry={org.reload} />
      </ListPage>
    );
  }

  if (org.available && org.teams.length === 0) {
    return (
      <ListPage header={header} tabs={tabs}>
        <EmptyState
          variant="firstUse"
          icon={<Users />}
          title={t("teams.list.emptyTitle")}
          description={t("teams.list.emptyDescription")}
          command="noust team list"
        />
      </ListPage>
    );
  }

  const count = !org.available ? "" : filtered ? t("teams.list.countFiltered", { shown: shown.length, total: org.teams.length }) : t("teams.list.count", { count: org.teams.length });
  return (
    <ListPage
      header={header}
      tabs={tabs}
      notice={notice}
      filters={
        <FilterBar
          label={t("teams.list.filterLabel")}
          search={{
            value: search.q ?? "",
            onChange: (value) => onSearchChange(value === "" ? {} : { q: value }, { replace: true }),
            label: t("teams.list.searchLabel"),
            placeholder: t("teams.list.searchPlaceholder"),
          }}
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
      {...(org.available ? { footer: <CommandHint command="noust team list" label={t("teams.fromTerminal")} /> } : {})}
    >
      <DataTable
        columns={columns}
        rows={shown}
        getRowId={(team) => team.slug}
        caption={filtered ? t("teams.list.captionFiltered") : t("teams.list.caption")}
        loading={org.loading}
        skeletonRows={2}
        defaultSort={{ column: "team", direction: "ascending" }}
        mobile="cards"
        pagination="teams"
        rowActions={(team) => (
          <Menu align="end" trigger={<IconButton label={t("teams.list.actionsFor", { name: team.name })} icon={<ICONS.more />} size="sm" tooltip={false} />}>
            <MenuItem icon={<PanelTop />} onClick={() => void navigate({ to: "/teams/$team", params: { team: team.slug } })}>
              {t("teams.list.open")}
            </MenuItem>
            <MenuItem
              icon={<Users />}
              disabled={org.active === team.slug}
              onClick={() => {
                org.choose(team.slug).then(
                  () => announce(t("teams.switcher.switched", { name: team.name })),
                  (error: unknown) => reportActionError(t("teams.switcher.failed"), error),
                );
              }}
            >
              {t("teams.list.workIn")}
            </MenuItem>
          </Menu>
        )}
        empty={
          <EmptyState
            variant="inline"
            title={t("teams.list.noMatch")}
            action={
              <Button size="sm" variant="ghost" onClick={clear}>
                {t("teams.clearFilters")}
              </Button>
            }
          />
        }
      />
      {dialog}
    </ListPage>
  );
}
