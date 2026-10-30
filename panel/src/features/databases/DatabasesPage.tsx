import { useQuery } from "@tanstack/react-query";
import { X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { sessionQuery } from "../../api/queries/auth";
import { databaseBackupsQuery, databasesQuery, enginesQuery, exposureQuery, policiesQuery } from "../../api/queries/databases";
import { useColumnCarries } from "../../app/frame/columnContext";
import { CommandHint } from "../../components/page/CommandHint";
import { FilterBar } from "../../components/page/FilterBar";
import { ListPage } from "../../components/page/ListPage";
import { ErrorBlock } from "../../components/page/QueryState";
import { Section, Sections } from "../../components/page/Section";
import { SegmentedControl } from "../../components/page/SegmentedControl";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { ICONS } from "../../components/ui/icons";
import { Notice } from "../../components/ui/Notice";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";
import { useNode } from "../../nodes/useNode";
import { useOwnerScope } from "../teams/ownership";
import { DatabaseRowActions } from "./DatabaseRowActions";
import { DatabasesTable } from "./DatabasesTable";
import { ListingProblemsNotice } from "./EngineAccess";
import { engineName } from "./engines";
import { filterDatabases } from "./filters";
import { isFiltered } from "./search";
import type { DatabasesSearch } from "./search";
import { useDatabasesHeader } from "./listHeader";
import { newestDumps, policiesById } from "./protection";
import { engineGroups } from "./servers";
import { ServerSlabs } from "./servers/ServerSlabs";
import { useDatabaseServers } from "./servers/useDatabaseServers";
import { useServerActions } from "./servers/useServerActions";

const ALL = "all";

type SearchPatch = { [K in keyof DatabasesSearch]?: DatabasesSearch[K] | undefined };

export interface DatabasesPageProps {
  search: DatabasesSearch;
  onSearchChange: (search: DatabasesSearch, options?: { replace?: boolean }) => void;
}

/** How long the rows wait for the notice above them once the list itself has answered. */
const SETTLE_MS = 1_000;

/** `ListPage`'s `arrival`: what is drawn while the list waits, and what replaces it all at once. */
const WAITING = "waiting";
const ANSWERED = "answered";

/**
 * Databases, in two parts of one page: the databases (the name first, the server each runs on, who uses it,
 * its size and whether it is backed up) and the database servers they run on, with where each stands in its
 * project's support. Above them the filters and at most one notice: a job in hand, then a server that could
 * not be read (the list is then incomplete), then a port open to the network. The engines in the side column
 * narrow both parts.
 */
export function DatabasesPage({ search, onSearchChange }: DatabasesPageProps) {
  const t = useT();
  const { node } = useNode();
  const actions = useServerActions();
  const { header, tabs, dialogs, openCreate } = useDatabasesHeader("databases", actions);
  const list = useQuery(databasesQuery());
  const serverList = useDatabaseServers();
  const engines = useQuery(enginesQuery());
  const policies = useQuery(policiesQuery());
  const dumps = useQuery(databaseBackupsQuery());
  const exposure = useQuery(exposureQuery());
  const { data: hostname } = useQuery({ ...sessionQuery(), select: (session) => session.hostname });
  const machine = node ?? hostname ?? "";
  const carried = useColumnCarries("databases");
  const listed = list.data !== undefined;
  const [settleExpired, setSettleExpired] = useState(false);
  useEffect(() => {
    if (!listed) return undefined;
    const timer = window.setTimeout(() => setSettleExpired(true), SETTLE_MS);
    return () => window.clearTimeout(timer);
  }, [listed]);

  const everything = useMemo(() => list.data?.databases ?? [], [list.data]);
  // The team the operator works in narrows the list, and the project and environment filters narrow it further.
  const owner = useOwnerScope(everything, search);
  const all = owner.inTeam;
  const byPolicy = useMemo(() => policiesById(policies.data), [policies.data]);
  const newest = useMemo(() => newestDumps(dumps.data?.backups), [dumps.data]);
  const servers = serverList.servers;
  const shown = useMemo(() => filterDatabases(owner.shown, search, byPolicy, servers ?? []), [owner.shown, search, byPolicy, servers]);
  const shownServers = useMemo(() => (servers ?? []).filter((server) => search.engine === undefined || server.flavour === search.engine || server.key === search.engine), [servers, search.engine]);
  const tagNames = useMemo(() => [...new Set(all.flatMap((database) => database.tags ?? []))].sort(), [all]);
  const engineChoices = useMemo(() => engineGroups(servers ?? [], everything), [servers, everything]);

  const set = (patch: SearchPatch, replace = false): void => {
    const next: SearchPatch = { ...search, ...patch };
    const clean: DatabasesSearch = {};
    if (next.q) clean.q = next.q;
    if (next.engine) clean.engine = next.engine;
    if (next.backups) clean.backups = next.backups;
    if (next.tag) clean.tag = next.tag;
    if (next.project) clean.project = next.project;
    if (next.environment) clean.environment = next.environment;
    onSearchChange(clean, { replace });
  };
  const clear = (): void => onSearchChange({});

  // Arriving with `?new` (the palette's "New database"): the dialog opens, and the link is spent.
  const opening = search.new === true;
  useEffect(() => {
    if (!opening) return;
    openCreate();
    const { q, engine, backups, tag, project, environment } = search;
    onSearchChange({ ...(q ? { q } : {}), ...(engine ? { engine } : {}), ...(backups ? { backups } : {}), ...(tag ? { tag } : {}), ...(project ? { project } : {}), ...(environment ? { environment } : {}) }, { replace: true });
    // Once per arrival: what it reads is what it was opened with.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opening]);

  const filtered = isFiltered(search);
  const running = (serverList.servers ?? []).some((server) => server.running);
  const exposed = exposure.data?.exposed ?? [];
  const unprotected = policies.data?.unprotected?.length ?? 0;
  const problems = list.data?.problems ?? [];

  let notice;
  if (actions.notice !== null) {
    notice = actions.notice;
  } else if (problems.length > 0) {
    notice = <ListingProblemsNotice problems={problems} engines={engines.data?.engines} />;
  } else if (exposed.length > 0) {
    notice = (
      <Notice tone="error" title={t("databases.list.exposedTitle", { count: exposed.length })}>
        {t("databases.list.exposedBody", {
          ports: exposed.map((port) => `${engineName(port.engine, engines.data?.engines)} ${port.address}:${String(port.port)}`).join(", "),
        })}
      </Notice>
    );
  }

  const Add = ICONS.add;
  // design-exception: hand-error-ternary the list's loading is its own table skeleton under filters that are already drawn (`arrival`), not a replacement of the page
  if (list.isError && list.data === undefined) {
    return (
      <>
        <ListPage header={header} tabs={tabs} arrival={ANSWERED}>
          <ErrorBlock error={list.error} title={t("databases.list.couldNotLoad")} onRetry={() => void list.refetch()} retrying={list.isRefetching} />
        </ListPage>
        {dialogs}
      </>
    );
  }

  // The settling below: the notice above the list depends on the policies and the open ports, so the list waits
  // for them and a notice arriving late never pushes the rows down under the pointer. It waits behind
  // placeholder rows, never behind a blank page, and never more than SETTLE_MS: the open-port check reads the
  // firewall and can take seconds, and rows the operator can already use are worth a notice that moves them once.
  const settling = (policies.isPending || exposure.isPending) && list.data !== undefined && !settleExpired;
  const loading = list.isPending || settling || owner.loading;

  const filterBar = (count?: string) => (
    <FilterBar
      label={t("databases.list.filterLabel")}
      search={{
        value: search.q ?? "",
        onChange: (value) => set({ q: value }, true),
        label: t("databases.list.searchLabel"),
        placeholder: t("databases.list.searchPlaceholder"),
      }}
      filters={
        <>
          {/* With the side column the engines are there; without it (one bar, a phone) they are a filter here. */}
          {!carried ? (
            <Select
              aria-label={t("databases.list.engineLabel")}
              value={search.engine ?? ALL}
              onValueChange={(value) => set({ engine: value === ALL ? undefined : value })}
              options={[{ value: ALL, label: t("databases.list.everyEngine") }, ...engineChoices.map((group) => ({ value: group.flavour, label: group.name }))]}
              className="min-w-40"
            />
          ) : null}
          {/* What the rows say one by one, counted: the way to get at the ones without a schedule. */}
          {unprotected > 0 || search.backups !== undefined ? (
            <SegmentedControl<"all" | "attention">
              label={t("databases.list.backupsLabel")}
              value={search.backups === "attention" ? "attention" : "all"}
              onValueChange={(value) => set({ backups: value === "attention" ? "attention" : undefined })}
              options={[
                { value: "all", label: t("databases.list.backupsAll") },
                { value: "attention", label: t("databases.list.backupsNone", { count: unprotected }) },
              ]}
            />
          ) : null}
          {tagNames.length > 0 || search.tag !== undefined ? (
            <Select
              aria-label={t("appTags.filter.label")}
              value={search.tag ?? ALL}
              onValueChange={(value) => set({ tag: value === ALL ? undefined : value })}
              options={[{ value: ALL, label: t("appTags.filter.every") }, ...tagNames.map((tag) => ({ value: tag, label: tag }))]}
              className="min-w-36"
            />
          ) : null}
          {owner.filters(search, (patch) => set({ project: patch.project, environment: patch.environment }))}
        </>
      }
      {...(count !== undefined ? { count } : {})}
      {...(filtered
        ? {
            actions: (
              <Button variant="ghost" icon={<X aria-hidden="true" />} onClick={clear}>
                {t("common.words.clearFilters")}
              </Button>
            ),
          }
        : {})}
    />
  );

  const count = filtered ? t("databases.list.countFiltered", { shown: shown.length, total: all.length }) : t("databases.list.count", { count: all.length });
  const empty = !loading && all.length === 0;

  const databasesPart = empty ? (
    <EmptyState
      variant="firstUse"
      icon={<OBJECT_ICONS.database />}
      title={t("databases.list.emptyTitle")}
      description={running ? t("databases.list.emptyDescription") : t("databases.list.emptyNoEngine")}
      action={
        running ? (
          <Button icon={<Add aria-hidden="true" />} onClick={openCreate}>
            {t("databases.page.newDatabase")}
          </Button>
        ) : undefined
      }
      command={running ? "noust db create postgresql shop_production" : "noust db install postgresql"}
    />
  ) : (
    <div className="flex min-w-0 flex-col gap-4">
      {loading ? null : filterBar(count)}
      <DatabasesTable
        loading={loading}
        databases={loading ? [] : shown}
        servers={servers}
        engines={engines.data?.engines}
        owner={{ scope: owner.scope, labels: owner.labels }}
        policies={byPolicy}
        dumps={newest}
        caption={filtered ? t("databases.list.captionFiltered") : t("databases.list.caption")}
        rowActions={(database) => <DatabaseRowActions database={database} engines={engines.data?.engines} />}
        empty={
          <EmptyState
            variant="inline"
            title={t("databases.list.noMatch")}
            action={
              <Button size="sm" variant="ghost" onClick={clear}>
                {t("common.words.clearFilters")}
              </Button>
            }
          />
        }
      />
    </div>
  );

  return (
    <>
      <ListPage
        header={header}
        tabs={tabs}
        // The notice, the filters and the rows arrive together, as new elements: placeholders
        // drawn under a notice not yet known would only be pushed down the page when it came.
        arrival={loading ? WAITING : ANSWERED}
        {...(!loading && notice !== undefined ? { notice } : {})}
        // Drawn with the rows, not before: under a list of unknown length it would only be pushed down the page when they arrive.
        {...(loading ? {} : { footer: <CommandHint command="noust db list" label={t("common.words.fromTerminal")} /> })}
      >
        <Sections>
          <Section title={t("dbServers.column.databases")} {...(loading || empty ? {} : { badge: <Badge>{all.length}</Badge> })}>
            {databasesPart}
          </Section>
          <Section
            title={t("dbServers.title")}
            {...(serverList.older ? { description: t("dbServers.older") } : {})}
            {...(servers === undefined ? {} : { badge: <Badge>{shownServers.length}</Badge> })}
          >
            {serverList.isError && servers === undefined ? (
              <ErrorBlock compact error={serverList.error} title={t("dbServers.loadFailed")} onRetry={serverList.refetch} retrying={serverList.isRefetching} />
            ) : (
              <ServerSlabs servers={servers === undefined ? undefined : shownServers} machine={machine} actions={actions} />
            )}
          </Section>
        </Sections>
      </ListPage>
      {dialogs}
    </>
  );
}
