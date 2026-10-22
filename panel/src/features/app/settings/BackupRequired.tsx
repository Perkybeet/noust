/**
 * Whether an update of the app is refused when its backup fails (`noust app backup-required`,
 * `GET/PUT /api/apps/{domain}/backup-required`). On, a backup that fails (a database dump
 * included) stops the update before anything changes, so a migration can always be undone; off,
 * the update goes on with a warning. With no choice made the default follows the databases: on
 * for an app that has one. Choosing either way, and returning to the default, ask "Confirm it's
 * you"; turning it off is asked once more.
 *
 * A row of Automation: the switch is the requirement, and what the default is and the way back to it
 * are said in its help.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../../api/client";
import { announce } from "../../../app/Announcer";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { Mono } from "../../../components/ui/Mono";
import { Setting } from "../../../components/ui/Setting";
import { textLinkClassName } from "../../../components/ui/TextLink";
import { useT } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { reportActionError } from "../../apps/useAppActions";
import { backupRequiredQuery, olderServer, settingsKeys } from "./queries";
import type { BackupRequired } from "./queries";
import { RowError, RowLoading, useAsksItsYou } from "./settingRows";

/** The row: a switch, what the default is and why, and the way back to the default. */
export function BackupRequiredRow({ domain }: { domain: string }) {
  const t = useT();
  const queryClient = useQueryClient();
  const { node } = useNode();
  const asksItsYou = useAsksItsYou();
  const [asking, setAsking] = useState(false);
  const query = useQuery(backupRequiredQuery(domain));
  const change = useMutation({
    mutationFn: (required: boolean | null) => request("put", "/api/apps/{domain}/backup-required", { params: { domain }, body: { required } }),
    onSuccess: (result) => {
      queryClient.setQueryData<BackupRequired>(settingsKeys.backupRequired(domain), result);
      announce(result.explicit ? (result.required ? t("appSettings.backupRequired.announceOn", { domain }) : t("appSettings.backupRequired.announceOff", { domain })) : t("appSettings.backupRequired.announceDefault", { domain }));
    },
  });

  if (query.isPending) return <RowLoading label={t("appSettings.backupRequired.loading")} rows={[3]} />;
  // A server older than 3.4 has no such setting: nothing to show rather than a guess.
  if (query.isError && olderServer(query.error)) return null;
  if (query.isError) return <RowError title={t("appSettings.backupRequired.readFailed")} error={query.error} onRetry={() => void query.refetch()} retrying={query.isRefetching} />;

  const data = query.data;
  const required = change.isPending && typeof change.variables === "boolean" ? change.variables : data.required;
  const fail = (error: unknown) => reportActionError(t("appSettings.backupRequired.notSaved", { domain }), error);
  const useDefault = (
    <button type="button" disabled={change.isPending} className={textLinkClassName("inline")} onClick={() => change.mutate(null, { onError: fail })}>
      {t("appSettings.backupRequired.useDefault")}
    </button>
  );

  return (
    <>
      <Setting
        label={t("appSettings.backupRequired.label")}
        help={
          <span data-testid="backup-required-note">
            {data.explicit
              ? t("appSettings.backupRequired.chosen")
              : data.default_required
                ? t.rich("appSettings.backupRequired.defaultOn", { databases: <Mono>{(data.databases ?? []).join(", ")}</Mono> })
                : t("appSettings.backupRequired.defaultOff")}
            {data.explicit ? <> {useDefault}</> : null}
          </span>
        }
        {...(required ? {} : { risk: t("appSettings.backupRequired.offRisk") })}
        elevated={asksItsYou}
        checked={required}
        busy={change.isPending}
        onCheckedChange={(next) => {
          // Turning off a safeguard is asked about first, naming the server on a fleet.
          if (next) change.mutate(true, { onError: fail });
          else setAsking(true);
        }}
      />
      <ConfirmDialog
        open={asking}
        onOpenChange={setAsking}
        friction="simple"
        server={node}
        title={t("appSettings.backupRequired.offTitle", { domain })}
        description={t("appSettings.backupRequired.offDescription")}
        actionLabel={t("appSettings.backupRequired.offAction")}
        onConfirm={async () => {
          await change.mutateAsync(false);
        }}
      />
    </>
  );
}
