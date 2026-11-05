import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useState } from "react";

import { gitlabKeys, gitlabStatusQuery, removeGitLabConnection, removeGitLabOAuthApp, testGitLabConnection } from "../../../api/queries/gitlab";
import type { GitLabConnection, GitLabOAuthApp, GitLabStatus } from "../../../api/queries/gitlab";
import { announce } from "../../../app/Announcer";
import { QueryState } from "../../../components/page/QueryState";
import { RowMenu } from "../../../components/page/RowMenu";
import { SaidLines } from "../../../components/page/SaidLines";
import { Subsection } from "../../../components/page/Subsection";
import { SurfaceSection } from "../../../components/page/SurfaceSection";
import { useCan } from "../../../components/page/permissions";
import { Badge } from "../../../components/ui/Badge";
import { Button } from "../../../components/ui/Button";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyCell } from "../../../components/ui/EmptyCell";
import { FirstUse } from "../../../components/ui/FirstUse";
import { ICONS } from "../../../components/ui/icons";
import { MenuItem } from "../../../components/ui/Menu";
import { Mono } from "../../../components/ui/Mono";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { reportActionError } from "../../apps/useAppActions";
import { ConnectGitLabDialog } from "./ConnectGitLabDialog";
import { connectionState } from "./gitlab";

/** What every change of a GitLab connection takes (the server's, `routes_gitlab.py`). */
const MANAGE = "apps.manage";

function State({ t, connection }: { t: T; connection: GitLabConnection }) {
  const state = connectionState(connection);
  if (state === "ok") return <StatusPill state="running" label={t("gitlab.state.ok")} appearance="inline" size="sm" />;
  if (state === "failed") return <StatusPill state="failed" label={t("common.statusPill.failed")} appearance="inline" size="sm" />;
  return <StatusPill state="stopped" label={t("gitlab.state.unchecked")} appearance="inline" size="sm" />;
}

function connectionColumns(t: T): Column<GitLabConnection>[] {
  return [
    {
      id: "name",
      header: t("gitlab.columnName"),
      // How it signs in is a tag of its name, on its line: a table's rows are all one height.
      cell: (connection) => (
        <span className="flex min-w-0 items-center gap-2">
          <Mono truncate>{connection.name}</Mono>
          <Badge>{connection.kind === "oauth" ? t("gitlab.kind.oauth") : t("gitlab.kind.token")}</Badge>
        </span>
      ),
      card: "title",
    },
    { id: "host", header: t("gitlab.columnHost"), cell: (connection) => <Mono truncate>{connection.host}</Mono>, hideBelow: "sm", card: "meta" },
    {
      id: "account",
      header: t("gitlab.columnAccount"),
      cell: (connection) => (connection.username ? <Mono truncate>{connection.username}</Mono> : <EmptyCell reason={t("gitlab.state.unchecked")} />),
      hideBelow: "md",
      card: "meta",
    },
    { id: "state", header: t("gitlab.columnState"), cell: (connection) => <State t={t} connection={connection} />, card: "status" },
  ];
}

/** Asks GitLab whether the connection still works and shows the answer in its row. */
function useCheck() {
  const t = useT();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: testGitLabConnection,
    onSuccess: (connection) => {
      void queryClient.invalidateQueries({ queryKey: gitlabKeys.status });
      if (connection.last_status === "ok") toast.success(t("gitlab.actions.tested", { name: connection.name, host: connection.host }));
      else announce(t("gitlab.actions.testFailed", { name: connection.name }));
    },
    onError: (error, name) => {
      reportActionError(t("gitlab.actions.testFailed", { name }), error);
    },
  });
}

/** What GitLab answered about the connections that did not work, as it said it. */
function Refusals({ connections }: { connections: readonly GitLabConnection[] }) {
  const t = useT();
  const lines = connections
    .filter((connection) => connection.last_status === "failed" && connection.last_error)
    .map((connection) => ({
      id: connection.name,
      said: t.rich("gitlab.refusalSaid", {
        name: (
          <span key="name" translate="no" className="font-medium">
            {connection.name}
          </span>
        ),
        said: <Mono key="said">{connection.last_error}</Mono>,
      }),
    }));
  return <SaidLines label={t("gitlab.refusalsLabel")} lines={lines} />;
}

function oauthColumns(t: T): Column<GitLabOAuthApp>[] {
  return [
    { id: "host", header: t("gitlab.oauthApps.columnHost"), cell: (app) => <Mono truncate>{app.host}</Mono>, card: "title" },
    { id: "client", header: t("gitlab.oauthApps.columnId"), cell: (app) => <Mono truncate>{app.client_id}</Mono>, card: "meta" },
  ];
}

/** The OAuth applications registered for the hosts a sign-in is made to. */
function OAuthApps({ status }: { status: GitLabStatus }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const canManage = useCan(MANAGE);
  const [removing, setRemoving] = useState<GitLabOAuthApp | null>(null);
  return (
    <div className="border-t border-border px-5 py-4">
      <Subsection level={3} title={t("gitlab.oauthApps.title")} description={t("gitlab.oauthApps.description")}>
        <DataTable
          caption={t("gitlab.oauthApps.caption")}
          columns={oauthColumns(t)}
          rows={status.oauth_apps}
          getRowId={(app) => app.host}
          density="compact"
          {...(canManage
            ? {
                rowActions: (app: GitLabOAuthApp) => (
                  <Button
                    size="sm"
                    variant="ghost"
                    aria-label={t("gitlab.oauthApps.removeLabel", { host: app.host })}
                    onClick={() => {
                      setRemoving(app);
                    }}
                  >
                    {t("common.words.remove")}
                  </Button>
                ),
              }
            : {})}
        />
        {removing !== null ? (
          <ConfirmDialog
            open
            onOpenChange={(open) => {
              if (!open) setRemoving(null);
            }}
            friction="simple"
            destructive
            server={node}
            title={t("gitlab.oauthApps.removeTitle", { host: removing.host })}
            description={t("gitlab.oauthApps.removeDescription", { host: removing.host })}
            actionLabel={t("gitlab.oauthApps.removeAction")}
            onConfirm={async () => {
              const host = removing.host;
              await removeGitLabOAuthApp(host);
              void queryClient.invalidateQueries({ queryKey: gitlabKeys.status });
              announce(t("gitlab.oauthApps.removed", { host }));
            }}
          />
        ) : null}
      </Subsection>
    </div>
  );
}

/**
 * Settings, Integrations: the GitLab connections this server holds, whether by access token or by signing in, checked
 * on demand, added and removed here, with the OAuth applications the sign-ins use. A slab with the state beside its
 * title: not connected is one sentence of what connecting gives and the button that starts it; connected is the
 * connections as a table, and what GitLab answered about one that failed, under it, as it said it. Every change goes
 * through "Confirm it's you".
 */
export function GitLabIntegration() {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const canManage = useCan(MANAGE);
  const query = useQuery(gitlabStatusQuery());
  const check = useCheck();
  const [connecting, setConnecting] = useState(false);
  const [removing, setRemoving] = useState<GitLabConnection | null>(null);
  const connected = query.data !== undefined && query.data.connections.length > 0;
  const connect = (primary: boolean) => (
    <Button
      variant={primary ? "primary" : "secondary"}
      size={primary ? "md" : "sm"}
      icon={<Plus aria-hidden="true" />}
      disabled={query.data === undefined}
      onClick={() => {
        setConnecting(true);
      }}
    >
      {t("gitlab.connect")}
    </Button>
  );

  return (
    <SurfaceSection
      title={t("gitlab.title")}
      icon={OBJECT_ICONS.source}
      status={query.data === undefined ? undefined : <StatusPill state={connected ? "running" : "stopped"} label={connected ? t("gitlab.state.connected") : t("gitlab.state.notConnected")} size="sm" />}
      description={connected ? t("gitlab.description") : undefined}
      actions={connected && canManage ? connect(false) : undefined}
      flush={connected}
    >
      <QueryState
        query={query}
        label={t("gitlab.loading")}
        skeleton={<Skeleton className="h-24 w-full rounded-card" />}
        isEmpty={(data) => data.connections.length === 0 && data.oauth_apps.length === 0}
        empty={<FirstUse {...(canManage ? { action: connect(true) } : {})}>{t("gitlab.firstUse")}</FirstUse>}
      >
        {(data) =>
          data.connections.length === 0 ? (
            <FirstUse {...(canManage ? { action: connect(true) } : {})}>{t("gitlab.firstUse")}</FirstUse>
          ) : (
            <>
              <DataTable
                embedded
                caption={t("gitlab.caption")}
                columns={connectionColumns(t)}
                rows={data.connections}
                getRowId={(connection) => connection.name}
                density="compact"
                {...(canManage
                  ? {
                      rowActions: (connection: GitLabConnection) => (
                        <RowMenu name={connection.name}>
                          {/* design-exception: write-without-permission the row's menu is drawn only where the table has actions, for apps.manage */}
                          <MenuItem
                            onClick={() => {
                              check.mutate(connection.name);
                            }}
                          >
                            {t("gitlab.actions.test")}
                          </MenuItem>
                          <MenuItem
                            destructive
                            icon={<ICONS.delete />}
                            onClick={() => {
                              setRemoving(connection);
                            }}
                          >
                            {t("common.words.remove")}
                          </MenuItem>
                        </RowMenu>
                      ),
                    }
                  : {})}
              />
              <Refusals connections={data.connections} />
              {data.oauth_apps.length > 0 ? <OAuthApps status={data} /> : null}
            </>
          )
        }
      </QueryState>
      {connecting && canManage && query.data !== undefined ? (
        <ConnectGitLabDialog
          open
          status={query.data}
          onClose={() => {
            setConnecting(false);
          }}
        />
      ) : null}
      {removing !== null ? (
        <ConfirmDialog
          open
          onOpenChange={(open) => {
            if (!open) setRemoving(null);
          }}
          friction="simple"
          destructive
          server={node}
          title={t("gitlab.remove.title", { name: removing.name })}
          description={t("gitlab.remove.description")}
          actionLabel={t("gitlab.remove.action")}
          onConfirm={async () => {
            const name = removing.name;
            await removeGitLabConnection(name);
            void queryClient.invalidateQueries({ queryKey: gitlabKeys.all });
            announce(t("gitlab.remove.removed", { name }));
          }}
        />
      ) : null}
    </SurfaceSection>
  );
}
