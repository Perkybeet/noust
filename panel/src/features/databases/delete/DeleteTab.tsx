import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useState } from "react";

import { request } from "../../../api/client";
import { databaseKeys, databaseOverviewQuery, databasesQuery } from "../../../api/queries/databases";
import { jobKeys } from "../../../api/queries/jobs";
import { GuardedButton, ReadOnlyLine } from "../../../components/page/permissions";
import { Checkbox } from "../../../components/ui/Checkbox";
import { Card } from "../../../components/ui/Card";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { ICONS } from "../../../components/ui/icons";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";
import { reportActionError } from "../../apps/useAppActions";
import { DatabasePage, useDatabaseFrame } from "../DatabasePage";
import { answers } from "../engineOf";
import { engineName } from "../engines";
import { useDatabaseJob } from "../jobs";

/**
 * What deleting a database takes, said before its button, and the choices that are the person's: whether a last
 * dump is taken first. What an application would lose is a fact, not a choice (the server refuses to drop a database an
 * application uses unless its address leaves that application's variables), so it is told, and done with the drop.
 * A database the engine no longer has is not deleted but forgotten, which stops it being backed up and listed.
 */
export function DeleteTab({ engine, name }: { engine: string; name: string }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const job = useDatabaseJob();
  const frame = useDatabaseFrame();
  const running = frame.life !== "stopped" && frame.life !== "changing";
  const overview = useQuery({ ...databaseOverviewQuery(engine, name), retry: false, enabled: answers(frame) });
  const row = useQuery({ ...databasesQuery(), select: (list) => list.databases.find((item) => item.engine === engine && item.name === name) });
  const [keepBackup, setKeepBackup] = useState(true);
  const [dropping, setDropping] = useState(false);
  const [forgetting, setForgetting] = useState(false);
  const data = overview.data;
  const database = data?.database ?? row.data;
  const links = data?.links ?? [];
  const apps = links.length > 0 ? links.map((link) => link.domain) : (database?.apps ?? []);
  const display = data?.display_name ?? engineName(engine);
  const missing = database?.missing === true;

  const forget = useMutation({
    mutationFn: () => request("post", "/api/databases/databases/{engine}/{name}/forget", { params: { engine, name } }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: databaseKeys.lists });
      toast.success(t("databases.layout.forgotten", { name }));
      void navigate({ to: "/databases" });
    },
    onError: (error) => reportActionError(t("databases.layout.forgetFailed", { name }), error),
  });

  const Delete = ICONS.delete;
  return (
    <DatabasePage title={missing ? t("databases.delete.forgetTitle") : t("databases.delete.title")}>
      <div className="flex flex-col gap-4">
        <ReadOnlyLine permission="databases.manage" />
        <Card tone="failed" icon={ICONS.delete} title={missing ? t("databases.delete.forgetWhat") : t("databases.delete.what")} level={2} description={missing ? t("databases.delete.forgetBody", { engine: display }) : t("databases.delete.body", { name })}>
          <div className="flex flex-col gap-3">
            {missing ? null : (
              <>
                {apps.length > 0 ? <p className="text-13 text-fg">{t("databases.delete.linked", { apps: apps.join(", ") })}</p> : null}
                <Checkbox checked={keepBackup} onCheckedChange={setKeepBackup} label={t("databases.delete.keepBackup")} description={keepBackup ? undefined : t("databases.delete.noBackup")} />
              </>
            )}
            <div>
              {missing ? (
                <GuardedButton permission="databases.manage" variant="danger" icon={<Delete aria-hidden="true" />} loading={forget.isPending} onClick={() => setForgetting(true)}>
                  {t("databases.layout.forget")}
                </GuardedButton>
              ) : (
                // design-exception: submit-disabled-invalid a job on this database is running, or its details are not read yet: nothing to ask, not a missing field
                <GuardedButton permission="databases.manage" variant="danger" icon={<Delete aria-hidden="true" />} disabled={job.busy || (running && data === undefined && !overview.isError)} onClick={() => setDropping(true)}>
                  {t("databases.delete.action")}
                </GuardedButton>
              )}
            </div>
          </div>
        </Card>
      </div>
      <ConfirmDialog
        friction="simple"
        open={forgetting}
        onOpenChange={setForgetting}
        title={t("databases.delete.forgetConfirmTitle", { name })}
        description={t("databases.delete.forgetConfirmBody")}
        actionLabel={t("databases.layout.forget")}
        server={node}
        onConfirm={async () => {
          await forget.mutateAsync();
        }}
      />
      <ConfirmDialog
        friction="type"
        open={dropping}
        onOpenChange={setDropping}
        title={t("databases.drop.title", { name })}
        description={`${apps.length > 0 ? t("databases.drop.descriptionLinked", { name, engine: display, apps: apps.join(", ") }) : t("databases.drop.description", { name, engine: display })} ${keepBackup ? t("databases.drop.backupKept") : t("databases.drop.backupSkipped")}`}
        actionLabel={t("databases.drop.action")}
        confirmText={name}
        server={node}
        onConfirm={async () => {
          const accepted = await request("delete", "/api/databases/databases/{engine}/{name}", {
            params: { engine, name },
            query: { force: false, keep_backup: keepBackup, unlink: apps.length > 0 },
          });
          job.track(accepted, "drop");
          void queryClient.invalidateQueries({ queryKey: jobKeys.active });
        }}
      />
    </DatabasePage>
  );
}
