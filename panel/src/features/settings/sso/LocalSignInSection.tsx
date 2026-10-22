import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { localSignInQuery, setLocalSignIn, ssoAdminKeys } from "../../../api/queries/ssoAdmin";
import type { LocalSignIn } from "../../../api/queries/ssoAdmin";
import { ErrorBlock } from "../../../components/page/QueryState";
import { SettingsGroup } from "../../../components/page/SettingsGroup";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Checkbox } from "../../../components/ui/Checkbox";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { Mono } from "../../../components/ui/Mono";
import { Setting } from "../../../components/ui/Setting";
import { StatusPill } from "../../../components/ui/StatusPill";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import { accountsQuery } from "../accounts/api";
import type { Account } from "../accounts/api";
import { roleLabel } from "../accounts/roles";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";

/** Roles first that can administer: the accounts worth keeping a password for. */
const ADMINISTRATORS = new Set(["admin", "security"]);

function byAdministrator(a: Account, b: Account): number {
  const rank = (account: Account): number => (ADMINISTRATORS.has(account.role) ? 0 : 1);
  return rank(a) - rank(b) || a.username.localeCompare(b.username);
}

function Names({ names, empty }: { names: readonly string[]; empty: string }) {
  if (names.length === 0) return <span className="text-13 text-fg-muted">{empty}</span>;
  return (
    <ul className="flex flex-wrap gap-x-3 gap-y-1">
      {names.map((name) => (
        <li key={name}>
          <Mono tone="default">{name}</Mono>
        </li>
      ))}
    </ul>
  );
}

interface KeepersDialogProps {
  current: LocalSignIn;
  /** Turning it off for the first time, or only changing who keeps it. */
  turningOff: boolean;
  onClose: () => void;
}

/**
 * Choosing who keeps a password when the identity provider is the way in, and turning the
 * password sign-in off with that choice. The server decides whether the choice leaves somebody
 * able to administer, and its words are shown when it does not.
 */
function KeepersDialog({ current, turningOff, onClose }: KeepersDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const accounts = useQuery(accountsQuery());
  const [kept, setKept] = useState<ReadonlySet<string>>(new Set(current.exempt));

  const save = useMutation({
    mutationFn: () => setLocalSignIn({ enabled: false, exempt: [...kept].sort() }),
    onSuccess: () => {
      toast.success(turningOff ? t("ssoAdmin.local.turnedOff") : t("ssoAdmin.local.changed"));
      void queryClient.invalidateQueries({ queryKey: ssoAdminKeys.localSignIn });
    },
  });

  const toggle = (username: string, on: boolean): void => {
    setKept((previous) => {
      const next = new Set(previous);
      if (on) next.add(username);
      else next.delete(username);
      return next;
    });
  };
  const active = (accounts.data?.accounts ?? []).filter((account) => account.status === "active").sort(byAdministrator);

  return (
    <ConfirmDialog
      friction="simple"
      open
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      // Turning it off removes a way in: the question says who is left with one. Changing who keeps it is a save.
      destructive={turningOff}
      title={turningOff ? t("ssoAdmin.local.dialog.title") : t("ssoAdmin.local.dialog.titleChange")}
      description={turningOff ? t("ssoAdmin.local.dialog.description") : t("ssoAdmin.local.dialog.descriptionChange")}
      actionLabel={turningOff ? t("ssoAdmin.local.dialog.action") : t("common.words.save")}
      onConfirm={async () => {
        await save.mutateAsync();
      }}
    >
      <fieldset className="flex min-w-0 flex-col gap-2">
        <legend className="mb-1 text-13 font-medium text-fg">{t("ssoAdmin.local.dialog.keepLegend")}</legend>
        {accounts.isError ? (
          <ErrorBlock compact error={accounts.error} title={t("ssoAdmin.loadFailed")} onRetry={() => void accounts.refetch()} />
        ) : active.length === 0 ? (
          <p className="text-13 text-fg-muted">{accounts.isPending ? t("ssoAdmin.loading") : t("ssoAdmin.local.dialog.noAccounts")}</p>
        ) : (
          active.map((account) => (
            <Checkbox
              key={account.username}
              checked={kept.has(account.username)}
              onCheckedChange={(on) => {
                toggle(account.username, on);
              }}
              label={<Mono tone="default">{account.username}</Mono>}
              description={roleLabel(t, account.role)}
            />
          ))
        )}
        <p className="text-13 text-fg-muted">{t("ssoAdmin.local.dialog.keepHelp")}</p>
      </fieldset>
    </ConfirmDialog>
  );
}

/**
 * Whether passwords and passkeys still sign accounts in beside the identity provider, and which
 * accounts keep them as the way back. Turning it off is a decision with a recovery path, so the
 * question names who keeps a password; the access token and the command line are untouched.
 */
export function LocalSignInSection() {
  const t = useT();
  const queryClient = useQueryClient();
  const query = useQuery(localSignInQuery());
  const [dialog, setDialog] = useState<"off" | "change" | null>(null);
  const on = useMutation({
    mutationFn: () => setLocalSignIn({ enabled: true }),
    onSuccess: () => {
      toast.success(t("ssoAdmin.local.turnedOn"));
      void queryClient.invalidateQueries({ queryKey: ssoAdminKeys.localSignIn });
    },
  });
  const current = query.data;

  if (current === undefined) {
    return (
      <Card level={2} title={t("ssoAdmin.local.title")} icon={OBJECT_ICONS.passkey} description={t("ssoAdmin.local.description")}>
        {query.isError ? <ErrorBlock error={query.error} title={t("ssoAdmin.loadFailed")} onRetry={() => void query.refetch()} retrying={query.isRefetching} /> : null}
      </Card>
    );
  }
  // With no provider, turning it off would leave nobody able to sign in: the switch says so.
  const reason = current.enabled && current.providers === 0 ? t("ssoAdmin.local.needsProvider") : null;

  return (
    <>
      <SettingsGroup
        level={2}
        title={t("ssoAdmin.local.title")} icon={OBJECT_ICONS.passkey}
        description={t("ssoAdmin.local.description")}
        status={<StatusPill state={current.enabled ? "running" : "stopped"} label={current.enabled ? t("ssoAdmin.local.pill.on") : t("ssoAdmin.local.pill.off")} size="sm" />}
      >
        <Setting
          label={t("ssoAdmin.local.label")}
          help={t("ssoAdmin.local.help")}
          checked={current.enabled}
          busy={on.isPending}
          // Off is a decision with a way back: it asks who keeps a password, and the switch stays until it is answered.
          onCheckedChange={(next) => {
            if (next) on.mutate();
            else setDialog("off");
          }}
          {...(reason !== null ? { disabled: true as const, reason } : {})}
        />
        {!current.enabled ? (
          <>
            <div className="flex items-start justify-between gap-4 px-5 py-3">
              <div className="flex min-w-0 flex-col gap-1">
                <span className="text-13 font-medium text-fg">{t("ssoAdmin.local.keepers")}</span>
                <Names names={current.exempt} empty={t("ssoAdmin.local.noKeepers")} />
              </div>
              <Button size="sm" onClick={() => setDialog("change")}>
                {t("ssoAdmin.local.change")}
              </Button>
            </div>
            <div className="flex min-w-0 flex-col gap-1 px-5 py-3">
              <span className="text-13 font-medium text-fg">{t("ssoAdmin.local.survivors")}</span>
              <Names names={current.survivors} empty={t("ssoAdmin.local.noSurvivors")} />
            </div>
          </>
        ) : null}
      </SettingsGroup>
      {on.isError ? <ErrorBlock live compact error={on.error} title={t("ssoAdmin.local.dialog.failed")} /> : null}
      {dialog !== null ? <KeepersDialog current={current} turningOff={dialog === "off"} onClose={() => setDialog(null)} /> : null}
    </>
  );
}
