/**
 * A change to how the server is reached (sshd, the firewall) that waits to be kept.
 *
 * Such a change undoes itself unless it is confirmed, so a mistake cannot lock the operator
 * out: a timer on the server reverts it when its window closes. Confirming needs a new SSH
 * login since the change, which proves the way in still works; a session that was already open
 * proves nothing, because reloading sshd or changing the firewall leaves open connections be.
 * The banner says that calmly, on every tab, with the time left and the two ways out.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../api/client";
import { CommandHint } from "../../components/page/CommandHint";
import { useNow } from "../../components/page/clock";
import { Button } from "../../components/ui/Button";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { useT } from "../../i18n";
import { useNode } from "../../nodes/useNode";
import { ServerErrorBlock, explainServerError } from "./errors";
import { serverKeys } from "./queries";
import type { PendingChange } from "./queries";

/** "1:42": the minutes and seconds left, never negative. */
export function countdown(expiresAt: number, now: number): string {
  const left = Math.max(0, Math.ceil(expiresAt - now / 1000));
  const minutes = Math.floor(left / 60);
  const seconds = left % 60;
  return `${String(minutes)}:${String(seconds).padStart(2, "0")}`;
}

function PendingChangeBanner({ change }: { change: PendingChange }) {
  const t = useT();
  const { node } = useNode();
  const queryClient = useQueryClient();
  const now = useNow(() => 1_000);
  const [revertOpen, setRevertOpen] = useState(false);
  const refresh = (): void => {
    void queryClient.invalidateQueries({ queryKey: serverKeys.changes });
    void queryClient.invalidateQueries({ queryKey: serverKeys.security });
  };
  const confirm = useMutation({
    mutationFn: () => request("post", "/api/server/security/changes/{change_id}/confirm", { params: { change_id: change.id } }),
    onSuccess: refresh,
  });
  const revert = async (): Promise<void> => {
    try {
      await request("post", "/api/server/security/changes/{change_id}/revert", { params: { change_id: change.id } });
    } catch (error: unknown) {
      throw explainServerError(t, error, node);
    }
    refresh();
  };
  const expired = now / 1000 >= change.expires_at;
  const firewall = change.kind.startsWith("firewall");
  return (
    <Notice
      variant="banner"
      tone="warning"
      title={
        expired
          ? t("server.pending.titleExpired")
          : firewall
            ? t("server.pending.titleFirewall", { left: countdown(change.expires_at, now) })
            : t("server.pending.titleSsh", { left: countdown(change.expires_at, now) })
      }
      action={
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" onClick={() => setRevertOpen(true)} disabled={confirm.isPending}>
            {t("server.pending.undo")}
          </Button>
          <Button size="sm" loading={confirm.isPending} onClick={() => confirm.mutate()}>
            {t("server.pending.confirm")}
          </Button>
        </div>
      }
    >
      <div className="flex min-w-0 flex-col gap-2">
        {/* The change's own name, as Noust recorded it. */}
        <p>{t.rich("server.pending.what", { change: <span className="font-medium">{change.title}</span> })}</p>
        <p>{t("server.pending.how")}</p>
        <CommandHint command={`noust server security confirm ${change.id}`} label={t("server.pending.fromTerminal")} />
        {confirm.isError ? <ServerErrorBlock live compact error={confirm.error} title={t("server.pending.confirmFailed")} /> : null}
        <p className="text-12 text-fg-muted">{t.rich("server.pending.id", { id: <Mono>{change.id}</Mono> })}</p>
      </div>
      <ConfirmDialog
        open={revertOpen}
        onOpenChange={setRevertOpen}
        friction="simple"
        server={node}
        title={t("server.pending.undoTitle")}
        description={t("server.pending.undoDescription")}
        actionLabel={t("server.pending.undoAction")}
        destructive={false}
        onConfirm={revert}
      />
    </Notice>
  );
}

/** Every change waiting for confirmation, one banner each (there is usually one). */
export function PendingChanges({ changes }: { changes: readonly PendingChange[] }) {
  return (
    <div className="flex min-w-0 flex-col gap-2">
      {changes.map((change) => (
        <PendingChangeBanner key={change.id} change={change} />
      ))}
    </div>
  );
}
