import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useState } from "react";

import { registryKeys, registryLoginsQuery, removeRegistryLogin, testRegistryLogin } from "../../../api/queries/registries";
import type { RegistryLogin } from "../../../api/queries/registries";
import { announce } from "../../../app/Announcer";
import { CommandHint } from "../../../components/page/CommandHint";
import { QueryState } from "../../../components/page/QueryState";
import { Section } from "../../../components/page/Section";
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
import { TextLink } from "../../../components/ui/TextLink";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { reportActionError } from "../../apps/useAppActions";
import { RegistryDialog } from "./RegistryDialog";
import { kindWords } from "./registries";

/** How many applications a row names before "and N more". */
const APPS_SHOWN = 2;

function State({ t, login }: { t: T; login: RegistryLogin }) {
  if (login.last_status === "ok") return <StatusPill state="running" label={t("registries.state.ok")} appearance="inline" size="sm" />;
  if (login.last_status === "failed") return <StatusPill state="failed" label={t("common.statusPill.failed")} appearance="inline" size="sm" />;
  return <StatusPill state="stopped" label={t("registries.state.untested")} appearance="inline" size="sm" />;
}

function columns(t: T): Column<RegistryLogin>[] {
  return [
    {
      id: "name",
      header: t("common.words.name"),
      cell: (login) => (
        <span className="flex min-w-0 flex-col">
          <Mono truncate>{login.name}</Mono>
          <span className="truncate text-12 text-fg-muted">{kindWords(t, login.kind)}</span>
        </span>
      ),
      card: "title",
    },
    { id: "server", header: t("registries.columnRegistry"), cell: (login) => <Mono truncate>{login.server}</Mono>, hideBelow: "md", card: "meta" },
    { id: "account", header: t("registries.columnAccount"), cell: (login) => <Mono truncate>{login.username}</Mono>, hideBelow: "sm", card: "meta" },
    {
      id: "state",
      header: t("registries.columnState"),
      cell: (login) => (
        <span className="flex min-w-0 flex-col items-start gap-0.5">
          <State t={t} login={login} />
          {/* What the registry answered, as it said it. */}
          {login.last_status === "failed" && login.last_error ? <Mono tone="muted" className="max-w-full text-12 break-words whitespace-pre-wrap">{login.last_error}</Mono> : null}
        </span>
      ),
      width: "w-56",
      card: "status",
    },
    {
      id: "apps",
      header: t("registries.columnApps"),
      cell: (login) => {
        const apps = login.apps ?? [];
        if (apps.length === 0) return <EmptyCell reason={t("registries.noApps")} />;
        const shown = apps.slice(0, APPS_SHOWN);
        return (
          <span className="truncate">
            {shown.map((domain, index) => (
              <span key={domain}>
                {index > 0 ? ", " : null}
                <TextLink to="/apps/$domain/settings/deploys" params={{ domain }} size="ui">
                  {domain}
                </TextLink>
              </span>
            ))}
            {apps.length > shown.length ? <span className="text-fg-muted"> {t("registries.andMore", { count: apps.length - shown.length })}</span> : null}
          </span>
        );
      },
      hideBelow: "lg",
      card: "meta",
    },
  ];
}

/** Logs in with the stored account and says how that went; the row shows the registry's own words. */
function useTest() {
  const t = useT();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: testRegistryLogin,
    onSuccess: (login) => {
      void queryClient.invalidateQueries({ queryKey: registryKeys.all });
      if (login.last_status === "ok") toast.success(t("registries.actions.tested", { name: login.name, server: login.server }));
      else announce(t("registries.actions.testFailed", { name: login.name }));
    },
    onError: (error, name) => {
      reportActionError(t("registries.actions.testFailed", { name }), error);
    },
  });
}

/**
 * Settings, Integrations: the accounts this server logs in to registries with, tested on
 * demand, added, changed and removed here. Which application uses which is chosen in that
 * application's Deploys settings. Every change goes through "Confirm it's you".
 */
export function RegistriesSection() {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const query = useQuery(registryLoginsQuery());
  const test = useTest();
  const [dialog, setDialog] = useState<{ login: RegistryLogin | null } | null>(null);
  const [removing, setRemoving] = useState<RegistryLogin | null>(null);
  const kinds = query.data?.kinds ?? [];

  return (
    <Section
      title={t("registries.title")}
      description={t("registries.description")}
      actions={
        <Button
          size="sm"
          icon={<Plus aria-hidden="true" />}
          disabled={query.data === undefined}
          onClick={() => {
            setDialog({ login: null });
          }}
        >
          {t("registries.add")}
        </Button>
      }
    >
      <QueryState
        query={query}
        label={t("registries.loading")}
        skeleton={<Skeleton className="h-24 w-full rounded-card" />}
        isEmpty={(data) => data.logins.length === 0}
        empty={<EmptyState variant="inline" title={t("registries.emptyTitle")} description={t("registries.emptyDescription")} />}
      >
        {(data) => (
          <DataTable
            caption={t("registries.caption")}
            columns={columns(t)}
            rows={data.logins}
            getRowId={(login) => login.name}
            density="compact"
            rowActions={(login) => (
              <Menu align="end" trigger={<IconButton label={t("common.rowActions", { name: login.name })} icon={<ICONS.more />} tooltip={false} />}>
                <MenuItem
                  onClick={() => {
                    test.mutate(login.name);
                  }}
                >
                  {t("registries.actions.test")}
                </MenuItem>
                <MenuItem
                  onClick={() => {
                    setDialog({ login });
                  }}
                >
                  {t("common.words.edit")}
                </MenuItem>
                <MenuItem
                  destructive
                  icon={<ICONS.delete />}
                  onClick={() => {
                    setRemoving(login);
                  }}
                >
                  {t("common.words.remove")}
                </MenuItem>
              </Menu>
            )}
          />
        )}
      </QueryState>
      <CommandHint command="noust registry list" label={t("common.words.sameFromTerminal")} />
      {dialog !== null ? (
        <RegistryDialog
          key={dialog.login?.name ?? "new"}
          open
          login={dialog.login}
          kinds={kinds}
          onClose={() => {
            setDialog(null);
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
          title={t("registries.remove.title", { name: removing.name })}
          description={t("registries.remove.description")}
          actionLabel={t("registries.remove.action")}
          onConfirm={async () => {
            const gone = await removeRegistryLogin(removing.name);
            void queryClient.invalidateQueries({ queryKey: registryKeys.all });
            announce(gone.apps.length === 0 ? t("registries.remove.removed", { name: gone.name }) : t("registries.remove.removedFrom", { name: gone.name, count: gone.apps.length }));
          }}
        />
      ) : null}
    </Section>
  );
}
