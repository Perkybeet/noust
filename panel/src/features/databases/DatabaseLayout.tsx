import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, Outlet, useNavigate } from "@tanstack/react-router";
import { Archive, ArchiveRestore, Database as DatabaseIcon, Trash2, UserCog } from "lucide-react";
import { useCallback, useState } from "react";

import { isApiError, request } from "../../api/client";
import { databaseKeys, databaseOverviewQuery, enginesQuery } from "../../api/queries/databases";
import type { DatabaseOverview } from "../../api/queries/databases";
import { jobKeys } from "../../api/queries/jobs";
import { LinkTabs } from "../../app/LinkTabs";
import type { PageHeaderProps } from "../../app/PageHeader";
import { DetailPage } from "../../components/page/DetailPage";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button, buttonClassName } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { EmptyState } from "../../components/ui/EmptyState";
import { MenuItem, MenuSeparator } from "../../components/ui/Menu";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusPill } from "../../components/ui/StatusPill";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { useNode } from "../../nodes/useNode";
import { reportActionError } from "../apps/useAppActions";
import { sizeWords } from "./DatabasesTable";
import { can, engineName } from "./engines";
import { FixOwnerDialog } from "./FixOwnerDialog";
import { DatabaseJobProvider, DatabaseJobSlot, useDatabaseJob, useHasDatabaseJob } from "./jobs";
import { databaseTabs } from "./tabs";
import { TEXT_LINK } from "./ui";

/** The header's facts: the engine and its version, the size, the owner, who uses it. */
function Facts({ overview, t }: { overview: DatabaseOverview; t: T }) {
  const database = overview.database;
  const size = sizeWords(database.size, t);
  const apps = database.apps ?? [];
  return (
    <>
      <span>
        {overview.display_name} {database.engine_version ? <Mono tone="muted">{database.engine_version}</Mono> : null}
      </span>
      {size !== null ? <span>{size}</span> : null}
      {database.owner ? <span>{t.rich("databases.layout.ownerFact", { owner: <Mono tone="muted">{database.owner}</Mono> })}</span> : null}
      {apps.length > 0 ? (
        <span className="flex min-w-0 flex-wrap items-center gap-x-1.5">
          {t.rich("databases.layout.usedByFact", {
            apps: (
              <span className="inline-flex flex-wrap gap-x-1.5">
                {apps.map((domain) => (
                  <Link key={domain} to="/apps/$domain/database" params={{ domain }} translate="no" className={TEXT_LINK}>
                    {domain}
                  </Link>
                ))}
              </span>
            ),
          })}
        </span>
      ) : null}
    </>
  );
}

function FactsSkeleton() {
  return (
    <span aria-hidden="true" className="flex h-5 items-center gap-3">
      <Skeleton className="h-3 w-24" />
      <Skeleton className="h-3 w-14" />
      <Skeleton className="h-3 w-28" />
    </span>
  );
}

/** What the page is doing while a job runs, else whether the database is where it should be. */
function useHeaderState(overview: DatabaseOverview | undefined, t: T) {
  if (overview === undefined) return null;
  if (overview.database.missing) return { state: "failed" as const, label: t("databases.layout.stateMissing") };
  return { state: "running" as const, label: t("databases.layout.stateOnline") };
}

interface LayoutBodyProps {
  engine: string;
  name: string;
}

function LayoutBody({ engine, name }: LayoutBodyProps) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const overview = useQuery(databaseOverviewQuery(engine, name));
  const engines = useQuery(enginesQuery());
  const job = useDatabaseJob();
  const hasJob = useHasDatabaseJob();
  const [dropping, setDropping] = useState(false);
  const [fixingOwner, setFixingOwner] = useState(false);
  const navigate = useNavigate();

  const listed = engines.data?.engines.find((item) => item.name === engine);
  const capabilities = overview.data?.capabilities ?? listed?.capabilities;
  const data = overview.data;
  const database = data?.database;
  const display = data?.display_name ?? engineName(engine, engines.data?.engines);
  const state = useHeaderState(data, t);
  const links = data?.links ?? [];

  const backup = useMutation({
    mutationFn: () => request("post", "/api/databases/backup-policies/{engine}/{database}/run", { params: { engine, database: name } }),
    onSuccess: (accepted) => {
      job.track(accepted, "backup");
      void queryClient.invalidateQueries({ queryKey: jobKeys.active });
    },
    onError: (error) => reportActionError(t("databases.list.backupFailed", { name }), error),
  });

  const forget = useMutation({
    mutationFn: () => request("post", "/api/databases/databases/{engine}/{name}/forget", { params: { engine, name } }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: databaseKeys.lists });
      toast.success(t("databases.layout.forgotten", { name }));
      void navigate({ to: "/databases" });
    },
    onError: (error) => reportActionError(t("databases.layout.forgetFailed", { name }), error),
  });

  const header: PageHeaderProps = {
    title: name,
    mono: true,
    server: node,
    breadcrumbs: [{ label: t("databases.page.title"), to: "/databases" }],
  };

  if (overview.isError && isApiError(overview.error) && overview.error.status === 404) {
    return (
      <DetailPage header={header}>
        <EmptyState
          variant="firstUse"
          icon={<DatabaseIcon />}
          title={t("databases.layout.notFoundTitle")}
          description={t("databases.layout.notFoundDescription", { name, engine: display })}
          action={
            <Link to="/databases" className={buttonClassName("secondary")}>
              {t("databases.layout.allDatabases")}
            </Link>
          }
          command="noust db list"
        />
      </DetailPage>
    );
  }

  let banner;
  if (data === undefined && overview.isError) {
    banner = <ErrorBlock error={overview.error} title={t("databases.layout.loadError", { name })} onRetry={() => void overview.refetch()} retrying={overview.isRefetching} />;
  } else if (database?.missing) {
    banner = (
      <Notice
        variant="banner"
        tone="error"
        title={t("databases.layout.missingTitle")}
        action={
          <Button size="sm" loading={forget.isPending} onClick={() => forget.mutate()}>
            {t("databases.layout.forget")}
          </Button>
        }
      >
        {t("databases.layout.missingBody", { engine: display })}
      </Notice>
    );
  }

  const dumps = can({ capabilities: capabilities ?? [] }, "dump");
  const canFixOwner = engine === "postgresql" && database?.username != null && database.owner != null && database.owner !== database.username;

  return (
    <>
      <DetailPage
        header={{
          ...header,
          status: state !== null ? <StatusPill state={state.state} label={state.label} /> : <Skeleton className="h-6 w-24 rounded-pill" />,
          meta: data !== undefined ? <Facts overview={data} t={t} /> : <FactsSkeleton />,
          ...(dumps
            ? {
                primaryAction: (
                  <Button
                    variant="primary"
                    icon={<Archive aria-hidden="true" />}
                    loading={backup.isPending}
                    disabled={job.busy || database?.missing === true}
                    onClick={() => backup.mutate()}
                  >
                    {t("databases.layout.backUpNow")}
                  </Button>
                ),
              }
            : {}),
          overflow: (
            <>
              {dumps ? (
                <MenuItem icon={<ArchiveRestore />} onClick={() => void navigate({ to: "/databases/$engine/$name/backups", params: { engine, name } })}>
                  {t("databases.layout.restore")}
                </MenuItem>
              ) : null}
              {canFixOwner ? (
                <MenuItem icon={<UserCog />} onClick={() => setFixingOwner(true)}>
                  {t("databases.layout.fixOwner", { user: database.username ?? "" })}
                </MenuItem>
              ) : null}
              <MenuSeparator />
              {database?.missing ? (
                <MenuItem icon={<Trash2 />} destructive onClick={() => forget.mutate()}>
                  {t("databases.layout.forget")}
                </MenuItem>
              ) : (
                <MenuItem icon={<Trash2 />} destructive disabled={job.busy || data === undefined} onClick={() => setDropping(true)}>
                  {t("databases.layout.drop")}
                </MenuItem>
              )}
            </>
          ),
        }}
        {...(banner !== undefined ? { banner } : {})}
        {...(hasJob ? { job: <DatabaseJobSlot /> } : {})}
        tabs={<LinkTabs label={t("databases.layout.sections")} tabs={databaseTabs(engine, name, capabilities)} />}
      >
        <Outlet />
      </DetailPage>
      {data !== undefined ? (
        <ConfirmDialog
          open={dropping}
          onOpenChange={setDropping}
          title={t("databases.drop.title", { name })}
          description={
            links.length > 0
              ? t("databases.drop.descriptionLinked", { name, engine: display, apps: links.map((link) => link.domain).join(", ") })
              : t("databases.drop.description", { name, engine: display })
          }
          actionLabel={t("databases.drop.action")}
          confirmText={name}
          server={node}
          onConfirm={async () => {
            const accepted = await request("delete", "/api/databases/databases/{engine}/{name}", {
              params: { engine, name },
              query: { force: false, keep_backup: true, unlink: links.length > 0 },
            });
            job.track(accepted, "drop");
            void queryClient.invalidateQueries({ queryKey: jobKeys.active });
          }}
        />
      ) : null}
      {fixingOwner && database?.username ? <FixOwnerDialog engine={engine} name={name} owner={database.username} onClose={() => setFixingOwner(false)} /> : null}
    </>
  );
}

/**
 * One database, as a T2 page: a header that stays put on every tab (its name, whether it is
 * there, its engine, size, owner and who uses it, "Back up now" and the rest in the menu), the
 * job in hand, and its sections as tabs whose state is the URL, drawn from what its engine can
 * do. Dropping it follows the job here and, once it has gone, goes back to the list.
 */
export function DatabaseLayout({ engine, name }: LayoutBodyProps) {
  const navigate = useNavigate();
  const dropped = useCallback(() => void navigate({ to: "/databases", replace: true }), [navigate]);
  return (
    <DatabaseJobProvider engine={engine} name={name} onDropped={dropped}>
      <LayoutBody engine={engine} name={name} />
    </DatabaseJobProvider>
  );
}
