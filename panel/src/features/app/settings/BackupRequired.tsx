/**
 * Whether an update of the app is refused when its backup fails (`noust app backup-required`,
 * `GET/PUT /api/apps/{domain}/backup-required`). On, a backup that fails (a database dump
 * included) stops the update before anything changes, so a migration can always be undone; off,
 * the update goes on with a warning. With no choice made the default follows the databases: on
 * for an app that has one. Choosing either way, and returning to the default, ask "Confirm it's
 * you"; turning it off is asked once more.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../../api/client";
import { announce } from "../../../app/Announcer";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { Mono } from "../../../components/ui/Mono";
import { Skeleton } from "../../../components/ui/Skeleton";
import { Switch } from "../../../components/ui/Switch";
import { useT } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { reportActionError } from "../../apps/useAppActions";
import { backupRequiredQuery, olderServer, settingsKeys } from "./queries";
import type { BackupRequired } from "./queries";

/** The card: a switch, what the default is and why, and the way back to the default. */
export function BackupRequiredCard({ domain }: { domain: string }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const query = useQuery(backupRequiredQuery(domain));
  const [confirmOff, setConfirmOff] = useState(false);
  const change = useMutation({
    mutationFn: (required: boolean | null) => request("put", "/api/apps/{domain}/backup-required", { params: { domain }, body: { required } }),
    onSuccess: (result) => {
      queryClient.setQueryData<BackupRequired>(settingsKeys.backupRequired(domain), result);
      announce(result.explicit ? (result.required ? t("appSettings.backupRequired.announceOn", { domain }) : t("appSettings.backupRequired.announceOff", { domain })) : t("appSettings.backupRequired.announceDefault", { domain }));
    },
  });
  const title = t("appSettings.backupRequired.title");

  if (query.isPending) {
    return (
      <Card title={title}>
        <div aria-busy="true">
          <span className="sr-only">{t("appSettings.backupRequired.loading")}</span>
          <Skeleton className="h-10 w-full rounded-control" />
        </div>
      </Card>
    );
  }
  // A server older than 3.4 has no such setting: nothing to show rather than a guess.
  if (query.isError && olderServer(query.error)) return null;
  if (query.isError) {
    return (
      <Card title={title}>
        <ErrorBlock compact error={query.error} title={t("appSettings.backupRequired.readFailed")} onRetry={() => void query.refetch()} retrying={query.isRefetching} />
      </Card>
    );
  }
  const data = query.data;
  const required = change.isPending && typeof change.variables === "boolean" ? change.variables : data.required;
  const fail = (error: unknown) => reportActionError(t("appSettings.backupRequired.notSaved", { domain }), error);

  return (
    <Card title={title} description={t("appSettings.backupRequired.description")}>
      <div className="flex flex-col gap-3">
        <Switch
          label={t("appSettings.backupRequired.label")}
          checked={required}
          disabled={change.isPending}
          onCheckedChange={(next) => {
            if (!next) {
              setConfirmOff(true);
              return;
            }
            change.mutate(true, { onError: fail });
          }}
        />
        <p className="max-w-measure text-13 text-pretty text-fg-muted" data-testid="backup-required-note">
          {data.explicit
            ? t("appSettings.backupRequired.chosen")
            : data.default_required
              ? t.rich("appSettings.backupRequired.defaultOn", { databases: <Mono>{(data.databases ?? []).join(", ")}</Mono> })
              : t("appSettings.backupRequired.defaultOff")}
        </p>
        {!required ? <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("appSettings.backupRequired.offNote")}</p> : null}
        {data.explicit ? (
          <Button size="sm" variant="ghost" className="self-start" disabled={change.isPending} onClick={() => change.mutate(null, { onError: fail })}>
            {t("appSettings.backupRequired.useDefault")}
          </Button>
        ) : null}
      </div>
      <ConfirmDialog
        open={confirmOff}
        onOpenChange={setConfirmOff}
        friction="simple"
        server={node}
        title={t("appSettings.backupRequired.offTitle", { domain })}
        description={t("appSettings.backupRequired.offDescription")}
        actionLabel={t("appSettings.backupRequired.offAction")}
        onConfirm={async () => {
          await change.mutateAsync(false);
        }}
      />
    </Card>
  );
}
