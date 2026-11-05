import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useState } from "react";

import { registryKeys, registryLoginsQuery, removeRegistryLogin, testRegistryLogin } from "../../../api/queries/registries";
import type { RegistryLogin } from "../../../api/queries/registries";
import { announce } from "../../../app/Announcer";
import { QueryState } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { RowMenu } from "../../../components/page/RowMenu";
import { SaidLines } from "../../../components/page/SaidLines";
import { SurfaceSection } from "../../../components/page/SurfaceSection";
import { GuardedButton, ReadOnlyLine, useCan } from "../../../components/page/permissions";
import { Button } from "../../../components/ui/Button";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyCell } from "../../../components/ui/EmptyCell";
import { FirstUse } from "../../../components/ui/FirstUse";
import { ICONS } from "../../../components/ui/icons";
import { MenuItem } from "../../../components/ui/Menu";
import { Mono } from "../../../components/ui/Mono";
import { NameWithTags } from "../../../components/ui/NameWithTags";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { TextLink } from "../../../components/ui/TextLink";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { reportActionError } from "../../apps/useAppActions";
import { SettingsPrimaryAction } from "../SettingsPrimaryAction";
import { RegistryDialog } from "./RegistryDialog";

/** How many applications a row names after its count before "+N". */
const APPS_SHOWN = 1;

/** The permission every change to a registry account takes (the server's, `routes_registries.py`). */
const MANAGE = "apps.manage";

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
      // The kind of registry is in its address and in the dialog that edits it: the row has no room for a sentence of it.
      cell: (login) => <Mono className="font-medium">{login.name}</Mono>,
      card: "title",
    },
    { id: "server", header: t("registries.columnRegistry"), cell: (login) => <Mono truncate>{login.server}</Mono>, hideBelow: "md", card: "meta" },
    { id: "account", header: t("registries.columnAccount"), cell: (login) => <Mono truncate>{login.username}</Mono>, hideBelow: "sm", card: "meta" },
    {
      id: "apps",
      header: t("registries.columnApps"),
      cell: (login) => {
        const apps = login.apps ?? [];
        if (apps.length === 0) return <EmptyCell reason={t("registries.noApps")} />;
        return (
          <NameWithTags
            name={t("registries.appsCount", { count: apps.length })}
            tags={apps}
            shown={APPS_SHOWN}
            title={t("registries.appsOf", { name: login.name })}
            renderTag={(domain) => (
              <TextLink to="/apps/$domain/settings/deploys" params={{ domain }} size="ui">
                {domain}
              </TextLink>
            )}
          />
        );
      },
      hideBelow: "lg",
      card: "meta",
    },
    {
      id: "state",
      header: t("registries.columnState"),
      cell: (login) => (
        <span className="flex min-w-0 items-center gap-2">
          <State t={t} login={login} />
          {login.last_tested_at ? <RelativeTime value={login.last_tested_at} className="text-12 text-fg-muted" /> : null}
        </span>
      ),
      card: "status",
    },
  ];
}

/** Logs in with the stored account and says how that went; what the registry answered is under the table, as it said it. */
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
 * What each registry that refused a login answered, as it answered, with the way to mend it beside the answer: the
 * password is what a refusal is almost always about, and the button is where the person is looking, not in a row that
 * has no room for it.
 */
function Refusals({ logins, onChangePassword }: { logins: readonly RegistryLogin[]; onChangePassword: ((login: RegistryLogin) => void) | undefined }) {
  const t = useT();
  const lines = logins
    .filter((login) => login.last_status === "failed" && login.last_error)
    .map((login) => ({
      id: login.name,
      said: t.rich("registries.refusalSaid", {
        name: (
          <span key="name" translate="no" className="font-medium">
            {login.name}
          </span>
        ),
        said: <Mono key="said">{login.last_error}</Mono>,
      }),
      ...(onChangePassword === undefined
        ? {}
        : {
            note: (
              <Button
                size="sm"
                onClick={() => {
                  onChangePassword(login);
                }}
              >
                {t("registries.changePassword")}
              </Button>
            ),
          }),
    }));
  return <SaidLines label={t("registries.refusalsLabel")} lines={lines} />;
}

/**
 * Settings, Image registries: the accounts this server logs in to registries with, tested on demand, added, changed
 * and removed here. Which application uses which is chosen in that application's Deploys settings. Every change goes
 * through "Confirm it's you". The first time is one sentence that says when an account is needed at all; the page's one
 * action, in its header, adds the first. A role that cannot manage applications reads the rows and sees no button but
 * the locked one.
 */
export function RegistriesSection() {
  const t = useT();
  const { node } = useNode();
  const canManage = useCan(MANAGE);
  const queryClient = useQueryClient();
  const query = useQuery(registryLoginsQuery());
  const test = useTest();
  const [dialog, setDialog] = useState<{ login: RegistryLogin | null } | null>(null);
  const [removing, setRemoving] = useState<RegistryLogin | null>(null);
  const kinds = query.data?.kinds ?? [];
  const first = query.data?.logins.length === 0;
  // Edge to edge while the rows are read and when they are there; the answer that failed is a block with its own room.
  const flush = query.isPending || query.data !== undefined;

  const rowActions = (login: RegistryLogin) => (
    <span className="flex items-center justify-end gap-1.5">
      <RowMenu name={login.name}>
        {/* design-exception: write-without-permission the row's menu is drawn only where the table has actions, for apps.manage */}
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
      </RowMenu>
    </span>
  );

  return (
    <>
      <SettingsPrimaryAction>
        <GuardedButton
          permission={MANAGE}
          variant="primary"
          icon={<Plus aria-hidden="true" />}
          disabled={query.data === undefined}
          onClick={() => {
            setDialog({ login: null });
          }}
        >
          {t("registries.add")}
        </GuardedButton>
      </SettingsPrimaryAction>
      <ReadOnlyLine permission={MANAGE} />
      {first ? (
        // Nothing yet: not a slab with an empty table, one sentence about when an account is needed at all.
        <section aria-label={t("registries.accountsTitle")}>
          <FirstUse>{t("registries.firstUse")}</FirstUse>
        </section>
      ) : (
        // The slab is the same while the accounts are read, when they arrive and when they cannot be read: only what is inside it changes.
        <SurfaceSection title={t("registries.accountsTitle")} icon={OBJECT_ICONS.registry} description={t("registries.accountsLine")} help={t("registries.accountsHelp")} flush={flush}>
          <QueryState query={query} label={t("registries.loading")} skeleton={<Skeleton className="m-5 h-24 w-auto rounded-card" />}>
            {(data) => (
              <>
                <DataTable
                  embedded
                  caption={t("registries.caption")}
                  columns={columns(t)}
                  rows={data.logins}
                  getRowId={(login) => login.name}
                  density="compact"
                  {...(canManage ? { rowActions } : {})}
                />
                <Refusals
                  logins={data.logins}
                  onChangePassword={
                    canManage
                      ? (login) => {
                          setDialog({ login });
                        }
                      : undefined
                  }
                />
              </>
            )}
          </QueryState>
        </SurfaceSection>
      )}
      {dialog !== null && canManage ? (
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
    </>
  );
}
