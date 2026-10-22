import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { Archive, Database as DatabaseIcon, Link2, PanelTop, Plug, SquareTerminal, Tags, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { request } from "../../api/client";
import { databaseBackupsQuery, databaseKeys, databasesQuery, enginesQuery, exposureQuery, policiesQuery } from "../../api/queries/databases";
import type { Database, Engine } from "../../api/queries/databases";
import { jobKeys } from "../../api/queries/jobs";
import { CommandHint } from "../../components/page/CommandHint";
import { FilterBar } from "../../components/page/FilterBar";
import { ListPage } from "../../components/page/ListPage";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { ICONS } from "../../components/ui/icons";
import { IconButton } from "../../components/ui/IconButton";
import { Menu, MenuGroup, MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { Notice } from "../../components/ui/Notice";
import { Select } from "../../components/ui/Select";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { reportActionError } from "../apps/useAppActions";
import { useOwnerScope } from "../teams/ownership";
import { ResourceTagsDialog } from "../app/settings/ResourceTagsDialog";
import { DatabasesTable } from "./DatabasesTable";
import { ListingProblemsNotice } from "./EngineAccess";
import { can, engineName, instanceLabel, isContainer, sortInstances } from "./engines";
import { filterDatabases, isFiltered } from "./filters";
import type { DatabasesSearch } from "./filters";
import { useDatabasesHeader } from "./listHeader";
import { newestDumps, policiesById } from "./protection";

const ALL = "all";

type SearchPatch = { [K in keyof DatabasesSearch]?: DatabasesSearch[K] | undefined };

export interface DatabasesPageProps {
  search: DatabasesSearch;
  onSearchChange: (search: DatabasesSearch, options?: { replace?: boolean }) => void;
}

/** The menu at the end of a database's row: open it, back it up now, query it, connect to it. */
function RowActions({ database, engines, t }: { database: Database; engines: readonly Engine[] | undefined; t: T }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const engine = engines?.find((item) => item.name === database.engine);
  const params = { engine: database.engine, name: database.name };
  const backup = useMutation({
    mutationFn: () => request("post", "/api/databases/backup-policies/{engine}/{database}/run", { params: { engine: database.engine, database: database.name } }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: jobKeys.active });
      toast.info(t("databases.list.backupQueued", { name: database.name }), {
        action: { label: t("databases.list.follow"), onClick: () => void navigate({ to: "/databases/$engine/$name/backups", params }) },
      });
    },
    onError: (error) => reportActionError(t("databases.list.backupFailed", { name: database.name }), error),
  });
  // A use found in an application's environment becomes a recorded link; the application is
  // not touched (its environment already names the database), so nothing needs confirming.
  const record = useMutation({
    mutationFn: (app: string) =>
      request("post", "/api/databases/databases/{engine}/{name}/links/detected", { params: { engine: database.engine, name: database.name }, body: { app } }),
    onSuccess: (_link, app) => {
      void queryClient.invalidateQueries({ queryKey: databaseKeys.lists });
      void queryClient.invalidateQueries({ queryKey: databaseKeys.database(database.engine, database.name) });
      toast.success(t("databases.list.linkRecorded", { app, name: database.name }));
    },
    onError: (error, app) => reportActionError(t("databases.list.recordFailed", { app, name: database.name }), error),
  });
  const detected = (database.detected_apps ?? []).filter((app) => !(database.apps ?? []).includes(app));
  const [editingTags, setEditingTags] = useState(false);
  return (
    <>
    <Menu align="end" trigger={<IconButton label={t("common.rowActions", { name: database.name })} icon={<ICONS.more />} size="sm" tooltip={false} />}>
      <MenuItem icon={<PanelTop />} onClick={() => void navigate({ to: "/databases/$engine/$name", params })}>
        {t("databases.list.open")}
      </MenuItem>
      {can(engine, "sql") ? (
        <MenuItem icon={<SquareTerminal />} disabled={database.missing} onClick={() => void navigate({ to: "/databases/$engine/$name/query", params })}>
          {t("databases.list.query")}
        </MenuItem>
      ) : null}
      <MenuItem icon={<Plug />} disabled={database.missing} onClick={() => void navigate({ to: "/databases/$engine/$name/connect", params })}>
        {t("databases.list.connect")}
      </MenuItem>
      <MenuItem icon={<Tags />} disabled={database.missing} onClick={() => setEditingTags(true)}>
        {t("databases.list.editTags")}
      </MenuItem>
      {can(engine, "dump") ? (
        <>
          <MenuSeparator />
          <MenuItem icon={<Archive />} disabled={database.missing || backup.isPending} onClick={() => backup.mutate()}>
            {t("common.words.backUpNow")}
          </MenuItem>
        </>
      ) : null}
      {detected.length > 0 ? (
        <>
          <MenuSeparator />
          <MenuGroup label={t("databases.list.detectedGroup")}>
            {detected.map((app) => (
              <MenuItem
                key={app}
                icon={<Link2 />}
                disabled={record.isPending}
                description={t("databases.list.recordLinkDescription")}
                onClick={() => record.mutate(app)}
              >
                {t("databases.list.recordLink", { app })}
              </MenuItem>
            ))}
          </MenuGroup>
        </>
      ) : null}
    </Menu>
    {editingTags ? (
      <ResourceTagsDialog
        name={database.name}
        noun={t("databases.list.noun")}
        tags={database.tags ?? []}
        save={(tags) => request("put", "/api/databases/databases/{engine}/{name}/tags", { params: { engine: database.engine, name: database.name }, body: { tags } })}
        onSaved={() => void queryClient.invalidateQueries({ queryKey: databaseKeys.lists })}
        onClose={() => setEditingTags(false)}
      />
    ) : null}
    </>
  );
}

/** How long the rows wait for the notice above them once the list itself has answered. */
const SETTLE_MS = 1_000;

/** `ListPage`'s `arrival`: what is drawn while the list waits, and what replaces it all at once. */
const WAITING = "waiting";
const ANSWERED = "answered";

/**
 * Every database on the server, as a T1 list: the name first and whether it is backed up
 * beside it, the engine, who uses it, its size and its newest dump. Above it, the filters and
 * at most one notice (an engine that could not be read first, since the list itself is then
 * incomplete; then a port open to the network); the engines have their own tab.
 */
export function DatabasesPage({ search, onSearchChange }: DatabasesPageProps) {
  const t = useT();
  const { header, tabs, dialogs, openCreate } = useDatabasesHeader();
  const list = useQuery(databasesQuery());
  const engines = useQuery(enginesQuery());
  const policies = useQuery(policiesQuery());
  const dumps = useQuery(databaseBackupsQuery());
  const exposure = useQuery(exposureQuery());
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
  const shown = useMemo(() => filterDatabases(owner.shown, search, byPolicy), [owner.shown, search, byPolicy]);
  const tagNames = useMemo(() => [...new Set(all.flatMap((database) => database.tags ?? []))].sort(), [all]);
  // The server's own engines, then each instance in a container, named by where it runs so two
  // PostgreSQLs are told apart.
  const engineOptions = useMemo(
    () => sortInstances((engines.data?.engines ?? []).filter((engine) => engine.installed || isContainer(engine))),
    [engines.data],
  );

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
  const running = (engines.data?.engines ?? []).some((engine) => engine.running);
  const exposed = exposure.data?.exposed ?? [];
  const unprotected = policies.data?.unprotected?.length ?? 0;
  const problems = list.data?.problems ?? [];

  let notice;
  if (problems.length > 0) {
    notice = <ListingProblemsNotice problems={problems} engines={engines.data?.engines} />;
  } else if (exposed.length > 0) {
    notice = (
      <Notice tone="error" title={t("databases.list.exposedTitle", { count: exposed.length })}>
        {t("databases.list.exposedBody", {
          ports: exposed.map((port) => `${engineName(port.engine, engines.data?.engines)} ${port.address}:${String(port.port)}`).join(", "),
        })}
      </Notice>
    );
  } else if (unprotected > 0 && search.backups === undefined) {
    notice = (
      <Notice
        tone="warning"
        title={t("databases.list.unprotectedTitle", { count: unprotected })}
        action={
          <Button size="sm" onClick={() => set({ backups: "attention" })}>
            {t("databases.list.showThem")}
          </Button>
        }
      >
        {t("databases.list.unprotectedBody")}
      </Notice>
    );
  }

  const Add = ICONS.add;
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

  // The filters and the count above the table, drawn with the rows (`arrival` below):
  // nothing is there to be pushed when they come.
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
          <Select
            aria-label={t("databases.list.engineLabel")}
            value={search.engine ?? ALL}
            onValueChange={(value) => set({ engine: value === ALL ? undefined : value })}
            options={[{ value: ALL, label: t("databases.list.everyEngine") }, ...engineOptions.map((engine) => ({ value: engine.name, label: instanceLabel(t, engine) }))]}
            className="min-w-40"
          />
          <Select
            aria-label={t("databases.list.backupsLabel")}
            value={search.backups ?? ALL}
            onValueChange={(value) => set({ backups: value === "attention" ? "attention" : undefined })}
            options={[
              { value: ALL, label: t("databases.list.everyBackupState") },
              { value: "attention", label: t("databases.list.needsAttention") },
            ]}
            className="min-w-40"
          />
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

  // The notice above the list depends on the policies and the open ports: the list waits for
  // them, so a notice arriving late never pushes the rows down under the pointer. It waits
  // behind placeholder rows, never behind a blank page. Never more than SETTLE_MS: the
  // open-port check reads the firewall and can take seconds, and rows the operator can already
  // use are worth a notice that moves them once.
  const settling = (policies.isPending || exposure.isPending) && list.data !== undefined && !settleExpired;
  const loading = list.isPending || settling || owner.loading;

  if (!loading && all.length === 0 && problems.length > 0) {
    return (
      <>
        <ListPage header={header} tabs={tabs} notice={notice} arrival={ANSWERED} footer={<CommandHint command="noust db list" label={t("common.words.fromTerminal")} />}>
          {null}
        </ListPage>
        {dialogs}
      </>
    );
  }

  if (!loading && all.length === 0) {
    return (
      <>
        <ListPage header={header} tabs={tabs} arrival={ANSWERED} footer={<CommandHint command="noust db list" label={t("common.words.fromTerminal")} />}>
          <EmptyState
            variant="firstUse"
            icon={<DatabaseIcon />}
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
        </ListPage>
        {dialogs}
      </>
    );
  }

  const count =
    filtered ? t("databases.list.countFiltered", { shown: shown.length, total: all.length }) : t("databases.list.count", { count: all.length });

  return (
    <>
      <ListPage
        header={header}
        tabs={tabs}
        // The notice, the filters and the rows arrive together, as new elements: placeholders
        // drawn under a notice not yet known would only be pushed down the page when it came.
        arrival={loading ? WAITING : ANSWERED}
        {...(!loading && notice !== undefined ? { notice } : {})}
        {...(loading ? {} : { filters: filterBar(count) })}
        // Drawn with the rows, not before: under a list of unknown length it would only be pushed
        // down the page when they arrive.
        {...(loading ? {} : { footer: <CommandHint command="noust db list" label={t("common.words.fromTerminal")} /> })}
      >
        <DatabasesTable
          loading={loading}
          databases={loading ? [] : shown}
          owner={{ scope: owner.scope, labels: owner.labels }}
          engines={engines.data?.engines}
          policies={byPolicy}
          dumps={newest}
          caption={filtered ? t("databases.list.captionFiltered") : t("databases.list.caption")}
          rowActions={(database) => <RowActions database={database} engines={engines.data?.engines} t={t} />}
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
      </ListPage>
      {dialogs}
    </>
  );
}
