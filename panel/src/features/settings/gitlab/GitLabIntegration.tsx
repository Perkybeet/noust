import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useState } from "react";

import { gitlabKeys, gitlabStatusQuery, removeGitLabConnection, removeGitLabOAuthApp, testGitLabConnection } from "../../../api/queries/gitlab";
import type { GitLabConnection, GitLabOAuthApp, GitLabStatus } from "../../../api/queries/gitlab";
import { announce } from "../../../app/Announcer";
import { CommandHint } from "../../../components/page/CommandHint";
import { QueryState } from "../../../components/page/QueryState";
import { Section } from "../../../components/page/Section";
import { Subsection } from "../../../components/page/Subsection";
import { Button } from "../../../components/ui/Button";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyCell } from "../../../components/ui/EmptyCell";
import { EmptyState } from "../../../components/ui/EmptyState";
import { IconButton } from "../../../components/ui/IconButton";
import { ICONS } from "../../../components/ui/icons";
import { Menu, MenuItem } from "../../../components/ui/Menu";
import { Mono } from "../../../components/ui/Mono";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { reportActionError } from "../../apps/useAppActions";
import { ConnectGitLabDialog } from "./ConnectGitLabDialog";
import { connectionState } from "./gitlab";

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
      cell: (connection) => (
        <span className="flex min-w-0 flex-col">
          <Mono truncate>{connection.name}</Mono>
          <span className="truncate text-12 text-fg-muted">{connection.kind === "oauth" ? t("gitlab.kind.oauth") : t("gitlab.kind.token")}</span>
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
    {
      id: "state",
      header: t("gitlab.columnState"),
      cell: (connection) => (
        <span className="flex min-w-0 flex-col items-start gap-0.5">
          <State t={t} connection={connection} />
          {/* What GitLab answered, as it said it. */}
          {connection.last_status === "failed" && connection.last_error ? (
            <Mono tone="muted" className="max-w-full text-12 break-words whitespace-pre-wrap">
              {connection.last_error}
            </Mono>
          ) : null}
        </span>
      ),
      width: "w-56",
      card: "status",
    },
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
  const [removing, setRemoving] = useState<GitLabOAuthApp | null>(null);
  return (
    <Subsection level={3} title={t("gitlab.oauthApps.title")} description={t("gitlab.oauthApps.description")}>
      <DataTable
        caption={t("gitlab.oauthApps.caption")}
        columns={oauthColumns(t)}
        rows={status.oauth_apps}
        getRowId={(app) => app.host}
        density="compact"
        rowActions={(app) => (
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
        )}
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
  );
}

/**
 * Settings, Integrations: the GitLab connections this server holds, whether by access token or
 * by signing in, checked on demand, added and removed here, with the OAuth applications the
 * sign-ins use. Every change goes through "Confirm it's you".
 */
export function GitLabIntegration() {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const query = useQuery(gitlabStatusQuery());
  const check = useCheck();
  const [connecting, setConnecting] = useState(false);
  const [removing, setRemoving] = useState<GitLabConnection | null>(null);

  return (
    <Section
      title={t("gitlab.title")}
      description={t("gitlab.description")}
      actions={
        <Button
          size="sm"
          icon={<Plus aria-hidden="true" />}
          disabled={query.data === undefined}
          onClick={() => {
            setConnecting(true);
          }}
        >
          {t("gitlab.connect")}
        </Button>
      }
    >
      <QueryState
        query={query}
        label={t("gitlab.loading")}
        skeleton={<Skeleton className="h-24 w-full rounded-card" />}
        isEmpty={(data) => data.connections.length === 0 && data.oauth_apps.length === 0}
        empty={<EmptyState variant="inline" title={t("gitlab.emptyTitle")} description={t("gitlab.emptyDescription")} />}
      >
        {(data) => (
          <div className="flex min-w-0 flex-col gap-6">
            {data.connections.length === 0 ? (
              <EmptyState variant="inline" title={t("gitlab.emptyTitle")} description={t("gitlab.emptyDescription")} />
            ) : (
              <DataTable
                caption={t("gitlab.caption")}
                columns={connectionColumns(t)}
                rows={data.connections}
                getRowId={(connection) => connection.name}
                density="compact"
                rowActions={(connection) => (
                  <Menu align="end" trigger={<IconButton label={t("common.rowActions", { name: connection.name })} icon={<ICONS.more />} tooltip={false} />}>
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
                  </Menu>
                )}
              />
            )}
            {data.oauth_apps.length > 0 ? <OAuthApps status={data} /> : null}
          </div>
        )}
      </QueryState>
      <CommandHint command="noust gitlab list" label={t("common.words.sameFromTerminal")} />
      {connecting && query.data !== undefined ? (
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
    </Section>
  );
}
