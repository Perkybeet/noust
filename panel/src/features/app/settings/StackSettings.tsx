/**
 * A Docker Compose stack's own settings (spec 3.2, sections 1.8 and 8.4), as the commands have
 * them: whether an update copies the stack's databases first (`noust app backup-before-update`,
 * `PATCH .../backup-before-update`), and recording a worker a 1.x deploy gave a port as what it
 * is (`noust app headless`, `POST .../headless`). Both ask "Confirm it's you"; switching the copy
 * off and clearing the port are asked once more, and removing the site is an option that starts
 * unchecked, offered only when the command would offer it.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../../api/client";
import type { App } from "../../../api/queries/apps";
import { appKeys } from "../../../api/queries/apps";
import { announce } from "../../../app/Announcer";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { Checkbox } from "../../../components/ui/Checkbox";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { Skeleton } from "../../../components/ui/Skeleton";
import { Switch } from "../../../components/ui/Switch";
import { useT } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { reportActionError } from "../../apps/useAppActions";
import { headlessQuery, olderServer, settingsKeys } from "./queries";

/** Whether the app is a Compose stack, the only kind these settings are about. */
export function isComposeStack(app: Pick<App, "app_type">): boolean {
  return app.app_type === "docker-compose";
}

/** The copy of a stack's databases before each update: a switch, asked again to turn off. */
export function BackupBeforeUpdateCard({ app }: { app: App }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const domain = app.domain;
  const [confirmOff, setConfirmOff] = useState(false);
  const change = useMutation({
    mutationFn: (enabled: boolean) => request("patch", "/api/apps/{domain}/backup-before-update", { params: { domain }, body: { enabled } }),
    onSuccess: (result) => {
      queryClient.setQueryData<App>(appKeys.detail(domain), (known) => (known ? { ...known, backup_before_update: result.backup_before_update } : known));
      void queryClient.invalidateQueries({ queryKey: appKeys.detail(domain), exact: true });
      announce(result.backup_before_update ? t("appSettings.stack.copyOn", { domain }) : t("appSettings.stack.copyOff", { domain }));
    },
  });
  // A server older than 3.2 does not report the setting: nothing to show rather than a guess.
  if (!("backup_before_update" in app)) return null;
  const enabled = change.isPending ? change.variables : app.backup_before_update;
  return (
    <Card title={t("appSettings.stack.copyTitle")} description={t("appSettings.stack.copyDescription")}>
      <div className="flex flex-col gap-2">
        <Switch
          label={t("appSettings.stack.copyLabel")}
          checked={enabled}
          disabled={change.isPending}
          onCheckedChange={(next) => {
            if (!next) {
              setConfirmOff(true);
              return;
            }
            change.mutate(true, { onError: (error) => reportActionError(t("appSettings.stack.copyNotSaved", { domain }), error) });
          }}
        />
        {!app.backup_before_update ? <p className="max-w-measure text-13 text-pretty text-fg-muted">{t("appSettings.stack.copyOffNote")}</p> : null}
      </div>
      <ConfirmDialog
        open={confirmOff}
        onOpenChange={setConfirmOff}
        friction="simple"
        server={node}
        title={t("appSettings.stack.copyOffTitle", { domain })}
        description={t("appSettings.stack.copyOffDescription")}
        actionLabel={t("appSettings.stack.copyOffAction")}
        onConfirm={async () => {
          await change.mutateAsync(false);
        }}
      />
    </Card>
  );
}

/**
 * A worker that still has a port recorded, said and fixed: shown only when the stack publishes
 * no port and a port is recorded, which is when every health reader calls it down.
 */
export function WorkerCard({ app }: { app: App }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const domain = app.domain;
  const check = useQuery(headlessQuery(domain));
  const [open, setOpen] = useState(false);
  const [removeSite, setRemoveSite] = useState(false);
  const title = t("appSettings.stack.workerTitle");

  if (check.isPending) {
    return (
      <Card title={title}>
        <div aria-busy="true">
          <span className="sr-only">{t("appSettings.stack.workerLoading")}</span>
          <Skeleton className="h-10 w-full rounded-control" />
        </div>
      </Card>
    );
  }
  // A server older than 3.2 cannot tell, and cannot clear it from here.
  if (check.isError && olderServer(check.error)) return null;
  if (check.isError) {
    return (
      <Card title={title}>
        <ErrorBlock compact error={check.error} title={t("appSettings.stack.workerReadFailed")} onRetry={() => void check.refetch()} retrying={check.isRefetching} />
      </Card>
    );
  }
  const data = check.data;
  const port = data.recorded_port ?? null;
  if (!data.headless || port === null) return null;
  return (
    <Card title={title}>
      <div className="flex flex-col gap-3">
        <p className="max-w-measure text-13 text-pretty text-fg">{t("appSettings.stack.workerDescription", { port })}</p>
        <Button
          size="sm"
          className="self-start"
          onClick={() => {
            setRemoveSite(false);
            setOpen(true);
          }}
        >
          {t("appSettings.stack.workerAction")}
        </Button>
      </div>
      <ConfirmDialog
        open={open}
        onOpenChange={setOpen}
        friction="simple"
        destructive={removeSite}
        server={node}
        title={t("appSettings.stack.workerConfirmTitle", { domain })}
        description={t("appSettings.stack.workerConfirmDescription", { port })}
        actionLabel={t("appSettings.stack.workerConfirmAction")}
        onConfirm={async () => {
          await request("post", "/api/apps/{domain}/headless", { params: { domain }, body: { remove_site: data.site_retirable && removeSite } });
          void queryClient.invalidateQueries({ queryKey: settingsKeys.headless(domain) });
          void queryClient.invalidateQueries({ queryKey: appKeys.detail(domain), exact: true });
          announce(t("appSettings.stack.workerDone", { domain }));
        }}
      >
        {data.site_retirable ? (
          <Checkbox
            label={t("appSettings.stack.workerRemoveSite")}
            description={t("appSettings.stack.workerRemoveSiteDescription")}
            checked={removeSite}
            onCheckedChange={setRemoveSite}
          />
        ) : null}
      </ConfirmDialog>
    </Card>
  );
}
