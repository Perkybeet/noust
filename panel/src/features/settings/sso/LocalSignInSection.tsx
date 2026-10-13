import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { localSignInQuery, setLocalSignIn, ssoAdminKeys } from "../../../api/queries/ssoAdmin";
import type { LocalSignIn } from "../../../api/queries/ssoAdmin";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Section } from "../../../components/page/Section";
import { Subsection } from "../../../components/page/Subsection";
import { Button } from "../../../components/ui/Button";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Dialog } from "../../../components/ui/Dialog";
import { FeatureState } from "../../../components/ui/FeatureState";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Skeleton } from "../../../components/ui/Skeleton";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import { accountsQuery } from "../accounts/api";
import type { Account } from "../accounts/api";
import { roleLabel } from "../accounts/roles";

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
  const formId = useId();
  const accounts = useQuery(accountsQuery());
  const [kept, setKept] = useState<ReadonlySet<string>>(new Set(current.exempt));

  const save = useMutation({
    mutationFn: () => setLocalSignIn({ enabled: false, exempt: [...kept].sort() }),
    onSuccess: () => {
      toast.success(turningOff ? t("ssoAdmin.local.turnedOff") : t("ssoAdmin.local.changed"));
      void queryClient.invalidateQueries({ queryKey: ssoAdminKeys.localSignIn });
      onClose();
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
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !save.isPending) onClose();
      }}
      size="md"
      title={turningOff ? t("ssoAdmin.local.dialog.title") : t("ssoAdmin.local.dialog.titleChange")}
      description={turningOff ? t("ssoAdmin.local.dialog.description") : t("ssoAdmin.local.dialog.descriptionChange")}
      footer={
        <>
          <Button disabled={save.isPending} onClick={onClose}>
            {t("settings.shared.cancel")}
          </Button>
          <Button type="submit" form={formId} variant={turningOff ? "danger" : "primary"} loading={save.isPending}>
            {turningOff ? t("ssoAdmin.local.dialog.action") : t("ssoAdmin.local.dialog.save")}
          </Button>
        </>
      }
    >
      <form
        id={formId}
        noValidate
        className="flex flex-col gap-4"
        onSubmit={(event: SyntheticEvent<HTMLFormElement>) => {
          event.preventDefault();
          if (!save.isPending) save.mutate();
        }}
      >
        {save.isError ? <ErrorBlock live compact error={save.error} title={t("ssoAdmin.local.dialog.failed")} /> : null}
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
      </form>
    </Dialog>
  );
}

/**
 * Whether passwords and passkeys still sign accounts in beside the identity provider, and which
 * accounts keep them as the way back. Turning it off is a decision with a recovery path, so the
 * dialog names who keeps a password; the access token and the command line are untouched.
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

  return (
    <Section title={t("ssoAdmin.local.title")} description={t("ssoAdmin.local.description")} loading={current === undefined && !query.isError}>
      {query.isError && current === undefined ? (
        <ErrorBlock error={query.error} title={t("ssoAdmin.loadFailed")} onRetry={() => void query.refetch()} retrying={query.isRefetching} />
      ) : current === undefined ? (
        <Skeleton className="h-24 w-full rounded-card" />
      ) : (
        <>
          <FeatureState
            state={current.enabled ? "on" : "off"}
            title={current.enabled ? t("ssoAdmin.local.on") : t("ssoAdmin.local.off")}
            action={
              current.enabled ? (
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={current.providers === 0}
                  onClick={() => {
                    setDialog("off");
                  }}
                >
                  {t("ssoAdmin.local.turnOff")}
                </Button>
              ) : (
                <Button
                  size="sm"
                  variant="secondary"
                  loading={on.isPending}
                  onClick={() => {
                    on.mutate();
                  }}
                >
                  {t("ssoAdmin.local.turnOn")}
                </Button>
              )
            }
          >
            {current.enabled ? t("ssoAdmin.local.onBody") : t("ssoAdmin.local.offBody")}
          </FeatureState>
          {current.enabled && current.providers === 0 ? <Notice>{t("ssoAdmin.local.needsProvider")}</Notice> : null}
          {on.isError ? <ErrorBlock live compact error={on.error} title={t("ssoAdmin.local.dialog.failed")} /> : null}
          {!current.enabled ? (
            <div className="flex flex-col gap-6">
              <Subsection
                title={t("ssoAdmin.local.keepers")}
                actions={
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => {
                      setDialog("change");
                    }}
                  >
                    {t("ssoAdmin.local.change")}
                  </Button>
                }
              >
                <Names names={current.exempt} empty={t("ssoAdmin.local.noKeepers")} />
              </Subsection>
              <Subsection title={t("ssoAdmin.local.survivors")}>
                <Names names={current.survivors} empty={t("ssoAdmin.local.noSurvivors")} />
              </Subsection>
            </div>
          ) : null}
          {dialog !== null ? (
            <KeepersDialog
              current={current}
              turningOff={dialog === "off"}
              onClose={() => {
                setDialog(null);
              }}
            />
          ) : null}
        </>
      )}
    </Section>
  );
}
